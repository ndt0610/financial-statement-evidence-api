"""PDF extraction types for the M4b pilot.

An automatically extracted PDF/OCR value stays EXTRACTED until a separate
source review is performed. A parser result is not an attestation of accuracy.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.models import DocumentContext, Evidence, FinancialFact, LineItemCode, Period, ReportScope
from app.models.financial_fact import PDFLocator
from app.services.vas_normalizer import fold, parse_accounting_number, shift_year
from app.adapters.spatial_ocr import recover_spatial_rows


TARGET_CODES = {
    "B01-DN": {"100", "110", "130", "131", "136", "140", "216", "270", "300", "310", "400"},
    "B02-DN": {"10", "60"},
    "B03-DN": {"20", "70"},
}
EXTENDED_TARGET_CODES = {
    "B01-DN": TARGET_CODES["B01-DN"] | {"120","123","137","141","149","150","200","210","220","230","240","250","260","311","320","330","338","410","429","430","440"},
    "B02-DN": TARGET_CODES["B02-DN"] | {"01","02","11","20","21","22","23","24","25","26","30","31","32","40","50","51","52","61"},
    "B03-DN": TARGET_CODES["B03-DN"] | {"01","02","03","04","05","06","08","09","10","11","12","13","14","15","16","17",
                                            "21","22","23","24","25","26","27","30","31","32","33","34","35","36","40","50","60","61"},
}


@dataclass(frozen=True)
class PdfDocumentSpec:
    company_name: str
    company_id: str
    document_hash: str
    source_path: str
    report_scope: ReportScope
    accounting_regime: str
    fiscal_start: date
    fiscal_end: date
    prepared_on: date | None = None


@dataclass(frozen=True)
class PdfIngestionResult:
    document_hash: str
    facts: list[FinancialFact]
    headers: dict = field(default_factory=dict)
    notes: dict[str, Evidence] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
    review_history: tuple[dict, ...] = ()


def _page_form(text: str) -> str | None:
    normalized = fold(text)
    match = re.search(r"\bb\s*0([123])\s*[-–]\s*dn\b", normalized)
    if match:
        return f"B0{match.group(1)}-DN"
    for phrase, form in (("bang can doi ke toan", "B01-DN"), ("ket qua hoat dong kinh doanh", "B02-DN"), ("bao cao luu chuyen tien te", "B03-DN")):
        if phrase in normalized:
            return form
    return None


def _page_unit(text: str) -> tuple[str, Decimal] | None:
    normalized = fold(text)
    if "trieu dong" in normalized:
        return "triệu đồng", Decimal("1000000")
    if "nghin dong" in normalized:
        return "nghìn đồng", Decimal("1000")
    if re.search(r"don\s*vi(?:\s*tinh)?\s*[:.]?\s*(?:vnd|dong)\b", normalized):
        return "đồng", Decimal("1")
    # OCR may read the unit-label letters incorrectly; never resolve VND if USD
    # also appears on the same page.
    if re.search(r"\bvnd\b", normalized) and not re.search(r"\busd\b", normalized):
        return "đồng", Decimal("1")
    return None


def _page_scope(text: str) -> ReportScope | None:
    """Use an explicit DN/HN form marker when OCR damages the scope heading."""
    normalized = fold(text)
    markers = set()
    if "hop nhat" in normalized or re.search(r"\bb\s*0[123]\s*[-–]\s*dn\s*/\s*hn\b", normalized):
        markers.add(ReportScope.CONSOLIDATED)
    if "rieng" in normalized:
        markers.add(ReportScope.SEPARATE)
    return next(iter(markers)) if len(markers) == 1 else None


def _column_indices(cells: list[dict], *, page_texts: list[str], form: str, ocr: bool, fiscal_year: int,
                    fiscal_start: date | None = None, fiscal_end: date | None = None) -> tuple[int, int, int, int | None] | None:
    code_col = current_col = prior_col = note_col = None
    split_code_col = None
    header_has_ma = False
    year_columns: dict[str, set[int]] = {}
    date_columns: dict[str, set[int]] = {}
    for cell in cells:
        if cell.get("start_row_offset_idx", 999) > 3:
            continue
        text = " ".join(fold(str(cell.get("text", ""))).split())
        simplified = re.sub(r"[^a-z0-9 ]", "", text)
        column = cell.get("start_col_offset_idx")
        date_match = re.match(r"^(\d{1,2}/\d{1,2}/\d{4})(?:\s|$)", text)
        if date_match and isinstance(column, int):
            day, month, year = (int(part) for part in date_match.group(1).split("/"))
            try:
                key = date(year, month, day).strftime("%d/%m/%Y")
            except ValueError:
                key = "INVALID"
            date_columns.setdefault(key, set()).add(column)
        header_has_ma = header_has_ma or bool(re.search(r"\bma\b", simplified))
        if text in {"ma so", "ma\nso"} or (ocr and text in {"ma s", "mas"}):
            code_col = column
        elif text == "so":
            split_code_col = column
        elif "so cuoi nam" in text or text == "nam nay" or (ocr and re.fullmatch(r"s(?:o)? cu(?:o)?i nam", simplified)):
            current_col = column
        elif "so dau nam" in text or text == "nam truoc" or (ocr and (re.fullmatch(r"s(?:o)? d(?:a)?u nam", simplified) or simplified == "nam truc")):
            prior_col = column
        elif text == "thuyet minh":
            note_col = column
        elif re.fullmatch(r"(?:nam\s+)?(?:19|20)\d{2}(?:\s*\(?\s*(?:vnd|dong)\s*\)?)?", text) and isinstance(column, int):
            year_columns.setdefault(re.search(r"(?:19|20)\d{2}", text).group(), set()).add(column)
    if current_col is None and len(year_columns.get(str(fiscal_year), set())) == 1:
        current_col = next(iter(year_columns[str(fiscal_year)]))
    if prior_col is None and len(year_columns.get(str(fiscal_year - 1), set())) == 1:
        prior_col = next(iter(year_columns[str(fiscal_year - 1)]))
    if form == "B01-DN" and fiscal_start and fiscal_end:
        end_key, start_key = fiscal_end.strftime("%d/%m/%Y"), fiscal_start.strftime("%d/%m/%Y")
        if current_col is None and len(date_columns.get(end_key, set())) == 1:
            current_col = next(iter(date_columns[end_key]))
        if prior_col is None and len(date_columns.get(start_key, set())) == 1:
            prior_col = next(iter(date_columns[start_key]))
    if code_col is None and (header_has_ma or any(fold(text.strip()) == "ma" for text in page_texts)):
        if split_code_col is not None and current_col is not None and split_code_col < current_col:
            code_col = split_code_col
    if code_col is None and header_has_ma and current_col is not None and prior_col is not None:
        # Scanned headings often read "Mã/số" as "Mā/9s". Infer only when one
        # column contains multiple exact TT200 codes, away from value columns.
        counts: dict[int, set[str]] = {}
        known = EXTENDED_TARGET_CODES.get(form, set())
        for cell in cells:
            col = cell.get("start_col_offset_idx")
            token = str(cell.get("text", "")).strip()
            if isinstance(col, int) and col < min(current_col, prior_col) and token in known:
                counts.setdefault(col, set()).add(token)
        matches = [col for col, codes in counts.items() if len(codes) >= 2]
        if len(matches) == 1:
            code_col = matches[0]
    if code_col is None or current_col is None or prior_col is None or current_col == prior_col:
        return None
    return code_col, current_col, prior_col, note_col


def _period(spec: PdfDocumentSpec, form: str, code: str, current: bool) -> Period:
    end = spec.fiscal_end if current else shift_year(spec.fiscal_end, -1)
    year = end.year
    if form == "B01-DN":
        return Period(period_type="INSTANT", as_of_date=spec.fiscal_end if current else spec.fiscal_start, fiscal_year_label=f"FY{year}")
    if form == "B03-DN" and code == "70":
        return Period(period_type="INSTANT", as_of_date=end, fiscal_year_label=f"FY{year}")
    start = spec.fiscal_start if current else shift_year(spec.fiscal_start, -1)
    if form == "B03-DN" and code == "60":
        return Period(period_type="INSTANT", as_of_date=start, fiscal_year_label=f"FY{year}")
    return Period(period_type="DURATION", period_start=start, period_end=end, fiscal_year_label=f"FY{year}")


def _unambiguous_pdf_number(raw: str) -> bool:
    """Reject malformed or merged grouped figures before number normalization."""
    text = raw.strip()
    if text in {"", "-", "–", "—"}:
        return True
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    elif text.startswith("-"):
        text = text[1:]
    if not text or any(char.isspace() for char in text):
        return False
    if re.fullmatch(r"\d+", text):
        return True
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", text):
        return True
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", text):
        return True
    return bool(re.fullmatch(r"\d+[.,]\d{1,2}", text))


def _split_merged_rows(cells: list[dict], code_col: int, current_col: int, prior_col: int) -> list[dict]:
    """Split only equal-length, unique, strictly ordered code/value streams.

    Result retains the original group bbox, not a fabricated per-value box.
    Equal lengths do not prove alignment, therefore facts remain EXTRACTED.
    """
    grouped: dict[int, dict[int, dict]] = {}
    for cell in cells:
        row, col = cell.get("start_row_offset_idx"), cell.get("start_col_offset_idx")
        if isinstance(row, int) and isinstance(col, int):
            grouped.setdefault(row, {})[col] = cell
    expanded = list(cells)
    for row, columns in grouped.items():
        raw_codes = str(columns.get(code_col, {}).get("text", "")).split()
        if len(raw_codes) < 2 or not all(re.fullmatch(r"\d{2,3}", code) for code in raw_codes):
            continue
        if len(set(raw_codes)) != len(raw_codes) or any(int(a) >= int(b) for a, b in zip(raw_codes, raw_codes[1:])):
            continue
        label_col = next((col for col in sorted(columns) if col != code_col and col < min(current_col, prior_col)
                          and col != code_col and (col < code_col or col == code_col + 1)), None)
        if label_col is None:
            continue
        labels = [line.strip() for line in str(columns[label_col].get("text", "")).splitlines() if line.strip()]
        if len(labels) != len(raw_codes):
            continue
        current = str(columns.get(current_col, {}).get("text", "")).split()
        prior = str(columns.get(prior_col, {}).get("text", "")).split()
        if len(current) != len(raw_codes) or len(prior) != len(raw_codes) or not all(
                _unambiguous_pdf_number(value) and value not in {"-", "–", "—"} for value in current + prior):
            continue
        expanded = [item for item in expanded if item.get("start_row_offset_idx") != row]
        for offset, (code, value, previous, label) in enumerate(zip(raw_codes, current, prior, labels), 1):
            # Synthetic negative row IDs cannot collide with normal Docling offsets.
            new_row = -(row * 1000 + offset)
            for col, text in ((code_col, code), (label_col, label), (current_col, value), (prior_col, previous)):
                expanded.append({**columns[col], "start_row_offset_idx": new_row, "text": text,
                                 "_merged_group": True})
    return expanded


def _align_misindexed_values(cells: list[dict], code_col: int, current_col: int, prior_col: int) -> list[dict]:
    """Repair missing cells only when both year values uniquely overlap a single code box."""
    by_row: dict[int, dict[int, dict]] = {}
    for cell in cells:
        row, col = cell.get('start_row_offset_idx'), cell.get('start_col_offset_idx')
        if isinstance(row, int) and isinstance(col, int):
            by_row.setdefault(row, {})[col] = cell

    def overlap(a: dict, b: dict) -> bool:
        a, b = a.get('bbox') or {}, b.get('bbox') or {}
        if not all(k in a and k in b for k in ('t', 'b', 'coord_origin')) or a['coord_origin'] != b['coord_origin']:
            return False
        alo, ahi = sorted((a['t'], a['b'])); blo, bhi = sorted((b['t'], b['b']))
        height = min(ahi - alo, bhi - blo)
        return height > 0 and max(0, min(ahi, bhi) - max(alo, blo)) / height >= .8

    codes = [c for c in cells if c.get('start_col_offset_idx') == code_col
             and re.fullmatch(r'\d{2,3}', str(c.get('text', '')).strip())]
    additions = []
    for code in codes:
        row = code['start_row_offset_idx']
        if current_col in by_row[row] or prior_col in by_row[row]:
            continue
        found = []
        for col in (current_col, prior_col):
            candidates = [c for c in cells if c.get('start_col_offset_idx') == col
                          and _unambiguous_pdf_number(str(c.get('text', '')).strip())
                          and str(c.get('text', '')).strip() not in {'', '-', '–', '—'}
                          and overlap(code, c)
                          and sum(overlap(other, c) for other in codes) == 1]
            if len(candidates) != 1:
                break
            found.append(candidates[0])
        if len(found) == 2:
            additions.extend({**c, 'start_row_offset_idx': row, '_spatial_alignment': True} for c in found)
    return cells + additions


def _split_cross_year_cells(cells: list[dict], code_col: int, current_col: int, prior_col: int) -> list[dict]:
    """Split two amounts only if one OCR box spans both dated columns."""
    if current_col >= prior_col:
        return cells
    headers = {c.get('start_col_offset_idx'): c.get('bbox', {}) for c in cells
               if c.get('start_row_offset_idx') == 0 and c.get('start_col_offset_idx') in (current_col, prior_col)}
    if len(headers) != 2 or any(b.get('coord_origin') != 'TOPLEFT' for b in headers.values()):
        return cells
    centers = [(headers[col]['l'] + headers[col]['r']) / 2 for col in (current_col, prior_col)]
    rows = {}
    for cell in cells:
        rows.setdefault(cell.get('start_row_offset_idx'), {}).setdefault(cell.get('start_col_offset_idx'), []).append(cell)
    replaced = []
    pattern = r'(\(?-?\d{1,3}(?:\.\d{3})+\)?)\s*(\(?-?\d{1,3}(?:\.\d{3})+\)?)'
    for row, cols in rows.items():
        if not isinstance(row, int) or row <= 0 or len(cols.get(code_col, [])) != 1:
            continue
        values = cols.get(current_col, []) + cols.get(prior_col, [])
        if len(values) != 1: continue
        cell = values[0]; bbox = cell.get('bbox', {}); raw = str(cell.get('text', '')).strip()
        match = re.fullmatch(pattern, raw)
        if (not match or not all(_unambiguous_pdf_number(x) for x in match.groups())
                or bbox.get('coord_origin') != 'TOPLEFT'
                or not bbox.get('l', float('inf')) < centers[0] < centers[1] < bbox.get('r', float('-inf'))):
            continue
        replaced.append((cell, [{**cell, 'start_col_offset_idx': col, 'text': value,
                                  '_cross_year_source': raw} for col, value in zip((current_col, prior_col), match.groups())]))
    for original, pair in replaced:
        cells = [c for c in cells if c is not original] + pair
    return cells


def _split_vertical_overlap(cells: list[dict], code_col: int, current_col: int, prior_col: int) -> list[dict]:
    """Recover the second value only when two adjacent coded rows share both boxes."""
    rows={}
    for cell in cells:
        row,col=cell.get('start_row_offset_idx'),cell.get('start_col_offset_idx')
        if isinstance(row,int) and isinstance(col,int):rows.setdefault(row,{})[col]=cell
    result=list(cells)
    for row,cols in rows.items():
        prior=rows.get(row-1,{})
        code,previous=cols.get(code_col,{}),prior.get(code_col,{})
        if not re.fullmatch(r'\d{2,3}',str(code.get('text','')).strip()):continue
        if not re.fullmatch(r'\d{2,3}[a-z]?',str(previous.get('text','')).strip(),re.I):continue
        if current_col in prior or prior_col in prior:continue
        if code.get('bbox',{}).get('coord_origin')!='TOPLEFT' or previous.get('bbox',{}).get('coord_origin')!='TOPLEFT':continue
        old=previous['bbox'];new=code['bbox'];pairs=[]
        for col in (current_col,prior_col):
            cell=cols.get(col,{});box=cell.get('bbox',{});parts=str(cell.get('text','')).split()
            if (len(parts)!=2 or not all(_unambiguous_pdf_number(part) for part in parts)
                    or box.get('coord_origin')!='TOPLEFT' or not (box.get('t',99999)<=old['b'] and box.get('b',-1)>=new['t'])
                    or box.get('b',0)-box.get('t',0) < (new['b']-new['t'])*1.5):break
            pairs.append((cell,parts[1]))
        if len(pairs)==2:
            for cell,value in pairs:
                result=[{**c,'text':value,'_vertical_source':str(cell['text'])} if c is cell else c for c in result]
    return result


def _table_parts(table: dict) -> list[dict]:
    cells = table.get("data", {}).get("table_cells", [])
    code_cols = sorted({c.get("start_col_offset_idx") for c in cells if c.get("start_row_offset_idx", 999) <= 3
                        and re.sub(r"[^a-z]", "", fold(str(c.get("text", "")))) in {"maso", "mas"}
                        and isinstance(c.get("start_col_offset_idx"), int)})
    num_cols = table.get("data", {}).get("num_cols", 0)
    if num_cols < 7 or len(code_cols) != 2 or code_cols[0] >= num_cols // 2 or code_cols[1] < num_cols // 2:
        return [table]
    boundary = code_cols[1]
    parts = []
    for lo, hi in ((0, boundary), (boundary, num_cols)):
        subset = [c for c in cells if lo <= c.get("start_col_offset_idx", -1) < hi]
        bbox_cells = [c.get("bbox") for c in subset if c.get("bbox")]
        if not bbox_cells:
            return [table]  # geometry is required to associate each slice with its title
        bounds = {**bbox_cells[0], "l": min(b["l"] for b in bbox_cells), "r": max(b["r"] for b in bbox_cells)}
        parts.append({**table, "data": {**table["data"], "table_cells": subset}, "_horizontal_bounds": bounds})
    return parts


def _local_context(items: list[dict], page: int, table: dict) -> list[str]:
    prov = table.get("prov", [])
    if not prov or not prov[0].get("bbox"):
        return [str(x.get("text", "")) for x in items if any(p.get("page_no") == page for p in x.get("prov", []))]
    box = table.get("_horizontal_bounds") or prov[0]["bbox"]
    vertical = prov[0]["bbox"]
    selected = []
    for item in items:
        for source in item.get("prov", []):
            b = source.get("bbox")
            if source.get("page_no") != page or not b or b.get("coord_origin") != vertical.get("coord_origin"):
                continue
            overlap = min(box["r"], b["r"]) - max(box["l"], b["l"])
            # Include headings that overlap the top border due to OCR box noise.
            above = b["t"] <= vertical["t"] if b["coord_origin"] == "TOPLEFT" else b["t"] >= vertical["t"]
            if overlap > 0 and above:
                selected.append(str(item.get("text", "")))
    return selected


def extract_docling_tables(payload: dict, spec: PdfDocumentSpec, *, extended_codes: bool = False,
                           confirmed_regions: dict[int, list[dict]] | None = None) -> PdfIngestionResult:
    """Extract only explicitly mapped health-review lines from Docling tables.

    Caller supplies document-level identity, accounting regime and fiscal
    period. Each page must independently expose form, scope, unit and year.
    Row/column structure is taken from Docling, not from benchmark labels.
    All output remains EXTRACTED, including native PDF text.
    """
    if "document" not in payload:
        return PdfIngestionResult(spec.document_hash, [], diagnostics=("DOCLING_FAILED",))
    document = payload["document"]
    page_texts: dict[int, list[str]] = {}
    for item in document.get("texts", []):
        for provenance in item.get("prov", []):
            page_texts.setdefault(provenance["page_no"], []).append(str(item.get("text", "")))
    facts: list[FinancialFact] = []
    diagnostics: list[str] = []
    validated_pages: dict[int, str] = {}
    previous_columns: dict[str, tuple[int, tuple[int, int, int, int | None]]] = {}
    if payload.get('spatial_ocr_error'):
        diagnostics.append('Spatial OCR unavailable: ' + str(payload['spatial_ocr_error']))
    if payload.get('refined_code_error'):
        diagnostics.append('Code refinement unavailable: ' + str(payload['refined_code_error']))
    diagnostics.extend('Rotation retry: '+str(warning) for warning in payload.get('rotation_warnings',[]))
    if str(payload.get("status", "")).split(".")[-1] != "SUCCESS" or payload.get("errors"):
        diagnostics.append(f"Docling conversion status: {payload.get('status', 'unknown')}")
    parts = [(number, part) for number, table in enumerate(document.get("tables", [])) for part in _table_parts(table)]
    for table_number, table in parts:
        provenance = table.get("prov", [])
        if len(provenance) != 1:
            diagnostics.append(f"table {table_number}: ambiguous page")
            continue
        page = provenance[0]["page_no"]
        confirmed = None
        if confirmed_regions is not None:
            selected = confirmed_regions.get(page, [])
            bounds = table.get("_horizontal_bounds") or provenance[0].get("bbox")
            if not bounds:
                boxes = [cell.get("bbox", {}) for cell in table.get("data", {}).get("table_cells", [])]
                boxes = [box for box in boxes if all(key in box for key in ("l", "t", "r", "b"))]
                if boxes:
                    bounds = {"l": min(box["l"] for box in boxes), "t": min(box["t"] for box in boxes),
                              "r": max(box["r"] for box in boxes), "b": max(box["b"] for box in boxes),
                              "coord_origin": boxes[0].get("coord_origin", "TOPLEFT")}
            if len(selected) == 1 and selected[0]["region"] == "FULL":
                confirmed = selected[0]
            elif bounds:
                x = (bounds["l"] + bounds["r"]) / 2
                y = (bounds["t"] + bounds["b"]) / 2
                if bounds.get("coord_origin") == "BOTTOMLEFT":
                    y = selected[0].get("page_height", 0) - y if selected else y
                matches = [item for item in selected if item["region_bbox"][0] <= x <= item["region_bbox"][2]
                           and item["region_bbox"][1] <= y <= item["region_bbox"][3]]
                if len(matches) == 1:
                    confirmed = matches[0]
            if confirmed is None:
                diagnostics.append(f"page {page}: table outside confirmed regions or region ambiguous")
                continue
        local_text = _local_context(document.get("texts", []), page, table)
        context = " ".join(local_text)
        normalized = fold(context)
        form = _page_form(context)
        if confirmed and form is not None and form != confirmed["form_code"]:
            diagnostics.append(f"page {page}: observed form conflicts with confirmed form")
            continue
        if form is None and confirmed:
            form = confirmed["form_code"]
        cells = table.get("data", {}).get("table_cells", [])
        header_text = " ".join(str(cell.get("text", "")) for cell in cells if cell.get("start_row_offset_idx", 999) <= 3)
        unit = _page_unit(context + " " + header_text)
        observed_scope = _page_scope(context)
        if confirmed and unit is None:
            unit = (confirmed.get("unit_label", "đồng"), Decimal(str(confirmed.get("unit_multiplier", "1"))))
        if confirmed and observed_scope is None:
            observed_scope = spec.report_scope
        header_text = " ".join(str(cell.get("text", "")) for cell in cells if cell.get("start_row_offset_idx", 999) <= 3)
        year_present = str(spec.fiscal_end.year) in normalized + " " + header_text
        continuation = bool(re.search(r'\b(?:tiep|tip)\s+theo\b', normalized))
        explicit_years = set(re.findall(r'\b20\d{2}\b', normalized + ' ' + header_text))
        inherited_year = continuation and validated_pages.get(page-1) == form and not explicit_years
        if form is None or unit is None or observed_scope != spec.report_scope or not (year_present or inherited_year or confirmed):
            diagnostics.append(f"page {page}: missing or conflicting form/scope/unit/year")
            continue
        columns = _column_indices(cells, page_texts=local_text, form=form, ocr=bool(payload.get("ocr")),
                                  fiscal_year=spec.fiscal_end.year, fiscal_start=spec.fiscal_start, fiscal_end=spec.fiscal_end)
        if columns is None and confirmed and form in previous_columns:
            old_page, inherited = previous_columns[form]
            code_col, current_col, prior_col, _ = inherited
            found_codes = {str(c.get("text", "")).strip() for c in cells if c.get("start_col_offset_idx") == code_col}
            available = {c.get("start_col_offset_idx") for c in cells}
            if (page > old_page and page - old_page <= 2 and
                    len(found_codes & EXTENDED_TARGET_CODES[form]) >= 2 and
                    {current_col, prior_col}.issubset(available)):
                columns = inherited
                diagnostics.append(f"page {page}: confirmed continuation inherited prior table columns")
        if columns is None:
            diagnostics.append(f"page {page}: unsupported table headings")
            continue
        validated_pages[page] = form
        previous_columns[form] = (page, columns)
        code_col, current_col, prior_col, note_col = columns
        cells = _split_merged_rows(cells, code_col, current_col, prior_col)
        cells = _align_misindexed_values(cells, code_col, current_col, prior_col)
        cells = _split_cross_year_cells(cells, code_col, current_col, prior_col)
        cells = _split_vertical_overlap(cells, code_col, current_col, prior_col)
        spatial_tokens = [] if table.get('_source_rotation') else payload.get('spatial_ocr', {}).get(str(page), [])
        if spatial_tokens:
            cells += recover_spatial_rows(cells, spatial_tokens, code_col, current_col, prior_col,
                                          payload.get('refined_code_groups', {}).get(str(page), []))
        rows: dict[int, dict[int, dict]] = {}
        for cell in cells:
            row, col = cell.get("start_row_offset_idx"), cell.get("start_col_offset_idx")
            if isinstance(row, int) and isinstance(col, int):
                rows.setdefault(row, {})[col] = cell
        code_counts = Counter(str(cols.get(code_col, {}).get('text', '')).strip() for cols in rows.values())
        doc = DocumentContext(
            company_name=spec.company_name, company_id=spec.company_id,
            report_scope=spec.report_scope, accounting_regime=spec.accounting_regime,
            form_code=form, fiscal_year_label=f"FY{spec.fiscal_end.year}",
            fiscal_period_start=spec.fiscal_start, fiscal_period_end=spec.fiscal_end,
            prepared_on=spec.prepared_on, document_hash=spec.document_hash,
            source_path=spec.source_path, data_origin="REAL",
        )
        for row, by_col in rows.items():
            code = str(by_col.get(code_col, {}).get("text", "")).strip()
            active_codes=EXTENDED_TARGET_CODES[form] if extended_codes else TARGET_CODES[form]
            if code not in active_codes:
                if len(code.split()) > 1 and any(token in active_codes for token in code.split()):
                    diagnostics.append(f"page {page} {form}: merged line codes are ambiguous")
                continue
            if code_counts[code] > 1:
                diagnostics.append(f'page {page} {form}:{code}: duplicate line code requires review')
                continue
            label_cell = next((by_col[col] for col in sorted(by_col) if col < code_col and str(by_col[col].get("text", "")).strip()), {})
            if not label_cell:
                label_cell = next((by_col[col] for col in sorted(by_col) if code_col < col < min(current_col, prior_col)
                                   and col != note_col and str(by_col[col].get("text", "")).strip()), {})
            label = " ".join(str(label_cell.get("text", "")).split()) or f"Mã {code}"
            note = " ".join(str(by_col.get(note_col, {}).get("text", "")).split()) or None if note_col is not None else None
            for is_current, column in ((True, current_col), (False, prior_col)):
                cell = by_col.get(column)
                if cell is None:
                    continue
                raw = str(cell.get("text", "")).strip()
                if not _unambiguous_pdf_number(raw):
                    diagnostics.append(f"page {page} {form}:{code}: ambiguous or merged numeric cell")
                    continue
                try:
                    value = parse_accounting_number(raw)
                except ValueError:
                    diagnostics.append(f"page {page} {form}:{code}: invalid numeric cell")
                    continue
                explicit_dash = raw in {"-", "–", "—"}
                if value is None and not explicit_dash:
                    continue
                box = cell.get("bbox") or {}
                coordinates = None
                origin = None
                if all(key in box for key in ("l", "t", "r", "b", "coord_origin")):
                    coordinates = tuple(float(box[key]) for key in ("l", "t", "r", "b"))
                    origin = box["coord_origin"]
                    if table.get('_source_rotation') == 90:
                        if origin != 'TOPLEFT' or not table.get('_source_original_height'):
                            diagnostics.append(f'page {page} {form}:{code}: cannot map rotated bbox')
                            continue
                        left,top,right,bottom=coordinates
                        height=float(table['_source_original_height'])
                        coordinates=(top,height-right,bottom,height-left)
                try:
                    locator = PDFLocator(page_number=page, table_id=f"{form}::{table.get('self_ref', table_number)}",
                                         region=f"Mã {code}, cột {'năm nay' if is_current else 'năm trước'}",
                                         bounding_box=coordinates, coordinate_origin=origin)
                except ValueError:
                    diagnostics.append(f"page {page} {form}:{code}: invalid cell bbox")
                    continue
                if explicit_dash:
                    if locator.bounding_box is None:
                        diagnostics.append(f"page {page} {form}:{code}: dash without numeric-cell bbox")
                        continue
                    value = Decimal(0)
                if cell.get("_merged_group"):
                    locator.region += " (ô ghép nhiều dòng; vùng bao toàn nhóm)"
                if cell.get('_cross_year_source'):
                    locator.region += ' (ô ghép hai cột năm; vùng bao cả hai số)'
                if cell.get('_vertical_source'):
                    locator.region += ' (ô ghép hai hàng; vùng bao cả hai số)'
                if cell.get('_code_refined'):
                    locator.region += ' (mã được OCR lại từ ảnh cắt)'
                if table.get('_source_rotation'):
                    locator.region += ' (đọc sau xoay; bbox quy về ảnh PDF gốc)'
                evidence = Evidence(document_hash=spec.document_hash, locator=locator, observed_text=cell.get('_cross_year_source',cell.get('_vertical_source',raw)))
                facts.append(FinancialFact(
                    document=doc, line_code=LineItemCode(accounting_regime=spec.accounting_regime, form_code=form, code=code),
                    original_label=label, normalized_label=label.casefold(), note_ref=note,
                    period=_period(spec, form, code, is_current), raw_value=raw,
                    zero_from_dash=explicit_dash, value=value,
                    currency="VND", unit_label=unit[0], unit_multiplier=unit[1],
                    normalized_value=value * unit[1], evidence=[evidence],
                ))
    if not document.get("tables"):
        diagnostics.append("NO_TABLES")
    return PdfIngestionResult(document_hash=spec.document_hash, facts=facts, diagnostics=tuple(diagnostics))
