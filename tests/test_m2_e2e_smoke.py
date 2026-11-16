"""Exercise all ten M0 contract questions through the real FastAPI boundary."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import ingest_excel
from app.api.main import create_app
from app.models import FinancialFact
from app.services.financial_calculator import (
    VerificationStatus,
    absolute_difference,
    debt_to_equity,
    gross_margin,
    growth_yoy,
    net_margin,
    roa,
    roe,
)
from app.services.question_router import QuestionRoute, route_question


ROOT = Path(__file__).resolve().parents[1]
XLSX = ROOT / "data" / "sample_bctc_2025.xlsx"
GOLD = json.loads((ROOT / "benchmarks" / "m0_smoke_questions.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def result():
    return ingest_excel(XLSX)


@pytest.fixture
def api(tmp_path):
    with TestClient(create_app(upload_dir=tmp_path / "uploads")) as client:
        payload = XLSX.read_bytes()
        response = client.post(
            "/v1/documents/ingest",
            files={"file": ("sample_bctc_2025.xlsx", payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200, response.text
        yield client, response.json()


def fact_at(result, sheet: str, cell: str) -> FinancialFact:
    return next(item for item in result.facts if item.evidence[0].locator.sheet_name == sheet and item.evidence[0].locator.cell_range == cell)


def test_ingest_response_and_hash_index(api):
    client, uploaded = api
    assert uploaded["document_hash"] == hashlib.sha256(XLSX.read_bytes()).hexdigest()
    assert uploaded["fact_count"] == 34
    assert uploaded["note_count"] == 2
    assert uploaded["sheet_count"] == 3
    assert uploaded["states"] == {
        "VERIFIED": 32,
        "UNVERIFIED_MISSING_CACHE": 1,
        "STALE_CACHE_MISMATCH": 1,
    }
    missing = client.post("/v1/qa/query", json={"document_hash": "0" * 64, "question": "Tổng tài sản?"})
    assert missing.status_code == 404


def test_all_ten_gold_questions_match_api_contract(api):
    client, uploaded = api
    route_expected = {
        "Q01": "FACT_LOOKUP", "Q02": "FACT_LOOKUP", "Q03": "FACT_LOOKUP",
        "Q04": "METRIC_COMPUTATION", "Q05": "METRIC_COMPUTATION", "Q06": "METRIC_COMPUTATION",
        "Q07": "HYBRID_EXPLANATION", "Q08": "HYBRID_EXPLANATION",
        "Q09": "REFUSAL", "Q10": "REFUSAL",
    }
    passed = 0
    for question in GOLD["questions"]:
        expected = question["expected"]
        response = client.post("/v1/qa/query", json={"document_hash": uploaded["document_hash"], "question": question["question"]})
        assert response.status_code == 200, (question["id"], response.text)
        actual = response.json()
        assert actual["document_hash"] == uploaded["document_hash"]
        assert actual["route"] == route_expected[question["id"]]
        assert actual["status"] == expected["status"], question["id"]
        assert actual["report_scope"] == expected["report_scope"], question["id"]
        assert actual["unit"] == expected["unit"], question["id"]
        assert (Decimal(actual["value"]) if actual["value"] is not None else None) == (Decimal(expected["value"]) if expected["value"] is not None else None), question["id"]
        for key, value in expected["period"].items():
            assert actual["period"][key] == value, (question["id"], key)
        assert actual["calculation"] == expected.get("calculation"), question["id"]
        locators = [item["locator"] for item in actual["evidences"]]
        assert [{key: locator[key] for key in expected_evidence} for locator, expected_evidence in zip(locators, expected["evidences"])] == expected["evidences"], question["id"]
        assert len(locators) == len(expected["evidences"]), question["id"]
        assert all(item["document_hash"] == uploaded["document_hash"] for item in actual["evidences"])
        if "note_excerpt" in expected:
            assert expected["note_excerpt"] in actual["answer"]
        if question["id"] == "Q02":
            assert actual["verification_status"] == "UNVERIFIED_MISSING_CACHE"
            assert actual["warnings"] == ["UNVERIFIED_MISSING_CACHE"]
        elif question["id"] == "Q06":
            assert actual["verification_status"] == "STALE_CACHE_MISMATCH"
            assert actual["warnings"] == ["STALE_CACHE_MISMATCH"]
        elif actual["status"] == "ANSWERED":
            assert actual["verification_status"] == "VERIFIED"
        else:
            assert actual["verification_status"] == "NOT_APPLICABLE"
        passed += 1
    assert passed == 10


def test_upload_validation_and_idempotent_hash(api):
    client, first = api
    payload = XLSX.read_bytes()
    again = client.post("/v1/documents/ingest", files={"file": ("copy.xlsx", payload)})
    assert again.status_code == 200
    assert again.json()["document_hash"] == first["document_hash"]
    conflicting = client.post("/v1/documents/ingest", files={"file": ("copy.xlsx", payload)}, data={"company_id": "DIFFERENT"})
    assert conflicting.status_code == 409
    bad_extension = client.post("/v1/documents/ingest", files={"file": ("report.txt", payload)})
    assert bad_extension.status_code == 415
    bad_zip = client.post("/v1/documents/ingest", files={"file": ("report.xlsx", b"not a zip")})
    assert bad_zip.status_code == 422
    with TestClient(create_app(upload_dir=client.app.state.upload_dir)) as restarted:
        recovered = restarted.post("/v1/qa/query", json={"document_hash": first["document_hash"], "question": GOLD["questions"][0]["question"]})
        assert recovered.status_code == 200
        assert recovered.json()["value"] == "1200"
        recovered_note = restarted.post("/v1/qa/query", json={"document_hash": first["document_hash"], "question": GOLD["questions"][6]["question"]})
        assert recovered_note.status_code == 200
        assert GOLD["questions"][6]["expected"]["note_excerpt"] in recovered_note.json()["answer"]


def test_router_four_routes_and_scope_year_refusal():
    kwargs = {"available_years": {2024, 2025}, "available_scopes": {"SEPARATE"}}
    assert route_question("Tổng tài sản 2025?", **kwargs).route == QuestionRoute.FACT_LOOKUP
    assert route_question("Tỷ số nợ phải trả trên vốn chủ sở hữu 2025?", **kwargs).route == QuestionRoute.METRIC_COMPUTATION
    assert route_question("Hàng tồn kho 2025, thuyết minh IV.02?", **kwargs).route == QuestionRoute.HYBRID_EXPLANATION
    assert route_question("Tổng tài sản hợp nhất 2025?", **kwargs).route == QuestionRoute.REFUSAL
    assert route_question("Doanh thu thuần 2023?", **kwargs).route == QuestionRoute.REFUSAL


def test_decimal_metrics_period_scope_and_warning_propagation(result):
    assets = fact_at(result, "B01-DN", "D13")
    previous_assets = fact_at(result, "B01-DN", "E13")
    debt = fact_at(result, "B01-DN", "D14")
    equity = fact_at(result, "B01-DN", "D15")
    previous_equity = fact_at(result, "B01-DN", "E15")
    gross_profit = fact_at(result, "B02-DN", "D12")
    missing_revenue = fact_at(result, "B02-DN", "D10")
    income_source = fact_at(result, "B02-DN", "D15")
    income = FinancialFact.model_validate({
        **income_source.model_dump(),
        "line_code": {"accounting_regime": "TT200", "form_code": "B02-DN", "code": "60"},
        "original_label": "Lợi nhuận sau thuế",
    })
    revenue_source = fact_at(result, "B02-DN", "D8")
    revenue = FinancialFact.model_validate({
        **revenue_source.model_dump(),
        "line_code": {"accounting_regime": "TT200", "form_code": "B02-DN", "code": "10"},
        "original_label": "Doanh thu thuần giả định cho kiểm thử calculator",
        "value": "5000", "normalized_value": "5000000000",
    })
    assert absolute_difference(assets, previous_assets, year_over_year=True).value == Decimal("500")
    assert growth_yoy(assets, previous_assets).value.quantize(Decimal("0.01")) == Decimal("14.29")
    assert debt_to_equity(debt, equity).value == Decimal("1.5")
    assert gross_margin(gross_profit, revenue).value == Decimal("38")
    assert net_margin(income, revenue).value == Decimal("20")
    missing_margin = gross_margin(gross_profit, missing_revenue)
    assert missing_margin.verification_status == VerificationStatus.UNVERIFIED_MISSING_CACHE
    assert missing_margin.warnings == ("UNVERIFIED_MISSING_CACHE",)
    assert roa(income, assets, basis="closing").value == Decimal("25")
    average_roa = roa(income, assets, basis="average", opening_assets=previous_assets)
    assert average_roa.value.quantize(Decimal("0.01")) == Decimal("26.67")
    assert "average" in average_roa.calculation
    assert roe(income, equity, basis="closing").value == Decimal("62.5")
    assert roe(income, equity, basis="average", opening_equity=previous_equity).value.quantize(Decimal("0.01")) == Decimal("66.67")
    with pytest.raises(ValueError, match="cross-year"):
        absolute_difference(assets, previous_assets)
    misaligned_prior = FinancialFact.model_validate({**previous_assets.model_dump(), "period": {"period_type": "INSTANT", "as_of_date": "2024-06-30", "fiscal_year_label": "FY2024"}})
    with pytest.raises(ValueError, match="YoY instant dates"):
        growth_yoy(assets, misaligned_prior)
    consolidated = FinancialFact.model_validate({**equity.model_dump(), "document": {**equity.document.model_dump(), "report_scope": "CONSOLIDATED"}})
    with pytest.raises(ValueError, match="separate and consolidated"):
        debt_to_equity(debt, consolidated)
    wrong_date = FinancialFact.model_validate({**assets.model_dump(), "period": {"period_type": "INSTANT", "as_of_date": "2025-06-30", "fiscal_year_label": "FY2025"}})
    with pytest.raises(ValueError, match="closing balance date"):
        roa(income, wrong_date, basis="closing")
    stale_source = fact_at(result, "B01-DN", "D16")
    stale_debt = FinancialFact.model_validate({**stale_source.model_dump(), "line_code": {"accounting_regime": "TT200", "form_code": "B01-DN", "code": "300"}})
    stale_ratio = debt_to_equity(stale_debt, equity)
    assert stale_ratio.verification_status == VerificationStatus.STALE_CACHE_MISMATCH
    assert stale_ratio.warnings == ("STALE_CACHE_MISMATCH",)
