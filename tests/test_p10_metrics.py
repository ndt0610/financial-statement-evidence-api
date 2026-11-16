from dataclasses import replace
from decimal import Decimal as D

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.models import Evidence, PDFLocator, ReportScope
from app.services.analysis_book import AnalysisBook, SourceDatum
from app.services.analysis_calculator import calculate_metric
from app.services.p10_metrics import calculate_p10
from app.services.full_analysis_report import build_full_report
from scripts.build_m4_fixture import build_fixture


def sample_book():
    evidence = Evidence(document_hash='a'*64, locator=PDFLocator(page_number=1, region='row'), observed_text='source')
    values = {'revenue':'1000','admin':'50','selling':'100','gross_profit':'300', 'ebt':'200',
              'net_income':'160','current_tax':'35','deferred_tax':'5', 'interest_expense':'20',
              'assets':'1200','equity':'600','liabilities':'600','current_assets':'500',
              'current_liabilities':'200','inventory':'400','borrowing_in':'500','principal_paid':'-250',
              'borrowings':'100','cash':'50','short_investments':'50','capex':'-100','dividends_paid':'-80'}
    datums = [SourceDatum(k, 2025, D(v), (evidence,), 'EXTRACTED', 'SYN', ReportScope.CONSOLIDATED, '2025-12-31', k) for k,v in values.items()]
    datums += [SourceDatum(k, 2024, D(v), (evidence,), 'EXTRACTED', 'SYN', ReportScope.CONSOLIDATED, '2024-12-31', k) for k,v in {'assets':'1000','equity':'400','borrowings':'80'}.items()]
    return AnalysisBook(datums,analysis_years=[2025])


def test_p10_values_and_source_status():
    b=sample_book(); metrics={x.metric_id:x for x in calculate_p10(b,2025)}
    for key,value in {'X01_ADMIN_TO_REV':'5','X02_SGA_TO_REV':'15','X03_NON_CORE_PRETAX_DIFF':'25',
                      'X04_EFFECTIVE_TAX_RATE':'20','X09_NWC_TO_INVENTORY':'75',
                      'X10_BORROWING_INFLOW_TO_ST_DEBT':'5','X11_REPAYMENT_TO_BORROWING_INFLOW':'50',
                      'X12_NET_WORTH':'600','X13_NOWC_TO_REV':'30','X14_CAPEX_TO_REV':'10'}.items():
        assert metrics[key].value == D(value), key
        assert metrics[key].verification_status == 'EXTRACTED'
        assert metrics[key].evidences
    factors=[metrics['X05_DUPONT_TAX_BURDEN'].value,metrics['X06_DUPONT_INTEREST_BURDEN'].value,
             metrics['X07_DUPONT_EBIT_MARGIN'].value/D(100),D('1000')/D('1100'),D('1100')/D('500')]
    product=D(1)
    for factor in factors: product *= factor
    assert abs(product-D('160')/D('500')) < D('1e-26')


def test_tax_mismatch_and_nonpositive_dupont_fail_closed():
    b=sample_book()
    data=[replace(x,value_vnd=D('6')) if x.concept=='deferred_tax' else x for x in b.by_key.values()]
    results={x.metric_id:x for x in calculate_p10(AnalysisBook(data,analysis_years=[2025]),2025)}
    assert results['X04_EFFECTIVE_TAX_RATE'].status == 'MISSING_INPUT'
    data=[replace(x,value_vnd=D('0')) if x.concept=='ebt' else x for x in b.by_key.values()]
    results={x.metric_id:x for x in calculate_p10(AnalysisBook(data,analysis_years=[2025]),2025)}
    assert results['X05_DUPONT_TAX_BURDEN'].status == 'NOT_APPLICABLE'


def test_missing_long_term_debt_yields_labelled_proxy():
    b=sample_book()
    assert calculate_metric(b,'D04',2025).status == 'PROXY'
    assert calculate_metric(b,'D08',2025).status == 'MISSING_INPUT'  # CFO absent


def test_structured_role_findings_cover_four_roles_with_evidence():
    report=build_full_report(sample_book(),company='Synthetic',years=[2025])
    assert set(report.role_diagnostics)=={'investor','credit','tender','cfo'}
    for tiers in report.role_diagnostics.values():
        assert len(tiers)==4
        for tier in tiers:
            assert all(tier[k] for k in ('observation','evidence_statement','meaning','follow_up'))
    assert report.role_diagnostics['investor'][0]['evidences']


def test_simulation_api_rejects_zero_factor_and_returns_assumptions(tmp_path):
    workbook=build_fixture(tmp_path/'sample.xlsx')
    with TestClient(create_app(upload_dir=tmp_path/'uploads')) as client:
        with workbook.open('rb') as f:
            response=client.post('/v1/documents/ingest',files={'file':('sample.xlsx',f)},data={'accounting_regime':'TT200','data_origin':'SYNTHETIC'})
        key=response.json()['document_hash']
        report=client.post('/v1/analysis/company-report',json={'document_hashes':[key], 'include_comparative_periods':True})
        assert report.status_code==200
        assert report.json()['report']['years']==[2024,2025]
        payload={'document_hashes':[key], 'fiscal_year':2025,'revenue_years':1,'revenue_factor':'2','duration_years':'1','financial_resource_ratio':'0.3'}
        simulation=client.post('/v1/analysis/simulations',json=payload)
        assert simulation.status_code==200, simulation.text
        assert simulation.json()['kind']=='SIMULATION'
        assert simulation.json()['revenue_capacity']['status']=='SIMULATION'
        assert D(simulation.json()['revenue_capacity']['value']) == D('325000000')
        assert D(simulation.json()['dio_sensitivity']['per_day']) == D('450000000')/D(365)
        assert simulation.json()['dio_sensitivity']['value'] is None  # no FY2023 opening inventory
        assert simulation.json()['revenue_capacity']['evidences']
        assert client.post('/v1/analysis/simulations',json={**payload,'revenue_factor':'0'}).status_code==422
