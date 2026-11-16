"""M4a contract: four statements, traceable ratios, and honest insufficiency."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.adapters import ingest_excel
from app.api.main import create_app
from app.models import FactState
from app.services.health_review import review_health
from app.services.qa_service import DocumentIndex
from scripts.build_m4_fixture import build_fixture


@pytest.fixture
def workbook(tmp_path):
    return build_fixture(tmp_path / "health.xlsx")


def _review(path):
    result = ingest_excel(path, accounting_regime="TT200", data_origin="SYNTHETIC")
    return result, review_health(DocumentIndex.from_ingestion(result))


def _change(path, sheet, cell, value):
    book = load_workbook(path)
    book[sheet][cell] = value
    book.save(path)
    book.close()


def test_four_statement_metrics_and_cell_provenance(workbook):
    result, report = _review(workbook)
    assert set(header.form_code for header in result.headers.values()) == {"B01-DN", "B02-DN", "B03-DN", "B09-DN"}
    by_code = {item.code: item for item in report.indicators}
    assert by_code["net_working_capital"].value == Decimal("200000000")
    assert by_code["revenue_growth"].value == Decimal("30")
    assert by_code["trade_receivables_growth"].value == Decimal("116.6666666666666666666666667")
    assert by_code["total_receivables_growth"].value == Decimal("70")
    assert by_code["other_receivables_short_share"].value == Decimal("2")
    assert by_code["other_receivables_total_share"].value == Decimal("6")
    assert by_code["cfo_to_net_income"].value < 0
    assert {(e.locator.sheet_name, e.locator.cell_range) for e in by_code["cfo_to_net_income"].input_evidences} == {
        ("B03-DN", "D8"), ("B02-DN", "D13")
    }
    assert {check.code: check.status.value for check in report.reconciliation_checks} == {
        "balance_sheet": "PASS", "closing_cash_b03_vs_b01": "PASS"
    }
    assert {signal.code for signal in report.review_signals} == {
        "positive_net_income_negative_operating_cash_flow", "trade_receivables_grow_faster_than_revenue"
    }
    assert any(f.line_code.form_code == "B02-DN" and f.line_code.code == "20" for f in result.facts)
    assert any(f.line_code.form_code == "B03-DN" and f.line_code.code == "20" for f in result.facts)


def test_cash_cross_statement_mismatch_keeps_both_evidences(workbook):
    _change(workbook, "B03-DN", "D13", 145)
    result, report = _review(workbook)
    check = next(item for item in report.reconciliation_checks if item.code == "closing_cash_b03_vs_b01")
    assert check.status.value == "WARNING"
    assert check.difference_vnd == Decimal("5000000")
    assert {(e.locator.sheet_name, e.locator.cell_range) for e in check.input_evidences} == {
        ("B03-DN", "D13"), ("B01-DN", "D9")
    }
    cash = next(f for f in result.facts if f.line_code.form_code == "B03-DN" and f.line_code.code == "70" and f.period.fiscal_year_label == "FY2025")
    assert cash.findings[0].code.value == "RECONCILIATION_WARNING"


def test_missing_and_unverified_cfo_cannot_emit_positive_signal(workbook):
    book = load_workbook(workbook)
    del book["B03-DN"]
    book.save(workbook)
    book.close()
    _, report = _review(workbook)
    assert next(item for item in report.indicators if item.code == "cfo_to_net_income").status.value == "INSUFFICIENT_DATA"
    assert any(signal.code == "earnings_cash_flow" and signal.status.value == "INSUFFICIENT_DATA" for signal in report.review_signals)
    build_fixture(workbook)
    _change(workbook, "B03-DN", "D8", "=D9-D10")
    result, report = _review(workbook)
    cfo = next(f for f in result.facts if f.line_code.form_code == "B03-DN" and f.line_code.code == "20" and f.period.fiscal_year_label == "FY2025")
    assert cfo.state == FactState.UNVERIFIED_MISSING_CACHE
    assert next(item for item in report.indicators if item.code == "cfo_to_net_income").status.value == "UNVERIFIED_INPUT"
    assert "positive_net_income_negative_operating_cash_flow" not in {signal.code for signal in report.review_signals}


def test_zero_income_and_mixed_scope_are_handled(workbook):
    _change(workbook, "B02-DN", "D13", 0)
    _, report = _review(workbook)
    assert next(item for item in report.indicators if item.code == "cfo_to_net_income").status.value == "INVALID_DENOMINATOR"
    assert "nonpositive_net_income" in {signal.code for signal in report.review_signals}
    build_fixture(workbook)
    _change(workbook, "B03-DN", "A3", "BÁO CÁO LƯU CHUYỂN TIỀN TỆ HỢP NHẤT")
    with pytest.raises(ValueError, match="exactly one report scope"):
        _review(workbook)


def test_misaligned_cash_flow_period_is_rejected(workbook):
    _change(workbook, "B03-DN", "A4", "Cho năm tài chính kết thúc ngày 30 tháng 9 năm 2025")
    with pytest.raises(ValueError, match="statement periods do not align"):
        _review(workbook)


def test_total_receivables_is_explicit_proxy_when_trade_line_absent(workbook):
    _change(workbook, "B01-DN", "D11", None)
    _change(workbook, "B01-DN", "E11", None)
    _, report = _review(workbook)
    proxy = next(signal for signal in report.review_signals if signal.code == "total_receivables_proxy_grow_faster_than_revenue")
    assert proxy.supporting_indicators == ["total_receivables_growth", "revenue_growth"]
    assert "B01:130" in proxy.explanation


def test_health_api_persists_index_and_returns_structured_review(workbook, tmp_path):
    upload_dir = tmp_path / "uploads"
    with TestClient(create_app(upload_dir=upload_dir)) as client:
        uploaded = client.post("/v1/documents/ingest", files={"file": ("health.xlsx", workbook.read_bytes())},
                               data={"accounting_regime": "TT200", "data_origin": "SYNTHETIC"})
        assert uploaded.status_code == 200, uploaded.text
        document_hash = uploaded.json()["document_hash"]
    with TestClient(create_app(upload_dir=upload_dir)) as client:
        response = client.post("/v1/health-reviews", json={"document_hash": document_hash, "year": 2025})
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["document_metadata"]["document_hash"] == document_hash
        assert report["document_metadata"]["periods_covered"] == ["FY2024", "FY2025"]
        assert report["reconciliation_checks"][1]["status"] == "PASS"
        assert any(item["code"] == "positive_net_income_negative_operating_cash_flow" for item in report["review_signals"])
        unsupported_year = client.post("/v1/health-reviews", json={"document_hash": document_hash, "year": 2024})
        assert unsupported_year.status_code == 422
