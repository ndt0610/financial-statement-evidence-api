"""T01–T25 independent claim traps and the 16 prohibited conclusions."""

from decimal import Decimal

import pytest

from app.services.conclusion_guard import ClaimContext, guard_conclusion
from app.models import Evidence, PDFLocator, ReportScope
from app.services.analysis_book import AnalysisBook, SourceDatum
from app.services.analysis_calculator import calculate_metric, calculate_supplements
from app.services.full_analysis_report import build_full_report
from app.services.interpretation_engine import FORBIDDEN_CONCLUSION_KEYS, interpret
from p5_fixture import pnj_book


TRAPS = [
    ("T01","INSOLVENT_FROM_CR",{"cr_now":"0.9"},{},"RATIO_NOT_PAYMENT_FORECAST"),
    ("T02","LIQUIDITY_IMPROVED",{"cr_before":"1.2","cr_now":"2.0","qr_before":"0.8","qr_now":"0.5"},{"cash_access_checked":True},"CHECK_QUICK_RATIO_AND_ACCESS"),
    ("T03","ALL_CASH_FREE",{"restricted_cash":"20"},{"cash_restriction_disclosure_checked":True},"RESTRICTED_CASH_UNKNOWN_OR_POSITIVE"),
    ("T04","CANNOT_PAY_INTEREST_FROM_ICR",{"icr":"0.8"},{},"ICR_NOT_PAYMENT_TEST"),
    ("T05","INFINITE_ICR",{"interest":"0"},{},"ZERO_INTEREST_IS_NOT_APPLICABLE"),
    ("T06","COVENANT_BREACH",{"dscr":"1.1","covenant_min":"1.25"},{"same_contract_definition":True,"waiver_checked":True,"waiver_exists":True},"NEED_CONTRACT_AND_WAIVER"),
    ("T07","LOW_LEVERAGE_FROM_NEGATIVE_MULTIPLE",{"ebitda":"-100","net_debt_multiple":"-3"},{"peer_or_contract_target":True},"NEGATIVE_EBITDA_INVALID_MULTIPLE"),
    ("T08","HEALTHY_ROE",{"net_income":"-20","equity":"-100"},{"roe_context_checked":True},"LOSS_AND_NEGATIVE_EQUITY_TRAP"),
    ("T09","ROE_GAIN_IS_OPERATIONAL",{"roe_before":"10","roe_now":"15"},{"equity_base_checked":False},"CHECK_DUPONT_AND_EQUITY_BASE"),
    ("T10","PARENT_ROE",{"net_income_parent":"10"},{"parent_earnings":True,"parent_equity":False},"PARENT_SCOPE_MISMATCH"),
    ("T11","RECURRING_EARNINGS",{"ni":"100","asset_sale_gain":"90"},{"nonrecurring_items_quantified":True,"recurring_profit_positive":False},"NONRECURRING_GAIN_NOT_REMOVED"),
    ("T12","ORGANIC_GROWTH",{"revenue_growth":"20"},{"acquisition_impact_quantified":False},"ACQUISITION_EFFECT_UNKNOWN"),
    ("T13","CASH_QUALITY_IMPROVED",{"cfo_growth":"50","payables_effect":"80"},{"payables_effect_removed":False},"PAYABLES_MAY_DRIVE_CFO"),
    ("T14","OBSOLETE_INVENTORY",{"inventory_growth":"30"},{"inventory_aging_or_writeoff_evidence":False},"VALUE_GROWTH_NOT_OBSOLESCENCE"),
    ("T15","OVERDUE_RECEIVABLES",{"dso":"90"},{"aging_schedule_evidence":False},"DSO_NOT_AGING"),
    ("T16","CAPEX_REDUCED_CFO",{"cfo_growth":"-10","capex":"100"},{},"CAPEX_IS_INVESTING_CASH_FLOW"),
    ("T17","OPERATING_CASH_GROWTH",{"cash_growth":"100","cfo_before":"20","cfo_now":"10"},{},"CASH_BALANCE_MAY_BE_FINANCED"),
    ("T18","STANDARD_YOY_GROWTH",{"base":"-10","current":"5"},{"comparable_periods":True},"NONPOSITIVE_OR_NONCOMPARABLE_BASE"),
    ("T19","PERCENTAGE_POINT_CHANGE",{"before_pct":"5","after_pct":"6","stated_pp":"20"},{},"PERCENT_VS_PERCENTAGE_POINTS"),
    ("T20","YOY_COMPARABLE",{"months_before":"12","months_now":"9"},{"same_period_length":False,"same_scope":True},"DURATION_OR_SCOPE_MISMATCH"),
    ("T21","B01_INVENTORY_EQUALS_B03_CASH_EFFECT",{"balance_change":"100","cash_effect":"-90"},{},"BALANCE_NOT_CASH_FLOW"),
    ("T22","SECTOR_BENCHMARK",{"peer_count":"2"},{"peer_comparability_checked":True},"INSUFFICIENT_PEER_SET"),
    ("T23","HEALTHY_FROM_CLEAN_AUDIT",{"ni":"-10","cfo":"-5"},{"clean_opinion":True},"AUDIT_OPINION_NOT_HEALTH_RATING"),
    ("T24","SUSTAINABLE_CFO_CONVERSION",{"years_observed":"1"},{"multi_year_bridge_checked":False},"ONE_YEAR_NOT_SUSTAINABILITY"),
    ("T25","INVENTORY_TURNOVER_IMPROVED",{"gross_dio_before":"365","gross_dio_now":"365","net_dio_before":"365","net_dio_now":"328.5"},{"provision_effect_checked":True},"NET_DIO_MAY_FALL_FROM_PROVISION"),
]


@pytest.mark.parametrize("trap_id,claim,numbers,flags,expected_reason",TRAPS,ids=[x[0] for x in TRAPS])
def test_independent_trap_refuses_unsupported_claim(trap_id,claim,numbers,flags,expected_reason):
    context=ClaimContext(numbers={k:Decimal(v) for k,v in numbers.items()},flags=flags)
    decision=guard_conclusion(claim,context)
    assert decision.conclusion_key==claim
    assert not decision.allowed and decision.reason_code==expected_reason


@pytest.mark.parametrize("claim,numbers,flags",[
    ("COVENANT_BREACH",{"dscr":"1.1","covenant_min":"1.25"},{"same_contract_definition":True,"waiver_checked":True,"waiver_exists":False}),
    ("PERCENTAGE_POINT_CHANGE",{"before_pct":"5","after_pct":"6","stated_pp":"1"},{}),
    ("INVENTORY_TURNOVER_IMPROVED",{"gross_dio_before":"200","gross_dio_now":"180","net_dio_before":"195","net_dio_now":"175"},{"provision_effect_checked":True}),
    ("SECTOR_BENCHMARK",{"peer_count":"7"},{"peer_comparability_checked":True}),
])
def test_guard_allows_a_supported_narrow_claim(claim,numbers,flags):
    assert guard_conclusion(claim,ClaimContext({k:Decimal(v) for k,v in numbers.items()},flags)).allowed


def test_pnj_findings_do_not_emit_sixteen_forbidden_claims():
    report=build_full_report(pnj_book(),company="PNJ")
    assert len(FORBIDDEN_CONCLUSION_KEYS)==16
    assert {f.conclusion_key for f in report.findings}.isdisjoint(FORBIDDEN_CONCLUSION_KEYS)
    latest={f.conclusion_key for f in report.findings if f.fiscal_year==2025}
    assert {"MARGIN_OFFSETS_REVENUE_DECLINE","DUPONT_MARGIN_LED_ROE","LOW_CASH_CONVERSION_MULTI_YEAR",
            "CFO_INVENTORY_CASH_USE","RAW_MATERIALS_UP_FINISHED_GOODS_DOWN",
            "LIQUIDITY_COMPONENTS_DIVERGE","RENT_AND_INTEREST_COVERAGE_HISTORICAL",
            "ASSETS_EQUAL_FUNDING_SOURCES"}.issubset(latest)
    assert all(f.evidences and f.verification_status=="analyst_transcribed_pending_separate_signoff" for f in report.findings)


def test_profit_interpretation_does_not_claim_bridge_reconciled_when_it_warns():
    book = pnj_book()
    report = build_full_report(book, company="PNJ")
    bridges = [b.model_copy(update={"status": "RECONCILIATION_WARNING"})
               if b.bridge_id == "PROFIT" and b.fiscal_year == 2025 else b
               for b in report.bridges]
    findings = interpret(book, report.metrics, bridges)
    finding = next(f for f in findings if f.conclusion_key == "MARGIN_OFFSETS_REVENUE_DECLINE"
                   and f.fiscal_year == 2025)
    assert "còn phần chưa đối chiếu" in finding.narrative_vi
    assert "đã khớp các cấu phần" not in finding.narrative_vi


def test_t25_engine_emits_counter_finding_on_provision_only_dio_drop():
    evidence=Evidence(document_hash="b"*64,locator=PDFLocator(page_number=1,table_id="SYNTHETIC"))
    values=[("inventory_gross",2023,100),("inventory_gross",2024,100),("inventory_gross",2025,100),
            ("inventory",2023,100),("inventory",2024,100),("inventory",2025,80),
            ("inventory_provision",2023,0),("inventory_provision",2024,0),("inventory_provision",2025,20),
            ("cogs",2024,100),("cogs",2025,100)]
    book=AnalysisBook([SourceDatum(name,year,Decimal(value),(evidence,),"VERIFIED","OTHER_CO",ReportScope.CONSOLIDATED)
                       for name,year,value in values],analysis_years=[2024,2025])
    metrics=[calculate_metric(book,"E02",year) for year in (2024,2025)]
    metrics += [next(x for x in calculate_supplements(book,year) if x.metric_id=="DIO_GROSS") for year in (2024,2025)]
    metrics += [calculate_metric(book,"Q03",2025)]
    findings=interpret(book,metrics,[])
    keys={x.conclusion_key for x in findings}
    assert "NET_DIO_DROP_PROVISION_TRAP" in keys
    assert "HISTORICAL_INVENTORY_TURNOVER_IMPROVED" not in keys
