"""Tender rules and API use explicit thresholds and verified evidence."""
from decimal import Decimal
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.adapters import ingest_excel
from app.api.main import create_app
from app.services.qa_service import DocumentIndex
from app.services.tender_review import TenderCriteriaConfig, review_tender
from app.adapters.pdf_adapter import PdfIngestionResult
from app.models import FinancialFact
from scripts.build_m4_fixture import build_fixture


@pytest.fixture
def index(tmp_path):
    path = build_fixture(tmp_path / "financial.xlsx")
    return DocumentIndex.from_ingestion(ingest_excel(path, accounting_regime="TT200", data_origin="SYNTHETIC", company_id="TEST"))


def config(criteria):
    return TenderCriteriaConfig(company_id="TEST", report_scope="SEPARATE", criteria_source="example solicitation", criteria=criteria)


def criterion(metric, minimum="0", **extra):
    return {"id": metric, "metric": metric, "years": [2025], "minimum": minimum, **extra}


def test_metrics_thresholds_and_evidence(index):
    criteria = [criterion("NET_ASSETS"), criterion("WORKING_CAPITAL", "200000000"), criterion("CURRENT_RATIO", "1"),
                criterion("NET_INCOME"), criterion("CFO"), criterion("AVERAGE_REVENUE", revenue_code="10")]
    result = review_tender([index], config(criteria))
    by_code = {item.metric: item for item in result.findings}
    assert by_code["WORKING_CAPITAL"].value == Decimal("200000000")
    assert by_code["WORKING_CAPITAL"].status == "PASS"
    assert by_code["CFO"].status == "FAIL"
    assert all(item.evidences and item.value is not None for item in result.findings)
    strict = review_tender([index], config([criterion("WORKING_CAPITAL", "200000000", operator=">")]))
    assert strict.findings[0].status == "FAIL"


def test_cfo_to_net_income_threshold_uses_two_verified_source_lines(index):
    finding = review_tender([index], config([criterion("CFO_TO_NET_INCOME", "0")])).findings[0]
    assert finding.status == "FAIL"
    assert finding.unit == "%"
    assert finding.value is not None and finding.value < 0
    assert finding.formula == "B03:20 / B02:60 × 100"
    assert len(finding.evidences) >= 2


def test_missing_scope_year_and_duplicate_are_needs_review(index):
    assert review_tender([index], config([criterion("NET_ASSETS", years=[2023])])).findings[0].status == "NEEDS_REVIEW"
    assert review_tender([index, index], config([criterion("NET_ASSETS")])).findings[0].status == "NEEDS_REVIEW"
    wrong = config([criterion("NET_ASSETS")]).model_copy(update={"report_scope": "CONSOLIDATED"})
    assert review_tender([index], wrong).findings[0].status == "NEEDS_REVIEW"


def test_config_rejects_ambiguous_revenue_and_duplicate_years():
    with pytest.raises(ValueError):
        config([criterion("AVERAGE_REVENUE")])
    with pytest.raises(ValueError):
        config([criterion("AVERAGE_REVENUE", revenue_code="10", years=[2025, 2025])])


def test_unverified_inputs_cannot_pass_or_fail(index):
    changed = []
    for fact in index.result.facts:
        data = fact.model_dump()
        if fact.line_code.form_code == "B01-DN" and fact.line_code.code == "100" and fact.period.fiscal_year_label == "FY2025":
            data.update(state="EXTRACTED", verification=None, findings=[])
        changed.append(FinancialFact.model_validate(data))
    incomplete = DocumentIndex.from_ingestion(PdfIngestionResult(document_hash=index.result.document_hash, facts=changed))
    finding = review_tender([incomplete], config([criterion("WORKING_CAPITAL")])).findings[0]
    assert finding.status == "NEEDS_REVIEW" and finding.value is None and finding.reason == "UNVERIFIED_OR_INVALID_INPUT"


def test_zero_debt_does_not_generate_a_ratio(index):
    changed = []
    for fact in index.result.facts:
        data = fact.model_dump()
        if fact.line_code.form_code == "B01-DN" and fact.line_code.code == "310" and fact.period.fiscal_year_label == "FY2025":
            data.update(value=Decimal(0), normalized_value=Decimal(0), raw_value="0", findings=[])
        changed.append(FinancialFact.model_validate(data))
    changed_index = DocumentIndex.from_ingestion(PdfIngestionResult(document_hash=index.result.document_hash, facts=changed))
    result = review_tender([changed_index], config([criterion("CURRENT_RATIO")])).findings[0]
    assert result.status == "NEEDS_REVIEW" and result.reason == "INVALID_DENOMINATOR" and result.value is None


def yearly_index(index, year, *, scope="SEPARATE", bad_revenue=False):
    facts = []
    for fact in index.result.facts:
        if fact.period.fiscal_year_label != "FY2025":
            continue
        data = fact.model_dump()
        data["document"].update(fiscal_year_label=f"FY{year}", fiscal_period_start=date(year, 1, 1),
                                fiscal_period_end=date(year, 12, 31), report_scope=scope)
        data["period"]["fiscal_year_label"] = f"FY{year}"
        if data["period"]["period_type"] == "INSTANT":
            data["period"]["as_of_date"] = date(year, 12, 31)
        else:
            data["period"]["period_start"] = date(year, 1, 1)
            data["period"]["period_end"] = date(year, 12, 31)
        if bad_revenue and fact.line_code.form_code == "B02-DN" and fact.line_code.code == "10":
            data.update(state="EXTRACTED", verification=None, findings=[])
        facts.append(FinancialFact.model_validate(data))
    return DocumentIndex.from_ingestion(PdfIngestionResult(document_hash=index.result.document_hash, facts=facts))


def test_three_year_average_and_missing_scope_or_unverified(index):
    docs = [yearly_index(index, year) for year in (2023, 2024, 2025)]
    test = config([criterion("AVERAGE_REVENUE", revenue_code="10", years=[2023, 2024, 2025])])
    result = review_tender(docs, test).findings[0]
    assert result.status == "PASS" and len(result.evidences) == 3
    assert review_tender(docs[:2], test).findings[0].status == "NEEDS_REVIEW"
    mixed = [docs[0], yearly_index(index, 2024, scope="CONSOLIDATED"), docs[2]]
    assert review_tender(mixed, test).findings[0].status == "NEEDS_REVIEW"
    bad = [docs[0], yearly_index(index, 2024, bad_revenue=True), docs[2]]
    assert review_tender(bad, test).findings[0].status == "NEEDS_REVIEW"


@pytest.mark.parametrize("state", ["UNVERIFIED_MISSING_CACHE", "STALE_CACHE_MISMATCH"])
def test_bad_excel_formula_cache_is_never_used(index, state):
    changed = []
    for fact in index.result.facts:
        data = fact.model_dump()
        if fact.line_code.form_code == "B01-DN" and fact.line_code.code == "100" and fact.period.fiscal_year_label == "FY2025":
            data["evidence"][0]["locator"]["formula"] = "=D1+D2"
            data["state"] = state
            if state == "UNVERIFIED_MISSING_CACHE":
                data.update(value=None, normalized_value=None,
                            verification={"source_checked": True, "cache_present": False})
            else:
                data["verification"] = {"source_checked": True, "cache_present": True, "independent_formula_match": False}
        changed.append(FinancialFact.model_validate(data))
    altered = DocumentIndex.from_ingestion(PdfIngestionResult(document_hash=index.result.document_hash, facts=changed))
    result = review_tender([altered], config([criterion("WORKING_CAPITAL")])).findings[0]
    assert result.status == "NEEDS_REVIEW" and result.value is None


def test_api_review_and_hash_validation(index, tmp_path):
    app = create_app(upload_dir=tmp_path)
    key = index.result.document_hash
    app.state.documents[key] = index
    with TestClient(app) as client:
        body = {"document_hashes": [key], "config": config([criterion("WORKING_CAPITAL")]).model_dump(mode="json")}
        result = client.post("/v1/tender-reviews", json=body)
        assert result.status_code == 200, result.text
        assert result.json()["findings"][0]["status"] == "PASS"
        assert client.post("/v1/tender-reviews", json={**body, "document_hashes": ["bad"]}).status_code == 422
