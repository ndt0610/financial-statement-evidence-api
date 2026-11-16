"""Blind runner safety and error accounting with synthetic inputs only."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts.run_holdout_blind import ROOT, run
from scripts.snapshot_project import source_hashes
from scripts.build_blind_query_plan import make_plan
from test_holdout_gate import fixture as gold_fixture


def setup_plan(tmp_path):
    source = tmp_path / "synthetic.pdf"
    source.write_bytes(b"%PDF-1.4 synthetic")
    files = source_hashes(ROOT)
    version = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    plan = {"g1_status": "MANUALLY_APPROVED", "g1_approved_by": "synthetic test",
            "manifest_sha256": "1" * 64, "code_version": version,
            "documents": [{"id": "d1", "company_name": "Test", "company_id": "T", "scope": "SEPARATE",
                           "accounting_regime": "TT200", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                           "local_path": source.name, "fiscal_start": "2025-01-01", "fiscal_end": "2025-12-31",
                           "page_ranges": [{"start": 1, "end": 1, "ocr": False}]}],
            "cases": [{"id": "q1", "document_id": "d1", "question": "Doanh thu thuần năm 2025?"}]}
    return plan, source


def converter(source: Path, output: Path, start: int, end: int, ocr: bool):
    def cell(row, col, text):
        return {"start_row_offset_idx": row, "start_col_offset_idx": col, "text": text,
                "bbox": {"l": 1, "t": 1, "r": 10, "b": 10, "coord_origin": "TOPLEFT"}}
    return {"status": "SUCCESS", "document": {
        "texts": [{"text": "B02-DN báo cáo riêng đơn vị: đồng năm 2025", "prov": [{"page_no": 1}]}],
        "tables": [{"prov": [{"page_no": 1}], "data": {"table_cells": [
            cell(0, 1, "Mã số"), cell(0, 2, "Năm nay"), cell(0, 3, "Năm trước"),
            cell(1, 0, "Doanh thu thuần"), cell(1, 1, "10"), cell(1, 2, "1.000"), cell(1, 3, "900")
        ]}}]}}


def test_blind_runner_refuses_unverified_and_preserves_provenance(tmp_path):
    plan, source = setup_plan(tmp_path)
    result = run(plan, tmp_path, converter)
    answer = result["cases"][0]
    assert answer["status"] == "REFUSED" and answer["value"] is None
    assert answer["verification_status"] == "UNVERIFIED"
    assert answer["automatic_pdf_verified"] is False
    assert answer["evidence"][0]["page"] == 1
    assert result["document_diagnostics"][0]["states"] == ["EXTRACTED"]


def test_blind_runner_refuses_gold_and_missing_gate(tmp_path):
    plan, source = setup_plan(tmp_path)
    plan["g1_status"] = "UNREVIEWED"
    with pytest.raises(ValueError, match="manual approval"):
        run(plan, tmp_path, converter)
    plan["g1_status"] = "MANUALLY_APPROVED"
    plan["cases"][0]["expected"] = {"value": "1000"}
    with pytest.raises(ValueError, match="never include gold"):
        run(plan, tmp_path, converter)


def test_source_and_conversion_errors_are_not_silent_refusals(tmp_path):
    plan, source = setup_plan(tmp_path)
    source.write_bytes(b"altered")
    assert run(plan, tmp_path, converter)["cases"][0]["status"] == "ERROR"
    plan, source = setup_plan(tmp_path)
    result = run(plan, tmp_path, lambda *args: {"status": "PARTIAL_SUCCESS"})
    assert result["cases"][0]["status"] == "ERROR"
    assert result["cases"][0]["automatic_pdf_verified"] is None


def test_query_plan_strips_every_gold_answer_and_demands_frozen_labels():
    manifest, _ = gold_fixture()
    for i, doc in enumerate(manifest["documents"]):
        doc["local_path"] = f"doc{i}.pdf"
    options = {"documents": [{"id": d["id"], "company_name": d["company_id"],
                              "fiscal_start": "2025-01-01", "fiscal_end": "2025-12-31",
                              "page_ranges": [{"start": 1, "end": 2, "ocr": False}]}
                             for d in manifest["documents"]]}
    plan = make_plan(manifest, options, "a" * 64, "G1 reviewer", "b" * 64)
    assert len(plan["cases"]) == 60
    assert all(set(case) == {"id", "document_id", "question"} for case in plan["cases"])
    manifest["status"] = "DRAFT_NOT_FROZEN"
    with pytest.raises(ValueError, match="frozen"):
        make_plan(manifest, options, "a" * 64, "G1 reviewer", "b" * 64)
