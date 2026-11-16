from decimal import Decimal
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.models import Evidence, FactState, PDFLocator, ReportScope
from app.services.analysis_book import AnalysisBook, SourceDatum
from app.services.analysis_calculator import METRIC_IDS, calculate_metric
from app.services.metric_matrix import metric_matrix
from app.services.full_analysis_report import build_full_report


def test_catalogue_covers_all_52_and_exposes_dependencies(tmp_path):
    rows = metric_matrix()
    assert {row["id"] for row in rows} == set(METRIC_IDS)
    assert all(row["name"] and row["formula"] and row["inputs"] and row["history_rule"] for row in rows)
    assert next(row for row in rows if row["id"] == "Q02")["tier"] == "EXTENDED"
    with TestClient(create_app(upload_dir=tmp_path)) as client:
        assert len(client.get('/v1/analysis/metric-matrix').json()['metrics']) == 52


def test_single_report_supports_current_year_average_not_prior_year_average():
    evidence = Evidence(document_hash='a'*64, locator=PDFLocator(page_number=1), observed_text='synthetic')
    values = [("assets", 2024, "100"), ("assets", 2025, "140"),
              ("net_income", 2024, "8"), ("net_income", 2025, "12")]
    book = AnalysisBook([SourceDatum(name, year, Decimal(value), (evidence,), 'VERIFIED', 'SYN',
                                    ReportScope.CONSOLIDATED) for name, year, value in values])
    current = calculate_metric(book, 'P05', 2025)
    previous = calculate_metric(book, 'P05', 2024)
    assert current.value == Decimal('10') and current.status == 'COMPUTED'
    assert previous.value is None and previous.status == 'MISSING_INPUT' and 'assets@2023' in previous.reason
    unreviewed = AnalysisBook([replace(item, verification_status='EXTRACTED') for item in book.by_key.values()])
    assert calculate_metric(unreviewed, 'P05', 2025).input_status == 'UNVERIFIED_INPUT'


def test_financial_finding_has_sourced_sections_without_inventing_cause():
    evidence = Evidence(document_hash='b'*64, locator=PDFLocator(page_number=1), observed_text='synthetic')
    values = [("revenue", 2024, "100"), ("revenue", 2025, "120"),
              ("net_income", 2024, "10"), ("net_income", 2025, "12"),
              ("cfo", 2024, "8"), ("cfo", 2025, "9")]
    book = AnalysisBook([SourceDatum(name, year, Decimal(value), (evidence,), 'VERIFIED', 'SYN',
                                    ReportScope.CONSOLIDATED, source_line=("B03-DN:20" if name == 'cfo' else "B02-DN:10"))
                         for name, year, value in values], analysis_years=[2024, 2025])
    report = build_full_report(book, company='Synthetic')
    finding = next(item for item in report.analytical_findings if item['id'] == 'cashflow' and item['year'] == 2025)
    sections = finding['analysis_sections']
    assert set(sections) == {'headline', 'comparison', 'drivers', 'cross_statement', 'evidence_status', 'caveat', 'next_check'}
    assert sections['comparison'] and finding['evidences']
    assert set(sections['cross_statement']['forms']) == {'B02-DN', 'B03-DN'}
    assert 'thuyết minh' in sections['caveat']


def test_direct_cash_flow_code_02_cannot_supply_depreciation_to_d07():
    evidence = Evidence(document_hash='c'*64, locator=PDFLocator(page_number=12), observed_text='source')
    document = SimpleNamespace(company_name='Synthetic', company_id='SYN',
                               report_scope=ReportScope.CONSOLIDATED,
                               fiscal_period_start=date(2025, 1, 1),
                               fiscal_period_end=date(2025, 12, 31), fiscal_year_label='FY2025')

    def b03_book(label):
        fact = SimpleNamespace(line_code=SimpleNamespace(form_code='B03-DN', code='02'),
                               original_label=label, document=document,
                               period=SimpleNamespace(fiscal_year_label='FY2025',
                                                      as_of_date=None, period_end=date(2025, 12, 31)),
                               state=FactState.EXTRACTED, normalized_value=Decimal('10'),
                               evidence=(evidence,))
        cash = SimpleNamespace(line_code=SimpleNamespace(form_code='B01-DN', code='110'),
                               original_label='Tiền', document=document,
                               period=SimpleNamespace(fiscal_year_label='FY2025',
                                                      as_of_date=date(2025, 12, 31), period_end=None),
                               state=FactState.EXTRACTED, normalized_value=Decimal('20'),
                               evidence=(evidence,))
        index = SimpleNamespace(result=SimpleNamespace(facts=[fact, cash], review_history=()),
                                supplemental_facts=())
        return AnalysisBook.from_indexes([index])

    direct = b03_book('Tiền chi trả cho người cung cấp hàng hóa, dịch vụ')
    indirect = b03_book('Khấu hao tài sản cố định')
    assert direct.get('da', 2025) is None
    assert indirect.get('da', 2025).value_vnd == Decimal('10')

    base = [SourceDatum(name, 2025, Decimal(value), (evidence,), 'VERIFIED', 'SYN',
                        ReportScope.CONSOLIDATED) for name, value in
            [('borrowings', '100'), ('long_borrowings', '0'),
             ('ebt', '20'), ('interest_expense', '5')]]
    assert calculate_metric(AnalysisBook(base + list(direct.by_key.values())), 'D07', 2025).status == 'MISSING_INPUT'
    assert calculate_metric(AnalysisBook(base + list(indirect.by_key.values())), 'D07', 2025).value is not None
