"""VHC net debt stays incomplete until every cash and debt component is sourced."""
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app


ROOT = Path(__file__).resolve().parents[1]
VHC = "89f6237db1157d206eb26a56f4faddaa634034d5a88e11966fef64bb0eda6549"


def test_b01_123_signoff_alone_cannot_certify_net_debt(tmp_path):
    old_index = ROOT / "data/uploads/p8_signoff_backups_20261111" / f"{VHC}.index.json"
    originals = [ROOT / "data/uploads" / f"{VHC}.pdf",
                 old_index if old_index.is_file() else ROOT / "data/uploads" / f"{VHC}.index.json"]
    if any(not p.is_file() for p in originals):
        pytest.skip("VHC real PDF index not present locally")
    for source in originals:
        shutil.copy2(source, tmp_path / source.name)
    with TestClient(create_app(upload_dir=tmp_path)) as client:
        queue = client.get(f"/v1/documents/{VHC}/audit-queue").json()
        assert [(r["code"], r["fiscal_year"]) for r in queue["facts"][:5]] == [
            ("123", 2024), ("320", 2024), ("110", 2023), ("123", 2023), ("320", 2023)]
        assert not any(r["code"] == "110" and r["fiscal_year"] == 2024 for r in queue["facts"])
        row = next(r for r in queue["facts"] if r["code"] == "123" and r["fiscal_year"] == 2024)
        review = {"fact_id": row["fact_id"], "reviewer": "Test reviewer", "decision": "CONFIRM",
                  "reviewed_source_and_context": True,
                  "confirmed_normalized_value": row["normalized_value_vnd"],
                  "confirmed_currency": row["currency"], "confirmed_period": row["period"]}
        assert client.post(f"/v1/documents/{VHC}/signoff", json=review).status_code == 200
        report = client.post("/v1/analysis/company-report", json={"document_hashes": [VHC]})
        assert report.status_code == 200, report.text
        d04 = next(m for m in report.json()["report"]["metrics"]
                   if m["metric_id"] == "D04" and m["fiscal_year"] == 2024)
        assert d04["status"] == "MISSING_INPUT" and d04["reason"] == "cash@2024"


def test_signed_vhc_debt_inputs_unlock_cash_only_proxy_with_pdf_evidence():
    if not (ROOT / "benchmarks/p8_signoff_20261111.json").is_file():
        pytest.skip("P8 owner sign-off has not yet been applied")
    with TestClient(create_app(upload_dir=ROOT / "data/uploads")) as client:
        queue = client.get(f"/v1/documents/{VHC}/audit-queue").json()
        rows = {(r["code"], r["fiscal_year"]): r for r in queue["facts"] if r["form_code"] == "B01-DN"}
        assert {code: rows[(code, 2024)]["state"] for code in ("110", "123", "320", "338")} == {
            "110": "HUMAN_CORRECTED", "123": "VERIFIED", "320": "VERIFIED", "338": "HUMAN_CORRECTED"}
        report = client.post("/v1/analysis/company-report", json={"document_hashes": [VHC]})
        assert report.status_code == 200, report.text
        d04 = next(m for m in report.json()["report"]["metrics"]
                   if m["metric_id"] == "D04" and m["fiscal_year"] == 2024)
        assert d04["status"] == "PROXY" and d04["value"] == "1707094406653"
        assert {e["locator"]["page_number"] for e in d04["evidences"]} == {64}
