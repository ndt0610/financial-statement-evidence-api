"""PDF API contract with a fake converter; no OCR model or network required."""
import io

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.api.main import create_app


PDF = b"%PDF-1.4\nsynthetic fixture\n%%EOF"
FORM = {"company_name": "Test company", "company_id": "TEST", "report_scope": "SEPARATE",
        "fiscal_start": "2025-01-01", "fiscal_end": "2025-12-31", "page_start": "1", "page_end": "1"}


def converter(source, output, start, end, ocr):
    assert source.read_bytes() == PDF and (start, end, ocr) == (1, 1, False)
    def cell(row, col, text):
        return {"start_row_offset_idx": row, "start_col_offset_idx": col, "text": text,
                "bbox": {"l": 1, "t": 1, "r": 10, "b": 10, "coord_origin": "TOPLEFT"}}
    return {"status": "SUCCESS", "document": {
        "texts": [{"text": "B02-DN báo cáo riêng đơn vị: đồng năm 2025", "prov": [{"page_no": 1}]}],
        "tables": [{"prov": [{"page_no": 1}], "data": {"table_cells": [
            cell(0, 1, "Mã số"), cell(0, 2, "Năm nay"), cell(0, 3, "Năm trước"),
            cell(1, 0, "Doanh thu thuần"), cell(1, 1, "10"), cell(1, 2, "1.000"), cell(1, 3, "900")
        ]}}]}}


def upload(client, form=FORM, content=PDF, name="example.pdf"):
    return client.post("/v1/documents/ingest-pdf", data=form, files={"file": (name, content, "application/pdf")})


def test_pdf_ingest_unverified_query_and_restart(tmp_path):
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        response = upload(client)
        assert response.status_code == 200, response.text
        assert response.json()["states"] == {"EXTRACTED": 2}
        key = response.json()["document_hash"]
        conflict = upload(client, {**FORM, "company_id": "OTHER"})
        assert conflict.status_code == 409
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        answer = client.post("/v1/qa/query", json={"document_hash": key, "question": "Doanh thu thuần năm 2025?"})
        assert answer.status_code == 200
        assert answer.json()["status"] == "REFUSED_MISSING_DATA"
        assert answer.json()["value"] is None
        assert answer.json()["evidences"][0]["locator"]["source_kind"] == "pdf"
        (tmp_path / f"{key}.pdf").write_bytes(b"damaged")
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        assert client.post("/v1/qa/query", json={"document_hash": key, "question": "Doanh thu thuần?"}).status_code == 409


def test_pdf_ingest_rejects_invalid_and_failed_conversion(tmp_path):
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        assert upload(client, content=b"invalid").status_code == 422
        assert upload(client, name="example.xlsx").status_code == 415
        assert upload(client, {**FORM, "page_end": "31"}).status_code == 422
        assert upload(client, {**FORM, "report_scope": "CONSOLIDATED"}).status_code == 422
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=lambda *args: {"status": "PARTIAL_SUCCESS"})) as client:
        assert upload(client).status_code == 422
        assert list(tmp_path.glob("*.pdf")) == []


def test_pdf_reingest_merges_new_pages_without_duplicating_old_ones(tmp_path):
    def by_page(source, output, start, end, ocr):
        payload = converter(source, output, 1, 1, ocr)
        for item in payload["document"]["texts"] + payload["document"]["tables"]:
            item["prov"][0]["page_no"] = start
        if start == 2:
            cells = payload["document"]["tables"][0]["data"]["table_cells"]
            cells[4]["text"] = "60"
            cells[3]["text"] = "Lợi nhuận sau thuế"
        return payload
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=by_page)) as client:
        assert upload(client).json()["fact_count"] == 2
        second = {**FORM, "page_start": "2", "page_end": "2"}
        assert upload(client, second).json()["fact_count"] == 4
        assert upload(client, second).json()["fact_count"] == 4


def test_manual_confirmation_is_audited_and_persists_across_restart(tmp_path):
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        uploaded = upload(client).json()
        key = uploaded["document_hash"]
        facts = client.get(f"/v1/documents/{key}/pdf-facts").json()["facts"]
        fact = next(item for item in facts if item["fact"]["period"]["fiscal_year_label"] == "FY2025")
        review = {"fact_id": fact["fact_id"], "reviewer": "A reviewer", "decision": "CONFIRM",
                  "reviewed_source_and_context": True, "confirmed_normalized_value": "1000",
                  "confirmed_currency": "VND", "confirmed_period": fact["fact"]["period"]}
        endpoint = f"/v1/documents/{key}/pdf-fact-reviews"
        assert client.post(endpoint, json={**review, "confirmed_normalized_value": "999"}).status_code == 409
        assert client.post(endpoint, json={**review, "reviewed_source_and_context": False}).status_code == 422
        assert client.post(endpoint, json={**review, "fact_id": "0" * 64}).status_code == 409
        assert client.post(endpoint, json=review).json()["state"] == "VERIFIED"
        assert client.post(endpoint, json=review).status_code == 409
        assert upload(client).status_code == 409
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        answer = client.post("/v1/qa/query", json={"document_hash": key, "question": "Doanh thu thuần năm 2025?"}).json()
        assert answer["status"] == "ANSWERED" and answer["value"] == "1000"
        assert answer["verification_status"] == "VERIFIED"
        history = client.get(f"/v1/documents/{key}/pdf-facts").json()["review_history"]
        assert len(history) == 1 and history[0]["reviewer"] == "A reviewer" and history[0]["decision"] == "CONFIRM"


def test_rejected_pdf_fact_stays_unverified(tmp_path):
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        key = upload(client).json()["document_hash"]
        item = client.get(f"/v1/documents/{key}/pdf-facts").json()["facts"][0]
        fact = item["fact"]
        review = {"fact_id": item["fact_id"], "reviewer": "Reviewer", "decision": "REJECT",
                  "reviewed_source_and_context": True, "confirmed_normalized_value": fact["normalized_value"],
                  "confirmed_currency": fact["currency"], "confirmed_period": fact["period"], "comment": "Wrong OCR"}
        assert client.post(f"/v1/documents/{key}/pdf-fact-reviews", json={**review, "comment": ""}).status_code == 422
        result = client.post(f"/v1/documents/{key}/pdf-fact-reviews", json=review)
        assert result.status_code == 200 and result.json()["state"] == "REJECTED_OCR_ERROR"
        assert client.get(f"/v1/documents/{key}/pdf-facts").json()["review_history"][0]["decision"] == "REJECT"


def test_pdf_note_requires_review_and_links_to_hybrid_answer(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    pdf_bytes = buffer.getvalue()
    def with_note(source, output, start, end, ocr):
        payload = converter_stub()
        return payload
    def converter_stub():
        # Reuse the table fixture without asserting the source bytes.
        class Dummy:
            def read_bytes(self):
                return PDF
        payload = converter(Dummy(), None, 1, 1, False)
        cells = payload["document"]["tables"][0]["data"]["table_cells"]
        cells.extend([{"start_row_offset_idx": 0, "start_col_offset_idx": 4, "text": "Thuyết minh"},
                      {"start_row_offset_idx": 1, "start_col_offset_idx": 4, "text": "VI.02"}])
        return payload
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=with_note)) as client:
        key = upload(client, content=pdf_bytes).json()["document_hash"]
        items = client.get(f"/v1/documents/{key}/pdf-facts").json()["facts"]
        item = next(x for x in items if x["fact"]["period"]["fiscal_year_label"] == "FY2025")
        note = {"fact_id": item["fact_id"], "reviewer": "Reviewer", "note_ref": "VI.02", "page_number": 2,
                "excerpt": "Doanh thu đến từ bán hàng.", "reviewed_source_and_link": True}
        note_url = f"/v1/documents/{key}/pdf-note-reviews"
        assert client.post(note_url, json=note).status_code == 409
        fact = item["fact"]
        review = {"fact_id": item["fact_id"], "reviewer": "Reviewer", "decision": "CONFIRM",
                  "reviewed_source_and_context": True, "confirmed_normalized_value": fact["normalized_value"],
                  "confirmed_currency": fact["currency"], "confirmed_period": fact["period"]}
        fact_id = client.post(f"/v1/documents/{key}/pdf-fact-reviews", json=review).json()["fact_id"]
        note["fact_id"] = fact_id
        assert client.post(note_url, json={**note, "note_ref": "V.1"}).status_code == 409
        assert client.post(note_url, json={**note, "page_number": 3}).status_code == 422
        assert client.post(note_url, json=note).status_code == 200
        response = client.post("/v1/qa/query", json={"document_hash": key, "question": "Doanh thu thuần năm 2025 và thuyết minh VI.02?"}).json()
        assert response["status"] == "ANSWERED"
        assert "Doanh thu đến từ bán hàng" in response["answer"]
        assert response["evidences"][1]["locator"]["page_number"] == 2
