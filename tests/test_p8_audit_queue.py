"""The audit queue changes only reviewed PDF revisions and retains OCR history."""
import io

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.api.main import create_app


def source_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def converter(source, output, start, end, ocr):
    def cell(row, col, text):
        return {"start_row_offset_idx": row, "start_col_offset_idx": col, "text": text,
                "bbox": {"l": 10 + col * 50, "t": 15 + row * 20,
                         "r": 55 + col * 50, "b": 30 + row * 20,
                         "coord_origin": "TOPLEFT"}}
    cells = [cell(0, 1, "Mã số"), cell(0, 2, "Số cuối năm"), cell(0, 3, "Số đầu năm")]
    for row, (code, label, current, prior) in enumerate([
        ("270", "Tổng tài sản", "100", "90"),
        ("300", "Nợ phải trả", "60", "50"),
        ("400", "Vốn chủ sở hữu", "40", "40"),
    ], start=1):
        cells.extend([cell(row, 0, label), cell(row, 1, code),
                      cell(row, 2, current), cell(row, 3, prior)])
    return {"status": "SUCCESS", "document": {
        "texts": [{"text": "B01-DN báo cáo hợp nhất đơn vị: đồng năm 2025", "prov": [{"page_no": 1}]}],
        "tables": [{"prov": [{"page_no": 1}], "data": {"table_cells": cells}}]}}


FORM = {"company_name": "Test company", "company_id": "TEST", "report_scope": "CONSOLIDATED",
        "fiscal_start": "2025-01-01", "fiscal_end": "2025-12-31", "page_start": "1", "page_end": "1"}


def test_reconciled_batch_is_atomic_and_preview_is_highlighted(tmp_path):
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        response = client.post("/v1/documents/ingest-pdf", data=FORM,
                               files={"file": ("source.pdf", source_pdf(), "application/pdf")})
        assert response.status_code == 200, response.text
        key = response.json()["document_hash"]
        queue = client.get(f"/v1/documents/{key}/audit-queue").json()
        group = next(g for g in queue["reconciled_groups"] if g["fiscal_year"] == 2025)
        assert group["batch_eligible"] and group["reconciliation_residual_vnd"] == "0"
        assert group["pending_count"] == 3
        fact_id = group["pending_rows"][0]["fact_id"]
        preview = client.get(f"/v1/documents/{key}/audit-queue/{fact_id}/preview")
        assert preview.status_code == 200 and preview.content.startswith(b"\x89PNG")
        payload = {"form_code": "B01-DN", "fiscal_year": 2025, "reviewer": "Analyst",
                   "reviewed_all_source_pages": True,
                   "rows": [{"fact_id": r["fact_id"], "confirmed_normalized_value": r["value_vnd"]}
                            for r in group["pending_rows"]]}
        assert client.post(f"/v1/documents/{key}/audit-queue/batch-confirm",
                           json={**payload, "rows": []}).status_code == 422
        changed = [*payload["rows"]]
        changed[0] = {**changed[0], "confirmed_normalized_value": "999"}
        assert client.post(f"/v1/documents/{key}/audit-queue/batch-confirm",
                           json={**payload, "rows": changed}).status_code == 409
        result = client.post(f"/v1/documents/{key}/audit-queue/batch-confirm", json=payload)
        assert result.status_code == 200 and result.json()["signed_count"] == 3
        again = client.get(f"/v1/documents/{key}/audit-queue").json()
        assert len([r for r in again["review_history"] if r["decision"] == "BATCH_CONFIRM"]) == 3
        assert client.post(f"/v1/documents/{key}/audit-queue/batch-confirm", json=payload).status_code == 409


def test_unmatched_and_parenthesized_rows_require_individual_review(tmp_path):
    def with_extra(source, output, start, end, ocr):
        payload = converter(source, output, start, end, ocr)
        cells = payload['document']['tables'][0]['data']['table_cells']
        for col, text in enumerate(('Tiền', '110', '(20)', '(10)')):
            cells.append({**cells[col + 3], 'start_row_offset_idx': 4,
                          'start_col_offset_idx': col, 'text': text})
        return payload
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=with_extra)) as client:
        uploaded = client.post('/v1/documents/ingest-pdf', data=FORM,
                               files={'file': ('source.pdf', source_pdf())})
        assert uploaded.status_code == 200, uploaded.text
        key = uploaded.json()['document_hash']
        group = next(g for g in client.get(f'/v1/documents/{key}/audit-queue').json()['reconciled_groups']
                     if g['fiscal_year'] == 2025)
        flagged = next(r for r in group['pending_rows'] if r['code'] == '110')
        assert group['batch_eligible'] and group['individual_review_count'] == 1
        assert flagged['review_bucket'] == 'INDIVIDUAL_REVIEW'
        assert {'PARENTHESIZED_VALUE_REVIEW', 'NOT_IN_MATCHED_IDENTITY'} <= set(flagged['warnings'])
        rejected = client.post(f'/v1/documents/{key}/audit-queue/batch-confirm', json={
            'form_code': 'B01-DN', 'fiscal_year': 2025, 'reviewer': 'Analyst',
            'reviewed_all_source_pages': True,
            'rows': [{'fact_id': flagged['fact_id'], 'confirmed_normalized_value': flagged['value_vnd']}]})
        assert rejected.status_code == 409


def test_printed_dash_becomes_zero_only_with_located_numeric_cell(tmp_path):
    def with_dash(source, output, start, end, ocr):
        payload = converter(source, output, start, end, ocr)
        cells = payload['document']['tables'][0]['data']['table_cells']
        next(c for c in cells if c['start_row_offset_idx'] == 2 and
             c['start_col_offset_idx'] == 2)['text'] = '—'
        return payload
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=with_dash)) as client:
        uploaded = client.post('/v1/documents/ingest-pdf', data=FORM,
                               files={'file': ('source.pdf', source_pdf())})
        assert uploaded.status_code == 200, uploaded.text
        key = uploaded.json()['document_hash']
        queue = client.get(f'/v1/documents/{key}/audit-queue').json()
        row = next(r for r in queue['facts'] if r['fiscal_year'] == 2025 and r['code'] == '300')
        assert row['raw_value'] == '—' and row['normalized_value_vnd'] == '0'
        assert row['state'] == 'EXTRACTED'
        assert row['evidence']['observed_text'] == '—'
        assert row['evidence']['locator']['bounding_box']
        import json
        # The persisted fact retains the explicit-dash provenance flag.
        stored = json.loads((tmp_path / f'{key}.index.json').read_text(encoding='utf-8'))
        assert next(f for f in stored['facts'] if f['line_code']['code'] == '300' and
                    f['period']['fiscal_year_label'] == 'FY2025')['zero_from_dash']
        correction = client.post(f'/v1/documents/{key}/audit-queue/correct', json={
            'fact_id': row['fact_id'], 'reviewer': 'Analyst', 'reviewed_source_and_context': True,
            'corrected_normalized_value': '6', 'confirmed_currency': 'VND',
            'confirmed_period': row['period'], 'comment': 'Printed mark misread; source shows 6'})
        assert correction.status_code == 200, correction.text
        stored = json.loads((tmp_path / f'{key}.index.json').read_text(encoding='utf-8'))
        corrected = next(f for f in stored['facts'] if f['line_code']['code'] == '300' and
                         f['period']['fiscal_year_label'] == 'FY2025')
        assert corrected['normalized_value'] == '6' and corrected['zero_from_dash'] is False


def test_extended_index_append_keeps_signed_fact_history_and_rejects_conflicts(tmp_path):
    import json
    import pytest
    from pathlib import Path
    from app.services.qa_service import DocumentIndex
    from app.services.fact_identity import pdf_fact_id
    from scripts.augment_extended_pdf_index import extend

    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        uploaded = client.post('/v1/documents/ingest-pdf', data=FORM,
                               files={'file': ('source.pdf', source_pdf())})
        assert uploaded.status_code == 200, uploaded.text
        key = uploaded.json()['document_hash']
        row = next(r for r in client.get(f'/v1/documents/{key}/audit-queue').json()['facts']
                   if r['code'] == '270' and r['fiscal_year'] == 2025)
        signed = client.post(f'/v1/documents/{key}/signoff', json={
            'fact_id': row['fact_id'], 'reviewer': 'Analyst', 'decision': 'CONFIRM',
            'reviewed_source_and_context': True, 'confirmed_normalized_value': row['normalized_value_vnd'],
            'confirmed_currency': 'VND', 'confirmed_period': row['period']})
        assert signed.status_code == 200, signed.text

    old = DocumentIndex.from_json_dict(json.loads((tmp_path / f'{key}.index.json').read_text(encoding='utf-8')))
    old_ids = {pdf_fact_id(f) for f in old.result.facts}
    payload = converter(None, None, 1, 1, False)
    cells = payload['document']['tables'][0]['data']['table_cells']
    for col, text in enumerate(('Tổng nguồn vốn', '440', '100', '90')):
        cells.append({**cells[col + 3], 'start_row_offset_idx': 4, 'start_col_offset_idx': col,
                      'text': text})
    cache = Path(tmp_path) / 'converter.json'
    cache.write_text(json.dumps(payload), encoding='utf-8')
    preview = extend(key, cache, upload_dir=tmp_path)
    assert preview['append_count'] == 2 and preview['existing_value_conflicts'] == 0
    assert not list(tmp_path.glob('*.pre-extended-backup'))
    applied = extend(key, cache, upload_dir=tmp_path, apply=True)
    updated = DocumentIndex.from_json_dict(json.loads((tmp_path / f'{key}.index.json').read_text(encoding='utf-8')))
    assert applied['append_count'] == 2 and old_ids <= {pdf_fact_id(f) for f in updated.result.facts}
    assert updated.result.review_history == old.result.review_history
    assert all(f.state.value == 'EXTRACTED' for f in updated.result.facts if f.line_code.code == '440')
    assert extend(key, cache, upload_dir=tmp_path)['append_count'] == 0
    conflicting = json.loads(cache.read_text(encoding='utf-8'))
    next(c for c in conflicting['document']['tables'][0]['data']['table_cells']
         if c['start_row_offset_idx'] == 1 and c['start_col_offset_idx'] == 2)['text'] = '101'
    cache.write_text(json.dumps(conflicting), encoding='utf-8')
    with pytest.raises(ValueError, match='conflict'):
        extend(key, cache, upload_dir=tmp_path)


def test_earnings_batch_requires_explicit_rows_and_source_review(tmp_path):
    def earnings_converter(source, output, start, end, ocr):
        payload = converter(source, output, start, end, ocr)
        payload['document']['texts'][0]['text'] = 'B02-DN báo cáo hợp nhất đơn vị: đồng năm 2025'
        cells = payload['document']['tables'][0]['data']['table_cells']
        for cell in cells:
            if cell['start_col_offset_idx'] == 1 and cell['start_row_offset_idx'] > 0:
                cell['text'] = {'270':'10', '300':'11', '400':'20'}[cell['text']]
            if cell['start_col_offset_idx'] == 2 and cell['start_row_offset_idx'] == 3:
                cell['text'] = '40'
        return payload
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=earnings_converter)) as client:
        response = client.post('/v1/documents/ingest-pdf', data={**FORM, 'extended_codes': 'true'}, files={'file': ('source.pdf', source_pdf())})
        assert response.status_code == 200, response.text
        key = response.json()['document_hash']
        queue = client.get(f'/v1/documents/{key}/audit-queue').json()
        group = next(g for g in queue['reconciled_groups'] if g['form_code'] == 'B02-DN' and g['fiscal_year'] == 2025)
        assert group['batch_eligible'] and all(not row['default_selected'] for row in group['pending_rows'])
        assert all('OCR_CONFIDENCE_UNAVAILABLE' in row['warnings'] for row in group['pending_rows'])
        row = group['pending_rows'][0]
        signed = client.post(f'/v1/documents/{key}/audit-queue/batch-confirm', json={
            'form_code': 'B02-DN', 'fiscal_year': 2025, 'reviewer': 'Analyst',
            'reviewed_all_source_pages': True,
            'rows': [{'fact_id': row['fact_id'], 'confirmed_normalized_value': row['value_vnd']}]})
        assert signed.status_code == 200 and signed.json()['signed_count'] == 1


def test_reject_then_correct_retains_ocr_and_unblocks_calculation(tmp_path):
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        response = client.post("/v1/documents/ingest-pdf", data=FORM,
                               files={"file": ("source.pdf", source_pdf(), "application/pdf")})
        key = response.json()["document_hash"]
        row = next(r for r in client.get(f"/v1/documents/{key}/audit-queue").json()["facts"]
                   if r["code"] == "270" and r["fiscal_year"] == 2025)
        reject = {"fact_id": row["fact_id"], "reviewer": "Analyst", "decision": "REJECT",
                  "reviewed_source_and_context": True, "confirmed_normalized_value": row["normalized_value_vnd"],
                  "confirmed_currency": "VND", "confirmed_period": row["period"], "comment": "OCR lost digit"}
        assert client.post(f"/v1/documents/{key}/signoff", json=reject).json()["state"] == "REJECTED_OCR_ERROR"
        report = client.post("/v1/analysis/company-report", json={"document_hashes": [key]})
        assert report.status_code == 200, report.text
        assert not any(m["verification_status"] == "HUMAN_VERIFIED" and
                       any(e["observed_text"] == "100" for e in m["evidences"])
                       for m in report.json()["report"]["metrics"])
        rejected = next(r for r in client.get(f"/v1/documents/{key}/audit-queue").json()["facts"]
                        if r["code"] == "270" and r["fiscal_year"] == 2025)
        correct = {"fact_id": rejected["fact_id"], "reviewer": "Analyst",
                   "reviewed_source_and_context": True, "corrected_normalized_value": "101",
                   "confirmed_currency": "VND", "confirmed_period": rejected["period"],
                   "comment": "Read 101 from source page"}
        result = client.post(f"/v1/documents/{key}/audit-queue/correct", json=correct)
        assert result.status_code == 200, result.text
        assert result.json()["state"] == "HUMAN_CORRECTED"
        queue = client.get(f"/v1/documents/{key}/audit-queue").json()
        updated = next(r for r in queue["facts"] if r["code"] == "270" and r["fiscal_year"] == 2025)
        assert updated["normalized_value_vnd"] == "101" and updated["raw_value"] == "100"
        assert queue["review_history"][-1]["original_ocr_value_vnd"] == "100"
        assert queue["review_history"][-1]["corrected_value_vnd"] == "101"
        answer = client.post("/v1/qa/query", json={"document_hash": key,
                              "question": "Tổng tài sản hợp nhất năm 2025?"})
        assert answer.status_code == 200, answer.text
        assert answer.json()["value"] == "101"
        assert answer.json()["verification_status"] == "VERIFIED"


def test_row_shift_correction_replaces_wrong_bbox_and_keeps_ocr_history(tmp_path):
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        uploaded = client.post("/v1/documents/ingest-pdf", data=FORM,
                               files={"file": ("source.pdf", source_pdf(), "application/pdf")})
        key = uploaded.json()["document_hash"]
        row = next(r for r in client.get(f"/v1/documents/{key}/audit-queue").json()["facts"]
                   if r["code"] == "270" and r["fiscal_year"] == 2025)
        old_bbox = row["evidence"]["locator"]["bounding_box"]
        new_locator = {"source_kind": "pdf", "page_number": 1,
                       "coordinate_origin": "TOPLEFT", "bounding_box": [120, 140, 180, 158]}
        payload = {"fact_id": row["fact_id"], "reviewer": "Analyst",
                   "reviewed_source_and_context": True, "corrected_normalized_value": "101",
                   "confirmed_currency": "VND", "confirmed_period": row["period"],
                   "comment": "OCR row shift: source cell is below", "corrected_locator": new_locator,
                   "corrected_observed_text": "101"}
        invalid = client.post(f"/v1/documents/{key}/audit-queue/correct", json={
            **payload, "corrected_locator": {**new_locator, "bounding_box": [290, 140, 310, 158]}})
        assert invalid.status_code == 422
        response = client.post(f"/v1/documents/{key}/audit-queue/correct", json=payload)
        assert response.status_code == 200, response.text
        queue = client.get(f"/v1/documents/{key}/audit-queue").json()
        corrected = next(r for r in queue["facts"] if r["code"] == "270" and r["fiscal_year"] == 2025)
        assert corrected["evidence"]["locator"]["bounding_box"] == [120, 140, 180, 158]
        assert corrected["evidence"]["observed_text"] == "101"
        assert corrected["raw_value"] == "100"
        event = queue["review_history"][-1]
        assert event["page_bbox_evidence"][0]["locator"]["bounding_box"] == old_bbox
        assert event["corrected_page_bbox_evidence"][0]["locator"]["bounding_box"] == [120, 140, 180, 158]


def test_reconciled_batch_can_sign_selected_rows_and_leave_other_rows_extracted(tmp_path):
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        uploaded = client.post("/v1/documents/ingest-pdf", data=FORM,
                               files={"file": ("source.pdf", source_pdf(), "application/pdf")})
        key = uploaded.json()["document_hash"]
        group = next(g for g in client.get(f"/v1/documents/{key}/audit-queue").json()["reconciled_groups"]
                     if g["fiscal_year"] == 2025)
        chosen = group["pending_rows"][0]
        response = client.post(f"/v1/documents/{key}/audit-queue/batch-confirm", json={
            "form_code": "B01-DN", "fiscal_year": 2025, "reviewer": "Analyst",
            "reviewed_all_source_pages": True,
            "rows": [{"fact_id": chosen["fact_id"],
                      "confirmed_normalized_value": chosen["value_vnd"]}]})
        assert response.status_code == 200, response.text
        assert response.json()["signed_count"] == 1
        assert response.json()["remaining_pending_count"] == 2
        queue = client.get(f"/v1/documents/{key}/audit-queue").json()
        assert sum(r["state"] == "EXTRACTED" and r["fiscal_year"] == 2025 for r in queue["facts"]) == 2
        assert sum(r["state"] == "VERIFIED" and r["fiscal_year"] == 2025 for r in queue["facts"]) == 1


def test_missing_pdf_row_proposal_requires_page_bbox_and_preserves_revision(tmp_path):
    with TestClient(create_app(upload_dir=tmp_path, pdf_converter=converter)) as client:
        response = client.post("/v1/documents/ingest-pdf", data=FORM,
                               files={"file": ("source.pdf", source_pdf(), "application/pdf")})
        key = response.json()["document_hash"]
        period = next(r["period"] for r in client.get(f"/v1/documents/{key}/audit-queue").json()["facts"]
                      if r["fiscal_year"] == 2025)
        payload = {"form_code": "B01-DN", "code": "110", "original_label": "Tiền và tương đương tiền",
                   "period": period, "normalized_value_vnd": "5", "reviewer": "Analyst",
                   "reviewed_source_and_context": True,
                   "locator": {"source_kind": "pdf", "page_number": 1,
                               "bounding_box": [10, 20, 60, 40], "coordinate_origin": "TOPLEFT"},
                   "observed_text": "5", "comment": "Read missing row from the source image"}
        endpoint = f"/v1/documents/{key}/audit-queue/propose-missing"
        assert client.post(endpoint, json={**payload, "locator": {"page_number": 1}}).status_code == 422
        result = client.post(endpoint, json=payload)
        assert result.status_code == 200, result.text
        assert result.json()["state"] == "HUMAN_CORRECTED"
        assert client.post(endpoint, json=payload).status_code == 409
        queue = client.get(f"/v1/documents/{key}/audit-queue").json()
        added = next(r for r in queue["facts"] if r["code"] == "110" and r["fiscal_year"] == 2025)
        assert added["state"] == "HUMAN_CORRECTED" and added["raw_value"] is None
        assert queue["review_history"][-1]["origin"] == "MISSING_OCR_ROW"
