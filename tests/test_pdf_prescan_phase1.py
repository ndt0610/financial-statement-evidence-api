"""Phase 1 page-map acceptance tests use generated PDFs, never source filings."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from urllib.request import urlopen

import fitz
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.adapters.pdf_adapter import PdfDocumentSpec, extract_docling_tables
from app.models import ReportScope
from app.services.pdf_prescan import _select_light_ocr_pages, scan_pdf
from scripts.evaluate_pdf_prescan import evaluate


def _line(page, y, code, label, current="1.000", prior="900", *, offset=0):
    page.insert_text((35 + offset, y), label, fontsize=9)
    page.insert_text((290 + offset, y), code, fontsize=9)
    page.insert_text((390 + offset, y), current, fontsize=9)
    page.insert_text((480 + offset, y), prior, fontsize=9)


def _heading(page, title, *, offset=0):
    page.insert_text((35 + offset, 50), title, fontsize=11)
    page.insert_text((290 + offset, 70), "Ma so", fontsize=9)
    page.insert_text((390 + offset, 70), "2024", fontsize=9)
    page.insert_text((480 + offset, 70), "2023", fontsize=9)


def _generated_pdf(path, count=57):
    pdf = fitz.open()
    for _ in range(count):
        pdf.new_page(width=595, height=842)
    _heading(pdf[2], "B01-DN/HN Bang can doi ke toan")
    _line(pdf[2], 100, "100", "Tai san ngan han")
    _line(pdf[2], 120, "110", "Tien")
    _line(pdf[3], 100, "300", "No phai tra")
    _line(pdf[3], 120, "400", "Von chu so huu")
    _heading(pdf[39], "B02-DN/HN Ket qua hoat dong kinh doanh")
    _line(pdf[39], 100, "10", "Doanh thu")
    _line(pdf[39], 120, "20", "Loi nhuan gop")
    _heading(pdf[49], "B03-DN/HN Luu chuyen tien te")
    _line(pdf[49], 100, "01", "Loi nhuan truoc thue")
    _line(pdf[49], 120, "20", "Tien tu kinh doanh")
    _line(pdf[51], 100, "21", "Tien dau tu")
    _line(pdf[51], 120, "30", "Tong dau tu")
    pdf.save(path)
    pdf.close()


def test_full_57_page_inventory_and_untitled_continuations(tmp_path):
    source = tmp_path / "long.pdf"
    _generated_pdf(source)
    result = scan_pdf(source)
    assert result["page_count"] == 57
    assert result["missing_forms"] == []
    observed = {(x["page_number"], x["form"], x["reason"]) for x in result["candidates"]}
    assert (3, "B01-DN", "TITLE_OR_FORM") in observed
    assert (4, "B01-DN", "CONTINUATION_CODE_SEQUENCE") in observed
    assert (40, "B02-DN", "TITLE_OR_FORM") in observed
    assert (50, "B03-DN", "TITLE_OR_FORM") in observed
    assert (52, "B03-DN", "CONTINUATION_CODE_SEQUENCE") in observed


def test_scanned_late_table_clusters_receive_bounded_ocr_slots():
    ranked = [(3, page) for page in range(1, 58)]
    ranked = [(30 if page in {7, 40, 50} else 14 if page in {8, 9, 10, 11, 12, 13, 41, 51}
               else score, page) for score, page in ranked]
    selected = _select_light_ocr_pages(57, ranked, 12)
    assert selected[:7] == [7, 8, 9, 10, 11, 12, 13]
    assert {40, 41, 50, 51} <= set(selected)
    assert len(selected) <= 12


def test_complete_page_map_evaluation_reports_exact_metrics(tmp_path):
    source = tmp_path / "long.pdf"
    _generated_pdf(source)
    gold = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "document_type": "generated-text",
            "pages": [{"page_number": page, "form": form, "region": "FULL"} for page, form in (
                (3, "B01-DN"), (4, "B01-DN"), (40, "B02-DN"), (50, "B03-DN"), (52, "B03-DN"))]}
    result = evaluate(source, gold)
    assert result["page_region_precision"] == result["page_region_recall"] == 1
    assert result["missing_form_detection_correct"] is True
    assert not result["misses"] and not result["extra_candidates"]
    with pytest.raises(ValueError, match="hash"):
        evaluate(source, {**gold, "source_sha256": "0" * 64})


def test_two_up_and_partial_report_are_visible(tmp_path):
    source = tmp_path / "two-up.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=1200, height=842)
    _heading(page, "B01-DN/HN Balance", offset=0)
    _heading(page, "B02-DN/HN Earnings", offset=600)
    _line(page, 100, "100", "Assets", offset=0)
    _line(page, 120, "110", "Cash", offset=0)
    _line(page, 100, "10", "Revenue", offset=600)
    _line(page, 120, "20", "Profit", offset=600)
    pdf.save(source)
    pdf.close()
    candidates = scan_pdf(source)["candidates"]
    assert {(x["form"], x["region"]) for x in candidates} == {("B01-DN", "LEFT"), ("B02-DN", "RIGHT")}
    only_b02 = tmp_path / "part.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    _heading(page, "B02-DN/HN Earnings")
    _line(page, 100, "10", "Revenue")
    pdf.save(only_b02)
    pdf.close()
    assert scan_pdf(only_b02)["missing_forms"] == ["B01-DN", "B03-DN"]


def test_overlapping_orthogonal_halves_are_not_auto_selected(tmp_path):
    source = tmp_path / "overlap.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=1200, height=842)
    _heading(page, "B02-DN/HN Ket qua hoat dong kinh doanh")
    _line(page, 100, "10", "Revenue")
    page.insert_text((650, 500), "B03-DN/HN Luu chuyen tien te", fontsize=11)
    page.insert_text((900, 520), "2024", fontsize=9)
    page.insert_text((1000, 520), "2023", fontsize=9)
    _line(page, 550, "20", "Cash", offset=600)
    pdf.save(source)
    pdf.close()
    candidates = scan_pdf(source)["candidates"]
    assert {(item["form"], item["region"]) for item in candidates} == {
        ("B02-DN", "LEFT"), ("B03-DN", "RIGHT")}


def test_single_statement_on_one_half_of_landscape_page(tmp_path):
    source = tmp_path / "single-half.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=1200, height=842)
    for line in range(6):
        page.insert_text((35, 100 + 25 * line), "Auditor opinion explains scope and methods in this report", fontsize=9)
    _heading(page, "B01-DN/HN Bang can doi ke toan", offset=600)
    _line(page, 100, "100", "Assets", offset=600)
    _line(page, 120, "110", "Cash", offset=600)
    pdf.save(source)
    pdf.close()
    assert [(item["form"], item["region"]) for item in scan_pdf(source)["candidates"]] == [
        ("B01-DN", "RIGHT")]


def test_rotated_page_keeps_physical_page_and_dimensions(tmp_path):
    source = tmp_path / "rotated.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    _heading(page, "B02-DN/HN Ket qua hoat dong kinh doanh")
    _line(page, 100, "10", "Revenue")
    page.set_rotation(90)
    pdf.save(source)
    pdf.close()
    result = scan_pdf(source)
    assert result["page_count"] == 1
    assert result["pages"][0]["width"] == 842
    assert (result["candidates"][0]["page_number"], result["candidates"][0]["form"]) == (1, "B02-DN")


def test_short_text_layer_is_kept_even_when_page_has_an_image(tmp_path):
    source = tmp_path / "mixed.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    icon = page.get_pixmap(matrix=fitz.Matrix(0.05, 0.05), alpha=False).tobytes("png")
    page.insert_image(fitz.Rect(520, 10, 560, 50), stream=icon)
    _heading(page, "B02-DN/HN Ket qua hoat dong kinh doanh")
    _line(page, 100, "10", "Revenue")
    pdf.save(source)
    pdf.close()
    result = scan_pdf(source, max_light_ocr_pages=0)
    assert result["pages"][0]["route"] == "MIXED_LOW_TEXT"
    assert result["candidates"][0]["form"] == "B02-DN"


def test_first_detected_continuation_suggests_preceding_image_for_review(tmp_path):
    source = tmp_path / "continued.pdf"
    pdf = fitz.open()
    pdf.new_page()
    page = pdf.new_page()
    _heading(page, "B01-DN/HN Bang can doi ke toan (tiep theo)")
    _line(page, 100, "200", "Long-term assets")
    _line(page, 120, "270", "Total assets")
    pdf.save(source)
    pdf.close()
    result = scan_pdf(source, max_light_ocr_pages=0)
    assert [(item["page_number"], item["reason"], item["confidence"]) for item in result["candidates"]] == [
        (1, "PRECEDING_PAGE_REVIEW", "LOW"), (2, "TITLE_OR_FORM", "HIGH")]


def test_missing_b03_suggests_following_image_pages_without_claiming_detection(tmp_path):
    source = tmp_path / "after-b02.pdf"
    pdf = fitz.open()
    first = pdf.new_page()
    _heading(first, "B02-DN/HN Ket qua hoat dong kinh doanh")
    _line(first, 100, "10", "Revenue")
    pdf.new_page()
    pdf.new_page()
    pdf.save(source)
    pdf.close()
    result = scan_pdf(source, max_light_ocr_pages=0)
    assert [(item["page_number"], item["reason"]) for item in result["candidates"]] == [
        (1, "TITLE_OR_FORM"), (2, "FOLLOWING_PAGE_REVIEW"), (3, "FOLLOWING_PAGE_REVIEW")]
    assert result["missing_forms"] == ["B01-DN", "B03-DN"]


def test_missing_rotated_b02_between_other_statements_is_review_suggestion(tmp_path):
    source = tmp_path / "rotated-gap.pdf"
    pdf = fitz.open()
    first = pdf.new_page()
    _heading(first, "B01-DN/HN Bang can doi ke toan")
    _line(first, 100, "100", "Assets")
    _line(first, 120, "110", "Cash")
    pdf.new_page()
    third = pdf.new_page()
    _heading(third, "B03-DN/HN Luu chuyen tien te")
    _line(third, 100, "01", "Profit")
    _line(third, 120, "20", "CFO")
    pdf.save(source)
    pdf.close()
    result = scan_pdf(source, max_light_ocr_pages=0)
    assert [(item["page_number"], item["form"], item["reason"]) for item in result["candidates"]] == [
        (1, "B01-DN", "TITLE_OR_FORM"), (2, "B02-DN", "BETWEEN_STATEMENTS_REVIEW"),
        (3, "B03-DN", "TITLE_OR_FORM")]
    assert result["missing_forms"] == ["B02-DN"]


def test_b09_note_page_cannot_be_mistaken_for_b03_continuation(tmp_path):
    source = tmp_path / "note.pdf"
    pdf = fitz.open()
    first = pdf.new_page()
    _heading(first, "B03-DN/HN Luu chuyen tien te")
    _line(first, 100, "01", "Cash flow")
    _line(first, 120, "20", "Operating cash")
    second = pdf.new_page()
    _heading(second, "B09-DN/HN Thuyet minh bao cao tai chinh")
    _line(second, 100, "21", "Note item")
    _line(second, 120, "30", "Note item")
    pdf.save(source)
    pdf.close()
    result = scan_pdf(source)
    assert [(item["page_number"], item["form"]) for item in result["candidates"]] == [(1, "B03-DN")]
    assert "B09/notes title detected" in result["pages"][1]["diagnostics"][0]


def test_narrative_and_b09_mentions_are_not_statement_pages(tmp_path):
    source = tmp_path / "mentions.pdf"
    pdf = fitz.open()
    first = pdf.new_page()
    first.insert_text((35, 50), "Ket qua hoat dong kinh doanh tang truong nam 2024 va 2023", fontsize=10)
    _line(first, 100, "80", "Analysis")
    _line(first, 120, "175", "Analysis")
    second = pdf.new_page()
    second.insert_text((35, 50), "B09-DN/HN Thuyet minh bao cao tai chinh", fontsize=10)
    second.insert_text((35, 80), "Ket qua hoat dong kinh doanh duoc dien giai", fontsize=10)
    _line(second, 100, "21", "Note")
    _line(second, 120, "33", "Note")
    pdf.save(source)
    pdf.close()
    assert scan_pdf(source)["candidates"] == []


def test_damaged_text_layer_form_marker_is_detected_with_table_evidence(tmp_path):
    source = tmp_path / "damaged-marker.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    _heading(page, "801-DN/HN financial statement")
    _line(page, 100, "100", "Assets")
    _line(page, 120, "110", "Cash")
    pdf.save(source)
    pdf.close()
    result = scan_pdf(source, max_light_ocr_pages=0)
    assert [(item["form"], item["region"]) for item in result["candidates"]] == [("B01-DN", "FULL")]


def test_long_image_only_pdf_does_not_run_full_ocr(tmp_path):
    source = tmp_path / "image-only.pdf"
    pdf = fitz.open()
    for _ in range(60):
        pdf.new_page()
    pdf.save(source)
    pdf.close()
    started = time.monotonic()
    result = scan_pdf(source)
    assert time.monotonic() - started < 5
    assert result["page_count"] == 60
    assert all(page["route"] == "IMAGE_REVIEW" for page in result["pages"])
    assert result["candidates"] == []


def _cell(row, col, value):
    return {"start_row_offset_idx": row, "start_col_offset_idx": col, "text": value,
            "bbox": {"l": 10 + col * 20, "t": 20 + row * 20, "r": 20 + col * 20,
                     "b": 30 + row * 20, "coord_origin": "TOPLEFT"}}


def _mock_converter(source, output, start, end, ocr):
    def table(page, codes, headers):
        cells = [_cell(0, i, value) for i, value in enumerate(headers)] if headers else []
        for row, code in enumerate(codes, start=1):
            cells += [_cell(row, 1, code), _cell(row, 2, "1.000"), _cell(row, 3, "900")]
        return {"prov": [{"page_no": page}], "data": {"table_cells": cells}}
    return {"status": "SUCCESS", "ocr": False,
            "document": {"texts": [
                {"text": "B01-DN/HN hop nhat nam 2024 don vi tinh: dong", "prov": [{"page_no": 1}]},
                {"text": "tiep theo", "prov": [{"page_no": 2}]},
            ], "tables": [table(1, ("100", "110"), ("Chi tieu", "Ma so", "2024 VND", "2023 VND")),
                          table(2, ("300", "400"), ())]}}


def _confirm_payload(prescan, pages):
    return {"prescan_id": prescan["prescan_id"], "company_name": "Demo", "company_id": "DEMO",
            "report_scope": "CONSOLIDATED", "fiscal_start": "2024-01-01", "fiscal_end": "2024-12-31",
            "unit_label": "đồng", "reviewer": "Reviewer", "reviewed_page_map": True,
            "pages": pages, "ocr": False}


def test_api_requires_confirmed_map_and_extracts_untitled_page(tmp_path):
    source = tmp_path / "source.pdf"
    pdf = fitz.open()
    first = pdf.new_page()
    _heading(first, "B01-DN/HN Bang can doi ke toan")
    _line(first, 100, "100", "Assets")
    _line(first, 120, "110", "Cash")
    second = pdf.new_page()
    _line(second, 100, "300", "Liabilities")
    _line(second, 120, "400", "Equity")
    pdf.save(source)
    pdf.close()
    calls = []
    def converter(*args):
        calls.append(args[2:4])
        return _mock_converter(*args)
    with TestClient(create_app(upload_dir=tmp_path / "uploads", pdf_converter=converter)) as client:
        assert client.post("/v1/documents/prescan", files={"file": ("bad.pdf", b"not a pdf")}).status_code == 422
        assert not list((tmp_path / "uploads").glob(".prescan-upload-*.pdf"))
        pre = client.post("/v1/documents/prescan", files={"file": ("sample.pdf", source.read_bytes())})
        assert pre.status_code == 200, pre.text
        info = pre.json()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        assert info["document_hash"] == digest and calls == []
        assert not (tmp_path / "uploads" / f"{digest}.index.json").exists()
        assert client.get(f"/v1/documents/{digest}/prescan-page/2").headers["content-type"] == "image/png"
        selected = [{"page_number": page, "form_code": "B01-DN", "region": "FULL"} for page in (1, 2)]
        body = _confirm_payload(info, selected)
        bad = {**body, "prescan_id": "0" * 64}
        assert client.post(f"/v1/documents/{digest}/extract-confirmed", json=bad).status_code == 409
        assert client.post(f"/v1/documents/{digest}/extract-confirmed", json={**body, "pages": selected + [selected[0]]}).status_code == 422
        assert client.post(f"/v1/documents/{digest}/extract-confirmed", json={**body, "report_scope": "SEPARATE"}).status_code == 422
        assert client.post(f"/v1/documents/{digest}/extract-confirmed", json={**body,
            "fiscal_start": "2025-01-01", "fiscal_end": "2025-12-31"}).status_code == 422
        partial = client.post(f"/v1/documents/{digest}/extract-confirmed", json={**body, "pages": selected[:1]})
        assert partial.status_code == 200, partial.text
        assert partial.json()["states"] == {"EXTRACTED": 4}
        partial_queue = client.get(f"/v1/documents/{digest}/audit-queue").json()["facts"]
        assert {item["code"] for item in partial_queue} == {"100", "110"}
        response = client.post(f"/v1/documents/{digest}/extract-confirmed", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["states"] == {"EXTRACTED": 8}
        assert calls == [(1, 1), (1, 2)]
        queue = client.get(f"/v1/documents/{digest}/audit-queue")
        assert queue.status_code == 200
        assert {item["code"] for item in queue.json()["facts"]} >= {"100", "110", "300", "400"}


def test_confirmed_half_page_excludes_other_half_even_with_one_selection():
    def table(offset, value):
        cells = [_cell(0, i, heading) for i, heading in enumerate(("Chi tieu", "Ma so", "2024 VND", "2023 VND"))]
        cells += [_cell(1, 1, "100"), _cell(1, 2, value), _cell(1, 3, "900")]
        for cell in cells:
            cell["bbox"]["l"] += offset
            cell["bbox"]["r"] += offset
        return {"prov": [{"page_no": 1, "bbox": {"l": offset, "t": 10, "r": offset + 300,
                                                     "b": 150, "coord_origin": "TOPLEFT"}}],
                "data": {"table_cells": cells}}
    payload = {"status": "SUCCESS", "ocr": False, "document": {
        "texts": [{"text": "B01-DN/HN hop nhat nam 2024 don vi tinh: dong", "prov": [{"page_no": 1}]}],
        "tables": [table(0, "1.000"), table(600, "9.000")]}}
    spec = PdfDocumentSpec(company_name="Demo", company_id="DEMO", document_hash="a" * 64,
        source_path="demo.pdf", report_scope=ReportScope.CONSOLIDATED,
        accounting_regime="TT200", fiscal_start=date(2024, 1, 1), fiscal_end=date(2024, 12, 31))
    selected = {1: [{"form_code": "B01-DN", "region": "LEFT", "region_bbox": (0, 0, 500, 842),
                     "page_height": 842, "unit_label": "đồng", "unit_multiplier": "1"}]}
    result = extract_docling_tables(payload, spec, confirmed_regions=selected)
    assert {fact.normalized_value for fact in result.facts} == {1000, 900}
    assert "table outside confirmed regions" in " ".join(result.diagnostics)


def test_ui_contract_has_page_review_instead_of_fixed_30_page_fields():
    html = (Path(__file__).resolve().parents[1] / "app/web/analysis.html").read_text(encoding="utf-8")
    js = (Path(__file__).resolve().parents[1] / "app/web/pdf-prescan-ui.js").read_text(encoding="utf-8")
    assert 'id="prescan-selection"' in html
    assert 'id="upload-start"' not in html and 'id="upload-end"' not in html
    assert "reviewed_page_map:true" in js and "prescan-page" in js


@pytest.mark.skipif(not list((Path(__file__).resolve().parents[1] /
                              "data/demo_vhc_uploads").glob("*.pdf")), reason="Local VHC development PDF unavailable")
def test_development_vhc_57_page_scan_finds_all_three_forms():
    source = next((Path(__file__).resolve().parents[1] / "data/demo_vhc_uploads").glob("*.pdf"))
    result = scan_pdf(source)
    assert result["page_count"] == 57
    assert {(item["page_number"], item["form"]) for item in result["candidates"]} >= {
        (7, "B01-DN"), (10, "B02-DN"), (11, "B03-DN")}
    assert not any(item["page_number"] >= 12 and item["form"] == "B03-DN"
                   for item in result["candidates"])


def test_browser_prescan_review_and_confirm(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    source = tmp_path / "browser.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    _heading(page, "B02-DN/HN Ket qua hoat dong kinh doanh")
    _line(page, 100, "10", "Revenue")
    pdf.save(source)
    pdf.close()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = [sys.executable, "-c", "import os; from pathlib import Path; import uvicorn; "
               "from app.api.main import create_app; "
               "uvicorn.run(create_app(upload_dir=Path(os.environ['PRESCAN_UPLOAD_DIR'])), "
               "host='127.0.0.1', port=int(os.environ['PRESCAN_PORT']), log_level='error')"]
    server = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1],
                              env={**os.environ, "PRESCAN_UPLOAD_DIR": str(tmp_path / "uploads"),
                                   "PRESCAN_PORT": str(port)},
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(80):
            try:
                with urlopen(base + "/analysis", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("Pre-scan browser server did not start")
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(headless=True)
            except Exception as error:
                pytest.skip(f"Chromium unavailable: {error}")
            try:
                window = browser.new_page()
                submitted = []
                def confirm(route):
                    submitted.append(json.loads(route.request.post_data))
                    route.fulfill(status=200, content_type="application/json", body=json.dumps({
                        "document_hash": submitted[-1]["prescan_id"], "fact_count": 2,
                        "states": {"EXTRACTED": 2}, "selected_pages": [1], "diagnostics": []}))
                window.route("**/extract-confirmed", confirm)
                window.goto(base + "/analysis")
                window.locator("#upload-open").click()
                window.locator("#upload-file").set_input_files(str(source))
                window.locator("#upload-id").fill("DEMO")
                window.locator("#upload-name").fill("Demo Company")
                window.locator("#upload-year").fill("2024")
                window.locator("#upload-form button[type=submit]").click()
                window.locator("#prescan-selection").wait_for(state="visible")
                assert "1 trang" in window.locator("#prescan-selection").inner_text()
                assert window.locator(".prescan-row").count() == 1
                window.locator(".prescan-region").select_option("LEFT")
                assert window.locator(".prescan-region-mark").get_attribute("data-region") == "LEFT"
                window.locator(".prescan-region").select_option("FULL")
                window.locator("#prescan-page").fill("1")
                window.locator("#prescan-add-button").click()
                assert window.locator(".prescan-row").count() == 1
                assert "đã có" in window.locator("#upload-message").inner_text()
                dark_color = window.locator("#prescan-selection").evaluate(
                    "element => [getComputedStyle(element).color, getComputedStyle(element).backgroundColor]")
                for width in (375, 768, 1024, 1440):
                    window.set_viewport_size({"width": width, "height": 900})
                    sizes = window.locator("#upload-dialog").evaluate(
                        "element => [element.scrollWidth, element.clientWidth]")
                    assert sizes[0] <= sizes[1] + 2, (width, sizes)
                window.locator("#upload-cancel").click()
                window.locator("#p10-theme").click()
                window.locator("#upload-open").click()
                assert window.locator("#prescan-selection").is_visible()
                light_color = window.locator("#prescan-selection").evaluate(
                    "element => [getComputedStyle(element).color, getComputedStyle(element).backgroundColor]")
                assert dark_color != light_color
                def contrast(pair):
                    def luminance(value):
                        rgb = [int(token) / 255 for token in re.findall(r"\d+", value)[:3]]
                        linear = [n / 12.92 if n <= 0.04045 else ((n + 0.055) / 1.055) ** 2.4 for n in rgb]
                        return sum(a * b for a, b in zip(linear, (0.2126, 0.7152, 0.0722)))
                    first, second = sorted((luminance(value) for value in pair))
                    return (second + 0.05) / (first + 0.05)
                assert contrast(dark_color) >= 4.5
                assert contrast(light_color) >= 4.5
                window.locator("#upload-form button[type=submit]").click()
                window.wait_for_timeout(500)
                assert len(submitted) == 1
                assert submitted[0]["reviewed_page_map"] is True
                assert submitted[0]["pages"] == [{"page_number": 1, "form_code": "B02-DN", "region": "FULL"}]
            finally:
                browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
