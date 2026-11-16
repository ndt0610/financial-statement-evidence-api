"""P5 gold N01–N22 and provenance traps, independent of product implementation."""

import json
from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.models import Evidence, PDFLocator, ReportScope
from app.services.analysis_book import AnalysisBook, SourceDatum, weakest
from app.services.analysis_calculator import calculate_all, calculate_dupont, calculate_metric, calculate_supplements
from app.services.financial_bridges import calculate_bridges, profit_bridge
from app.services.full_analysis_report import build_full_report
from p5_fixture import ROOT, pnj_book


def near(actual, expected):
    assert actual is not None and abs(actual-D(str(expected))) < D("0.0000000001")


@pytest.fixture(scope="module")
def case():
    book=pnj_book()
    report=build_full_report(book,company="PNJ")
    metrics={(m.metric_id,m.fiscal_year):m for m in report.metrics}
    bridges={(b.bridge_id,b.fiscal_year):b for b in report.bridges}
    return book,report,metrics,bridges


@pytest.mark.parametrize("gold_id",[f"N{i:02d}" for i in range(1,23)])
def test_pnj_gold_contract(gold_id,case):
    b,r,m,bridges=case
    v=lambda c,y:b.get(c,y).value_vnd
    val=lambda c,y:m[c,y].value
    if gold_id=="N01":assert v("revenue",2024)==D("37822837171385")
    elif gold_id=="N02":assert v("revenue",2025)==D("34976042929392")
    elif gold_id=="N03":near(val("G02",2025),"-7.526654410121174514788")
    elif gold_id=="N04":near((v("net_income",2025)/v("net_income",2024)-1)*100,(D("2828497462890")/D("2112916282946")-1)*100)
    elif gold_id=="N05":
        near(val("P01",2024),D("6673502640192")/D("37822837171385")*100)
        near(val("P01",2025),D("7683848308014")/D("34976042929392")*100)
    elif gold_id=="N06":assert (v("cfo",2024),v("cfo",2025))==(D("83185174755"),D("18890403841"))
    elif gold_id=="N07":
        near(val("C01",2024),v("cfo",2024)/v("net_income",2024)*100)
        near(val("C01",2025),v("cfo",2025)/v("net_income",2025)*100)
    elif gold_id=="N08":assert (v("cf_inventory",2025),v("cf_payables",2025),v("pre_wc",2025))==(D("-2806722790389"),D("-369026498161"),D("3631839011356"))
    elif gold_id=="N09":assert v("inventory",2025)-v("inventory",2024)==D("2820164052526")
    elif gold_id=="N10":near(val("E02",2024),"140.355800709045");near(val("E02",2025),"192.920255657281")
    elif gold_id=="N11":near(val("L01",2025),"2.70776375184933784");near(val("L02",2025),"0.083031598012391609")
    elif gold_id=="N12":assert v("borrowings",2025)==D("4223385635653")
    elif gold_id=="N13":near(val("D06",2025),"30.79309748730506899")
    elif gold_id=="N14":assert v("cash",2025)-v("cash",2024)==D("-600687135099")
    elif gold_id=="N15":assert v("raw_materials",2025)-v("raw_materials",2024)==D("4185854748957")
    elif gold_id=="N16":assert bridges["CASH",2025].unexplained_residual==0
    elif gold_id=="N17":
        gold=json.loads((ROOT/"independent_calculations.json").read_text(encoding="utf-8"))["dupont"]
        for year in range(2021,2026):
            d=calculate_dupont(b,year)
            for key,reference in (("net_margin",gold[str(year)]["3_step"]["net_margin"]),
                                  ("asset_turnover",gold[str(year)]["3_step"]["asset_turnover"]),
                                  ("equity_multiplier",gold[str(year)]["3_step"]["equity_multiplier"]),
                                  ("tax_burden",gold[str(year)]["5_step"]["tax_burden"])):
                near(d[key].value,reference)
            near(d["net_margin"].value*d["asset_turnover"].value*d["equity_multiplier"].value,d["roe"].value)
            near(d["tax_burden"].value*d["earnings_burden"].value*d["ebit_margin"].value*d["asset_turnover"].value*d["equity_multiplier"].value,d["roe"].value)
    elif gold_id=="N18":
        br=bridges["PROFIT",2025];assert br.unexplained_residual==0 and br.target_delta==D("715581179944")
        assert {s.code:s.signed_impact for s in br.steps}["deferred_tax"]==D("52934223733")
        assert br.memo_steps[0].raw_change==D("72307293498")
        assert all(s.raw_change is not None and s.signed_impact is not None for s in br.steps)
    elif gold_id=="N19":
        br=bridges["FUNDING",2025];assert br.unexplained_residual==0 and br.target_delta==D("2955899469356")
        assert {s.code:s.signed_impact for s in br.steps}=={"liabilities":D("936292640466"),"equity":D("2019606828890")}
        assert br.component_residuals=={"unclassified_assets":D("-152977448071"),"unclassified_liabilities":D("15858381944"),"unclassified_equity":D(0)}
        assert len(br.equity_components)==5 and sum((s.signed_impact for s in br.equity_components),D(0))==D("2019606828890")
    elif gold_id=="N20":
        near(val("D03",2025),"31.81478846220720264");near(val("ID_A",2025),"20.94556180563159838")
        assert val("D04",2025)==D("3701360378622") and val("NET_DEBT_ALL_TERM_SCENARIO",2025)==D("1791790378622")
        assert "conditional" in m["NET_DEBT_ALL_TERM_SCENARIO",2025].formula
    elif gold_id=="N21":
        assert v("store_rent",2025)==D("446745615383")
        assert v("rent_commitment_next_12m",2025)==D("439511473672")
        near(val("FCCR_ACCRUAL",2025),"7.270170695905204200")
        assert m["D09",2025].status=="MISSING_INPUT"
    elif gold_id=="N22":
        near(val("DIO_GROSS",2024),"140.72624260062711");near(val("DIO_GROSS",2025),"193.62499832810394")
        assert val("DIO_GROSS",2025)>val("E02",2025)


def test_coverage_and_weakest_link(case):
    b,r,m,_=case
    assert len([x for x in r.metrics if x.metric_id in {f"{prefix}{i:02d}" for prefix,n in (("G",7),("L",7),("D",9),("E",7),("P",9),("C",8),("Q",5)) for i in range(1,n+1)}])==260
    assert all(sum(c.values())==52 for c in r.coverage_by_year.values())
    roa=m["P05",2025]
    assert {"assets@2024","assets@2025","net_income@2025"}.issubset(roa.inputs)
    assert {e.locator.region for e in roa.evidences} >= {"mã 270; cột 2024","mã 270; cột 2025","mã 60; cột 2025"}
    assert roa.verification_status=="analyst_transcribed_pending_separate_signoff"
    assert all(f.verification_status!="HUMAN_VERIFIED" for f in r.findings)


def test_negative_expense_presentation_preserves_profit_bridge(case):
    b,_,_,_=case
    datums=[replace(d,value_vnd=-d.value_vnd) if d.concept in {"selling","admin","finance_expense"} and d.value_vnd is not None else d for d in b.by_key.values()]
    alternative=AnalysisBook(datums,analysis_years=b.analysis_years)
    assert profit_bridge(alternative,2025).target_delta==profit_bridge(b,2025).target_delta
    assert profit_bridge(alternative,2025).unexplained_residual==0


def test_t25_large_provision_does_not_improve_gross_turnover():
    evidence=Evidence(document_hash="a"*64,locator=PDFLocator(page_number=1,table_id="SYNTHETIC"))
    datums=[SourceDatum(name,year,D(value),(evidence,),"VERIFIED","TEST",ReportScope.CONSOLIDATED)
            for name,year,value in (("inventory_gross",2024,100),("inventory_gross",2025,100),
                                    ("inventory",2024,100),("inventory",2025,80),
                                    ("inventory_provision",2024,0),("inventory_provision",2025,20),
                                    ("cogs",2025,100))]
    book=AnalysisBook(datums,analysis_years=[2025])
    assert calculate_metric(book,"E02",2025).value==D("328.5")
    assert {x.metric_id:x.value for x in calculate_supplements(book,2025)}["DIO_GROSS"]==D("365")


def test_manual_source_ledger_has_exact_zero_residuals():
    ledger=json.loads((ROOT/"independent_calculations.json").read_text(encoding="utf-8"))
    assert len(ledger["checks"])==79
    assert all(item["pass"] and item["residual_vnd"]=="0" for item in ledger["checks"])


def test_zero_interest_is_not_applicable_not_infinite(case):
    b,_,_,_=case
    datums=[replace(d,value_vnd=D(0)) if d.concept=="interest_expense" and d.year==2025 else d for d in b.by_key.values()]
    metric=calculate_metric(AnalysisBook(datums,analysis_years=b.analysis_years),"D06",2025)
    assert metric.status=="NOT_APPLICABLE" and metric.value is None
    assert "zero or nonpositive denominator" in metric.reason


def test_missing_bridge_component_keeps_unexplained_residual(case):
    b,_,_,_=case
    datums=[d for d in b.by_key.values() if not(d.concept=="finance_income" and d.year==2025)]
    bridge=profit_bridge(AnalysisBook(datums,analysis_years=b.analysis_years),2025)
    assert bridge.status=="PARTIAL_COMPONENTS"
    assert bridge.unexplained_residual==D("60735787864")
    assert next(s for s in bridge.steps if s.code=="finance_income").signed_impact is None


def test_mixed_scope_and_weakest_status_fail_closed(case):
    b,_,_,_=case
    datums=list(b.by_key.values())
    with pytest.raises(ValueError,match="cannot combine companies or report scopes"):
        AnalysisBook([datums[0],replace(datums[1],report_scope=ReportScope.SEPARATE)])
    assert weakest("HUMAN_VERIFIED","analyst_transcribed_pending_separate_signoff") == "analyst_transcribed_pending_separate_signoff"
    assert weakest("VERIFIED","STALE_CACHE_MISMATCH")=="STALE_CACHE_MISMATCH"
