"""Cheap, source-located PDF page inventory; it never creates financial facts."""

from __future__ import annotations

import re
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path

import fitz


FORMS = ("B01-DN", "B02-DN", "B03-DN")
REGIONS = ("FULL", "LEFT", "RIGHT", "TOP", "BOTTOM")
FORM_MARKER = re.compile(r"\b[b8]\s*0([1239])\s*[-–]?\s*dn(?:\s*/\s*hn)?\b")
HEADINGS = {
    "B01-DN": ("bang can doi ke toan",),
    "B02-DN": ("ket qua hoat dong kinh doanh",),
    "B03-DN": ("luu chuyen tien te",),
}
CORE_CODES = {
    "B01-DN": {100, 110, 120, 130, 140, 150, 200, 210, 220, 240, 250, 260, 270,
               300, 310, 320, 330, 338, 400, 410, 440},
    "B02-DN": {1, 2, 10, 11, 20, 21, 22, 25, 26, 30, 31, 32, 40, 50, 51, 52, 60, 61, 70, 71},
    "B03-DN": {1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 17, 20, 21, 22,
               23, 24, 25, 26, 27, 30, 31, 32, 33, 34, 35, 36, 40, 50, 60, 61, 70},
}


def fold(value: str) -> str:
    raw = unicodedata.normalize("NFKD", value.casefold().replace("đ", "d"))
    return "".join(char for char in raw if not unicodedata.combining(char))


def region_box(width: float, height: float, region: str) -> tuple[float, float, float, float]:
    if region == "LEFT":
        return (0, 0, width / 2, height)
    if region == "RIGHT":
        return (width / 2, 0, width, height)
    if region == "TOP":
        return (0, 0, width, height / 2)
    if region == "BOTTOM":
        return (0, height / 2, width, height)
    return (0, 0, width, height)


def _codes(words: list[tuple]) -> list[int]:
    result = []
    for word in words:
        token = str(word[4]).strip("().,:;-")
        if re.fullmatch(r"\d{2,3}", token):
            number = int(token)
            if 1 <= number <= 440:
                result.append(number)
    return sorted(set(result))


def _two_period_columns(text: str, words: list[tuple]) -> bool:
    normalized = fold(text)
    years = set(re.findall(r"\b20\d{2}\b", normalized))
    if len(years) >= 2:
        return True
    if ("so cuoi nam" in normalized and "so dau nam" in normalized) or (
        "nam nay" in normalized and "nam truoc" in normalized
    ):
        return True
    # Continuation pages may omit their year headings. Two aligned amount
    # columns are a weaker signal, used only beside a previously anchored form.
    buckets = Counter()
    code_positions = [float(w[0]) for w in words if re.fullmatch(r"\d{2,3}", str(w[4]))
                      and 1 <= int(str(w[4])) <= 440]
    code_left = min(code_positions) if code_positions else float("inf")
    for word in words:
        token = str(word[4]).strip("()")
        if (word[0] > code_left + 40 and
                re.fullmatch(r"-?\d{1,3}(?:[.,]\d{3}){1,5}|-?\d{3,15}", token)):
            buckets[round(float(word[0]) / 15) * 15] += 1
    return sum(count >= 2 for count in buckets.values()) >= 2


def _explicit_form(text: str) -> str | None:
    normalized = fold(text)
    markers = {match.group(1) for match in FORM_MARKER.finditer(normalized) if match.group(1) in "123"}
    if len(markers) > 1:
        return None
    if markers:
        return f"B0{next(iter(markers))}-DN"
    # A narrative paragraph can mention the report title hundreds of words
    # below the page header. Heading-only evidence must be near the top.
    heading_zone = normalized[:200]
    found = [form for form, phrases in HEADINGS.items() if any(p in heading_zone for p in phrases)]
    return found[0] if len(found) == 1 else None


def _note_page(text: str) -> bool:
    normalized = fold(text)
    return any(match.group(1) == "9" for match in FORM_MARKER.finditer(normalized)) or (
        "thuyet minh bao cao tai chinh" in normalized)


def _scope(text: str) -> str | None:
    normalized = fold(text)
    consolidated = "hop nhat" in normalized or bool(re.search(r"[b8]\s*0[123]\s*[-–]?\s*dn\s*/\s*hn", normalized))
    separate = "bao cao rieng" in normalized or "bctc rieng" in normalized
    return "CONSOLIDATED" if consolidated and not separate else "SEPARATE" if separate and not consolidated else None


def _unit(text: str) -> str | None:
    normalized = fold(text)
    for phrase, unit in (("ty dong", "tỷ đồng"), ("trieu dong", "triệu đồng"),
                         ("nghin dong", "nghìn đồng")):
        if phrase in normalized:
            return unit
    if re.search(r"\bvnd\b|don\s*vi(?:\s*tinh)?\s*[:.]?\s*dong", normalized):
        return "đồng"
    return None


def _phase(form: str, codes: list[int]) -> int:
    if not codes:
        return 0
    if form == "B01-DN":
        return 1 if max(codes) <= 299 else 2
    if form == "B02-DN":
        return 1 if max(codes) <= 30 else 2
    return 1 if max(codes) <= 20 else 2 if max(codes) <= 40 else 3


def _continuation(form: str, previous: dict, codes: list[int], two_periods: bool) -> bool:
    if not two_periods or len(codes) < 2 or previous.get("form") != form:
        return False
    old = previous.get("codes", [])
    if len(old) < 2:
        return False
    # Page numbers inside note tables overlap B02/B03 codes. Require forward
    # progression as well as the two-column layout and a nearby anchored page.
    return max(codes) > max(old) and min(codes) >= min(old) and _phase(form, codes) >= _phase(form, old)


def _region_words(words: list[tuple], box: tuple[float, float, float, float]) -> list[tuple]:
    left, top, right, bottom = box
    return [w for w in words if left <= (w[0] + w[2]) / 2 < right and top <= (w[1] + w[3]) / 2 < bottom]


def _image_table_score(page) -> int:
    """Cheap pixel projection; ranks candidates but never asserts a form."""
    pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY, alpha=False)
    data = memoryview(pix.samples)
    width, height = pix.width, pix.height
    zones = ((0.10, 0.50), (0.58, 0.76), (0.77, 0.94))
    score = 0
    for y in range(int(height * 0.08), int(height * 0.88), 3):
        row = data[y * width:(y + 1) * width]
        counts = [sum(value < 185 for value in row[int(width * a):int(width * b)]) for a, b in zones]
        score += counts[0] > 2 and counts[1] > 1 and counts[2] > 1
    return int(score)


def _select_light_ocr_pages(page_count: int, ranked: list[tuple[int, int]], max_pages: int) -> list[int]:
    """Reserve probes for distant high-score clusters as well as an early table run."""
    if max_pages <= 0 or not ranked or max(score for score, _ in ranked) < 10:
        return []
    early_limit = max(20, page_count // 3)
    first_plausible_page = 2 if page_count <= 40 else 7
    anchors = [number for score, number in ranked
               if first_plausible_page <= number <= early_limit and score >= 12]
    anchor = min(anchors) if anchors else max(ranked)[1]
    eligible = {number for _, number in ranked}
    selected = [number for number in range(anchor, min(anchor + 7, page_count + 1))
                if number in eligible][:max_pages]
    # A statement may recur after notes or be far from B01. Reserve four of
    # the remaining slots for up to two physically distant table clusters.
    distant = 0
    for score, number in sorted(ranked, key=lambda row: (-row[0], row[1])):
        if distant >= 2 or len(selected) >= max_pages or score < 12:
            break
        if number <= anchor + 7 or min((abs(number - old) for old in selected), default=page_count) < 5:
            continue
        selected.append(number)
        distant += 1
        if number + 1 in eligible and len(selected) < max_pages:
            selected.append(number + 1)
    for score, number in sorted(ranked, key=lambda row: (-row[0], row[1])):
        if number not in selected and len(selected) < max_pages:
            selected.append(number)
    return selected


def _light_ocr(document, ranked: list[tuple[int, int]], *, max_pages: int) -> dict[int, list[tuple]]:
    """One bounded worker reads top strips on likely tables; no full-page OCR."""
    if max_pages <= 0:
        return {}
    root = Path(__file__).resolve().parents[2]
    bundled = root / ".venv-m4b" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable = os.environ.get("M4B_DOCLING_PYTHON") or (str(bundled) if bundled.is_file() else None)
    worker = root / "scripts/prescan_ocr_probe.py"
    if not executable or not worker.is_file():
        return {}
    # A high-scoring table page is an anchor, not necessarily the only page
    # worth probing: the B01/B02/B03 sequence often spans several adjacent
    # scanned pages whose individual pixel scores are low. Probe the cluster
    # in physical order, then use spare budget for other high-score pages.
    selected = _select_light_ocr_pages(len(document), ranked, max_pages)
    if not selected:
        return {}
    with tempfile.TemporaryDirectory(prefix="pdf-prescan-ocr-") as temporary:
        folder = Path(temporary)
        manifest = []
        for page_number in selected:
            page = document[page_number - 1]
            target = folder / f"page-{page_number}.png"
            page.get_pixmap(matrix=fitz.Matrix(1, 1),
                            clip=fitz.Rect(0, 0, page.rect.width, page.rect.height * 0.30),
                            alpha=False).save(target)
            manifest.append({"page_number": page_number, "image": str(target)})
        source = folder / "manifest.json"
        output = folder / "ocr.json"
        source.write_text(json.dumps(manifest), encoding="utf-8")
        try:
            subprocess.run([executable, "-X", "utf8", str(worker), str(source), str(output)],
                           capture_output=True, timeout=40, check=True)
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass
        try:
            raw = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    result: dict[int, list[tuple]] = {}
    for key, lines in raw.items():
        words = []
        for line in lines:
            box = line["box"]
            left, right = min(p[0] for p in box), max(p[0] for p in box)
            top, bottom = min(p[1] for p in box), max(p[1] for p in box)
            tokens = str(line["text"]).split()
            length = max(sum(len(token) for token in tokens), 1)
            cursor = left
            for token in tokens:
                end = cursor + (right - left) * len(token) / length
                words.append((cursor, top, end, bottom, token))
                cursor = end
        result[int(key)] = words
    return result


def scan_pdf(path: Path, *, max_light_ocr_pages: int = 12) -> dict:
    """Inventory every page; bound OCR and expose unscanned image pages."""
    pages = []
    candidates = []
    previous: dict[str, dict] = {}
    with fitz.open(path) as document:
        page_words: dict[int, list[tuple]] = {}
        ranked = []
        for number, page in enumerate(document, start=1):
            words = page.get_text("words", sort=True)
            text = " ".join(str(word[4]) for word in words)
            low_text = len(text.strip()) < 200 and bool(page.get_images()) or len(text.strip()) < 35
            image_only = low_text and not words
            page_row = {"page_number": number, "width": round(page.rect.width, 2),
                        "height": round(page.rect.height, 2),
                        "route": "IMAGE_REVIEW" if image_only else "MIXED_LOW_TEXT" if low_text else "TEXT_LAYER",
                        "diagnostics": ["No usable text layer; choose page manually or use deep OCR"] if image_only
                        else ["Short text layer retained; thumbnail OCR is a supplementary probe"] if low_text else []}
            pages.append(page_row)
            if low_text:
                ranked.append((_image_table_score(page), number))
            if words:
                page_words[number] = words
        text_forms = set()
        for number, words in page_words.items():
            page = document[number - 1]
            for region in ("FULL", "LEFT", "RIGHT", "TOP", "BOTTOM"):
                subset = _region_words(words, region_box(page.rect.width, page.rect.height, region))
                text_forms.add(_explicit_form(" ".join(str(word[4]) for word in subset)))
        ocr_words = {} if set(FORMS).issubset(text_forms) else _light_ocr(document, ranked, max_pages=max_light_ocr_pages)
        for number, words in ocr_words.items():
            page_words[number] = page_words.get(number, []) + words
            pages[number - 1]["route"] = "MIXED_OCR_LIGHT" if pages[number - 1]["route"] == "MIXED_LOW_TEXT" else "IMAGE_OCR_LIGHT"
            pages[number - 1]["diagnostics"] = ["Top 30% thumbnail OCR only; verify candidate against source page"]
        for number, page in enumerate(document, start=1):
            words = page_words.get(number, [])
            if not words:
                continue
            text = " ".join(str(word[4]) for word in words)
            explicit_on_page = _explicit_form(text)
            is_note_page = _note_page(text)
            if is_note_page:
                pages[number - 1]["diagnostics"].append("B09/notes title detected; not joined to a B03 continuation")
            # Inspect halves as well as the full page. This makes 2-up pages
            # visible without triggering Docling or full-page OCR.
            proposals = []
            for region in REGIONS:
                box = region_box(page.rect.width, page.rect.height, region)
                subset = _region_words(words, box)
                local_text = " ".join(str(word[4]) for word in subset)
                form = _explicit_form(local_text)
                codes = _codes(subset)
                periods = _two_period_columns(local_text, subset)
                reason = "TITLE_OR_FORM" if form else None
                if form and _note_page(local_text):
                    form = reason = None
                if form:
                    core_count = len(CORE_CODES[form].intersection(codes))
                    marker = any(f"B0{match.group(1)}-DN" == form
                                 for match in FORM_MARKER.finditer(fold(local_text)))
                    if (core_count < 2 and not (marker and core_count >= 1)) or (not periods and core_count < 3):
                        form = reason = None
                if form is None and region == "FULL" and explicit_on_page is None and not is_note_page:
                    for candidate_form, old in previous.items():
                        if number - old["page_number"] <= 2 and _continuation(candidate_form, old, codes, periods):
                            form, reason = candidate_form, "CONTINUATION_CODE_SEQUENCE"
                            break
                if form and (region != "FULL" or explicit_on_page is None or form == explicit_on_page):
                    proposals.append({"form": form, "page_number": number, "region": region,
                                      "region_bbox": [round(x, 2) for x in box],
                                      "confidence": "HIGH" if reason == "TITLE_OR_FORM" and periods else "LOW",
                                      "reason": reason, "title_evidence": local_text[:180] if reason == "TITLE_OR_FORM" else None,
                                      "code_evidence": codes[:20], "codes": codes,
                                      "year_headers": sorted(set(re.findall(r"\b20\d{2}\b", local_text))),
                                      "two_period_columns": periods,
                                      "report_scope": _scope(local_text), "unit_label": _unit(local_text),
                                      "diagnostics": list(pages[number - 1]["diagnostics"])})
            sides = [p for p in proposals if p["region"] in ("LEFT", "RIGHT")]
            verticals = [p for p in proposals if p["region"] in ("TOP", "BOTTOM")]
            side_pair = {p["region"] for p in sides} == {"LEFT", "RIGHT"} and len({p["form"] for p in sides}) > 1
            vertical_pair = {p["region"] for p in verticals} == {"TOP", "BOTTOM"} and len({p["form"] for p in verticals}) > 1
            other_words = (_region_words(words, region_box(page.rect.width, page.rect.height,
                           "RIGHT" if sides[0]["region"] == "LEFT" else "LEFT")) if len(sides) == 1 else [])
            other_text = " ".join(str(word[4]) for word in other_words)
            other_is_same_table = bool(sides and len(CORE_CODES[sides[0]["form"]].intersection(_codes(other_words))) >= 3
                                       and not _note_page(other_text))
            single_side = (page.rect.width > page.rect.height and len(sides) == 1 and sides[0]["two_period_columns"]
                           and len(other_words) >= 30 and not other_is_same_table)
            if single_side:
                proposals = sides
            elif side_pair or vertical_pair:
                side_rank = sum(2 if p["confidence"] == "HIGH" else 1 for p in sides) if side_pair else -1
                vertical_rank = sum(2 if p["confidence"] == "HIGH" else 1 for p in verticals) if vertical_pair else -1
                # One page map cannot contain overlapping orthogonal halves.
                # Prefer the orientation with more corroborated form regions.
                proposals = sides if side_rank >= vertical_rank else verticals
            elif any(p["region"] == "FULL" for p in proposals):
                proposals = [p for p in proposals if p["region"] == "FULL"]
            else:
                proposals = max(([p] for p in proposals), key=lambda group: group[0]["confidence"] == "HIGH", default=[])
            for proposal in proposals:
                candidates.append(proposal)
                previous[proposal["form"]] = proposal
    # OCR may identify the first visible page of a statement only after its
    # opening page. A clearly marked "continued" heading makes the previous
    # physical page worth review, but does not justify treating it as parsed.
    for form in FORMS:
        first = next((item for item in candidates if item["form"] == form), None)
        if not first or first["page_number"] <= 1 or "tiep theo" not in fold(first.get("title_evidence") or ""):
            continue
        preceding = first["page_number"] - 1
        if any(item["page_number"] == preceding and item["form"] == form for item in candidates):
            continue
        page = pages[preceding - 1]
        if page["route"] not in {"IMAGE_REVIEW", "IMAGE_OCR_LIGHT", "MIXED_LOW_TEXT", "MIXED_OCR_LIGHT"}:
            continue
        candidates.append({"form": form, "page_number": preceding, "region": "FULL",
                           "region_bbox": [0, 0, page["width"], page["height"]],
                           "confidence": "LOW", "reason": "PRECEDING_PAGE_REVIEW",
                           "title_evidence": None, "code_evidence": [], "codes": [], "year_headers": [],
                           "two_period_columns": False, "report_scope": None, "unit_label": None,
                           "diagnostics": ["Later page is marked continued; inspect this preceding image manually"]})
    if not any(item["form"] == "B03-DN" and not item["reason"].endswith("_REVIEW") for item in candidates):
        b02 = [item for item in candidates if item["form"] == "B02-DN" and not item["reason"].endswith("_REVIEW")]
        if b02:
            last = max(item["page_number"] for item in b02)
            for number in (last + 1, last + 2):
                if number > len(pages) or any(item["page_number"] == number for item in candidates):
                    continue
                page = pages[number - 1]
                if page["route"] not in {"IMAGE_REVIEW", "IMAGE_OCR_LIGHT", "MIXED_LOW_TEXT", "MIXED_OCR_LIGHT"}:
                    continue
                candidates.append({"form": "B03-DN", "page_number": number, "region": "FULL",
                                   "region_bbox": [0, 0, page["width"], page["height"]],
                                   "confidence": "LOW", "reason": "FOLLOWING_PAGE_REVIEW",
                                   "title_evidence": None, "code_evidence": [], "codes": [], "year_headers": [],
                                   "two_period_columns": False, "report_scope": None, "unit_label": None,
                                   "diagnostics": ["B03 was not detected; inspect pages following B02 manually"]})
    if not any(item["form"] == "B02-DN" and not item["reason"].endswith("_REVIEW") for item in candidates):
        b01 = [item["page_number"] for item in candidates if item["form"] == "B01-DN" and not item["reason"].endswith("_REVIEW")]
        b03 = [item["page_number"] for item in candidates if item["form"] == "B03-DN" and not item["reason"].endswith("_REVIEW")]
        if b01 and b03 and 0 < min(b03) - max(b01) <= 3:
            for number in range(max(b01) + 1, min(b03)):
                if any(item["page_number"] == number for item in candidates):
                    continue
                page = pages[number - 1]
                if page["route"] not in {"IMAGE_REVIEW", "IMAGE_OCR_LIGHT", "MIXED_LOW_TEXT", "MIXED_OCR_LIGHT"}:
                    continue
                candidates.append({"form": "B02-DN", "page_number": number, "region": "FULL",
                                   "region_bbox": [0, 0, page["width"], page["height"]],
                                   "confidence": "LOW", "reason": "BETWEEN_STATEMENTS_REVIEW",
                                   "title_evidence": None, "code_evidence": [], "codes": [], "year_headers": [],
                                   "two_period_columns": False, "report_scope": None, "unit_label": None,
                                   "diagnostics": ["B02 not detected between B01 and B03; possible rotated scan, inspect manually"]})
    candidates.sort(key=lambda item: (item["page_number"], item["region"], item["form"]))
    found = {candidate["form"] for candidate in candidates if not candidate["reason"].endswith("_REVIEW")}
    return {"page_count": len(pages), "pages": pages, "candidates": candidates,
            "missing_forms": [form for form in FORMS if form not in found]}
