from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.main import create_app
from scripts.build_m4_fixture import build_fixture


def test_full_report_api_uses_index_and_preserves_cells(tmp_path):
    workbook=build_fixture(tmp_path/"fixture.xlsx")
    with TestClient(create_app(upload_dir=tmp_path/"uploads")) as client:
        ingested=client.post("/v1/documents/ingest",files={"file":("fixture.xlsx",workbook.read_bytes())},
                             data={"accounting_regime":"TT200","data_origin":"SYNTHETIC"})
        assert ingested.status_code==200,ingested.text
        key=ingested.json()["document_hash"]
        response=client.post("/v1/financial-analysis/full-report",json={"document_hashes":[key]})
        assert response.status_code==200,response.text
        report=response.json()
        assert report["coverage_by_year"]["2025"]
        assert sum(report["coverage_by_year"]["2025"].values())==52
        revenue=next(m for m in report["metrics"] if m["metric_id"]=="G02" and m["fiscal_year"]==2025)
        assert Decimal(revenue["value"])==Decimal("30")
        assert {e["locator"]["cell_range"] for e in revenue["evidences"]}=={"D10","E10"}
        assert revenue["verification_status"]=="VERIFIED"
        assert {b["bridge_id"] for b in report["bridges"]}=={"PROFIT","CFO","FUNDING","CASH"}
        assert all("conclusion_key" in finding and "counter_evidence_checked" in finding for finding in report["findings"])
        wrong=client.post("/v1/financial-analysis/full-report",json={"document_hashes":[key],"years":[2027]})
        assert wrong.status_code==422
