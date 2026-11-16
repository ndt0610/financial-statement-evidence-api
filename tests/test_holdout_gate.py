"""Synthetic gate mechanics only; these are not real benchmark results."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_holdout_gate import GROUPS, evaluate


def fixture():
    docs = [{"id": f"d{i}", "company_id": f"company{i}", "sha256": str(i) * 64,
             "pdf_kind": "SCAN" if i == 0 else "NATIVE", "source_url": "https://example.org/report.pdf",
             "rights": "test", "scope": "SEPARATE", "fiscal_year": 2025, "accounting_regime": "TT200"}
            for i in range(3)]
    cases, predictions = [], []
    for group in sorted(GROUPS):
        for i in range(12):
            cid = f"{group}-{i}"
            if group == "TRAP":
                expected = {"disposition": "REFUSE", "reason": "UNVERIFIED"}
                answer = {"id": cid, "status": "REFUSED", "value": None, "verification_status": "UNVERIFIED"}
            else:
                evidence = [{"page": 2, "bbox": ["1", "2", "11", "12"]}]
                if group in {"METRIC", "HYBRID"}:
                    evidence.append({"page": 3, "bbox": ["1", "2", "11", "12"]})
                expected = {"disposition": "ANSWER", "value": "1000", "unit": "VND", "period": "FY2025",
                            "scope": "SEPARATE", "evidence": evidence}
                if group == "METRIC": expected["independent_calculation"] = "600 + 400"
                answer = {"id": cid, "status": "ANSWERED", "value": "1000.0", "unit": "VND",
                          "period": "FY2025", "scope": "SEPARATE", "evidence": copy.deepcopy(evidence)}
            answer["automatic_pdf_verified"] = False
            cases.append({"id": cid, "document_id": f"d{i % 3}", "group": group, "question": cid, "expected": expected})
            predictions.append(answer)
    return ({"status": "FROZEN_REVIEWED", "labeler": "A", "reviewer": "B", "frozen_at": "2026-01-01T00:00:00+00:00",
             "documents": docs, "cases": cases},
            {"code_version": "synthetic-test", "run_at": "2026-02-01T00:00:00+00:00", "cases": predictions})


def test_pass_and_numeric_denominator_is_all_expected_answers():
    gold, predictions = fixture()
    result = evaluate(gold, predictions)
    assert result["passed"] and result["metrics"]["numeric"] == 48 and result["metrics"]["refusals"] == 12
    for answer in predictions["cases"][:5]:
        answer["value"] = None
    result = evaluate(gold, predictions)
    assert not result["passed"] and result["metrics"]["numeric_correct"] == 43


def test_evidence_and_unsafe_refusal_fail_gate():
    gold, predictions = fixture()
    for answer in predictions["cases"][:5]:
        answer["evidence"] = [{"page": 2, "bbox": ["200", "200", "210", "210"]}]
    assert not evaluate(gold, predictions)["passed"]
    gold, predictions = fixture()
    trap = next(case for case in predictions["cases"] if case["status"] == "REFUSED")
    trap["value"] = "0"
    assert not evaluate(gold, predictions)["passed"]
    trap["value"] = None
    trap["automatic_pdf_verified"] = True
    assert not evaluate(gold, predictions)["passed"]


@pytest.mark.parametrize("change", ["missing_prediction", "duplicate_prediction", "same_reviewer", "short_group", "time", "missing_state"])
def test_invalid_holdout_is_not_a_pass(change):
    gold, predictions = fixture()
    if change == "missing_prediction": predictions["cases"].pop()
    if change == "duplicate_prediction": predictions["cases"].append(predictions["cases"][0])
    if change == "same_reviewer": gold["reviewer"] = gold["labeler"]
    if change == "short_group": gold["cases"].pop()
    if change == "time": predictions["run_at"] = gold["frozen_at"]
    if change == "missing_state": del predictions["cases"][0]["automatic_pdf_verified"]
    assert not evaluate(gold, predictions)["passed"]


def test_development_documents_cannot_be_relabelled_as_holdout():
    gold, predictions = fixture()
    gold["documents"][0]["sha256"] = "cb390ca3020463661fe79fd653e7c5ba5d270221b1eeaed08c6978fc35993627"
    result = evaluate(gold, predictions)
    assert not result["passed"]
    assert any("development source" in error for error in result["errors"])


def test_missing_input_cli_fails_closed(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_holdout_gate.py"
    output = tmp_path / "result.json"
    command = [sys.executable, str(script), str(tmp_path / "missing.json"), str(tmp_path / "predictions.json"), "--output", str(output)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is False


def test_cli_checks_exact_manifest_and_source_hashes(tmp_path):
    gold, predictions = fixture()
    for i, doc in enumerate(gold["documents"]):
        path = tmp_path / f"doc{i}.pdf"
        path.write_bytes(f"synthetic-{i}".encode())
        doc["local_path"] = path.name
        doc["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(gold), encoding="utf-8")
    predictions["manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    pred = tmp_path / "pred.json"
    pred.write_text(json.dumps(predictions), encoding="utf-8")
    output = tmp_path / "result.json"
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_holdout_gate.py"
    command = [sys.executable, str(script), str(manifest), str(pred), "--output", str(output)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    (tmp_path / "doc0.pdf").write_bytes(b"changed")
    assert subprocess.run(command, capture_output=True).returncode == 1
