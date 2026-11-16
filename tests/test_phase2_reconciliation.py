from decimal import Decimal
from types import SimpleNamespace
from datetime import date
from pathlib import Path
import json

import pytest

from app.models import FactState, ReportScope
from app.services.statement_reconciliation import reconcile_statement
from app.adapters.pdf_adapter import PdfDocumentSpec, _period, extract_docling_tables


def facts(form, values):
    return [SimpleNamespace(line_code=SimpleNamespace(form_code=form, code=code),
                            period=SimpleNamespace(fiscal_year_label="FY2025"),
                            state=FactState.EXTRACTED, normalized_value=Decimal(value))
            for code, value in values]


def check(items, form, name):
    return next(c for c in reconcile_statement(items, form, 2025) if c["check"] == name)


def test_balance_is_arithmetic_only_and_does_not_verify_wrong_values():
    rows = facts("B01-DN", [("270", "100"), ("440", "100"), ("300", "61"), ("400", "39")])
    assert check(rows, "B01-DN", "balance")["status"] == "MATH_CONSISTENT"
    assert all(row.state == FactState.EXTRACTED for row in rows)
    rows[1].normalized_value = Decimal("99")
    assert check(rows, "B01-DN", "balance")["residual_vnd"] == "1"
    assert check(rows, "B01-DN", "balance")["status"] == "MISMATCH"


def test_incomplete_duplicate_and_sign_are_not_silently_reconciled():
    rows = facts("B02-DN", [("10", "100"), ("11", "60"), ("20", "40")])
    assert check(rows, "B02-DN", "gross_profit")["status"] == "MATH_CONSISTENT"
    assert check(rows[:-1], "B02-DN", "gross_profit")["status"] == "MISSING_INPUT"
    assert check(rows + [rows[0]], "B02-DN", "gross_profit")["status"] == "DUPLICATE_INPUT"
    rows[1].normalized_value = Decimal("-60")
    assert check(rows, "B02-DN", "gross_profit")["status"] == "AMBIGUOUS_SIGN"


def test_cash_flow_identity_preserves_signed_flows():
    rows = facts("B03-DN", [("20", "15"), ("30", "-10"), ("40", "-2"), ("50", "3")])
    assert check(rows, "B03-DN", "net_cash_change")["residual_vnd"] == "0"
    rows[1].normalized_value = Decimal("10")
    assert check(rows, "B03-DN", "net_cash_change")["status"] == "MISMATCH"


def test_cash_flow_opening_balance_is_instant_at_period_start():
    spec = SimpleNamespace(fiscal_start=date(2025, 1, 1), fiscal_end=date(2025, 12, 31))
    opening = _period(spec, 'B03-DN', '60', True)
    prior = _period(spec, 'B03-DN', '60', False)
    assert opening.period_type.value == 'INSTANT' and opening.as_of_date == date(2025, 1, 1)
    assert prior.period_type.value == 'INSTANT' and prior.as_of_date == date(2024, 1, 1)


def test_funding_is_not_counted_twice_when_code_430_exists():
    rows = facts('B01-DN', [('300', '40'), ('410', '50'), ('430', '10'),
                            ('400', '60'), ('440', '100'), ('270', '100')])
    assert check(rows, 'B01-DN', 'equity')['residual_vnd'] == '0'
    assert check(rows, 'B01-DN', 'total_funding')['residual_vnd'] == '0'


def test_optional_printed_components_are_aggregate_inference_not_facts():
    rows = facts('B01-DN', [('100', '70'), ('110', '70'), ('400', '60'),
                              ('410', '60'), ('270', '100'), ('300', '40')])
    assets = check(rows, 'B01-DN', 'current_assets')
    equity = check(rows, 'B01-DN', 'equity')
    assert assets['status'] == equity['status'] == 'MATH_CONSISTENT'
    assert assets['omitted_zero_codes'] == ['120', '130', '140', '150']
    assert equity['omitted_zero_codes'] == ['430']
    assert assets['zero_inference_scope'] == 'AGGREGATE_ONLY_NOT_A_FACT'
    assert check(rows, 'B01-DN', 'balance_sheet_equation')['residual_vnd'] == '0'
    assert not any(row.line_code.code in {'120', '130', '140', '150', '430'} for row in rows)
    rows[1].normalized_value = Decimal('69')
    incomplete = check(rows, 'B01-DN', 'current_assets')
    assert incomplete['status'] == 'MISSING_INPUT' and incomplete['residual_vnd'] == '1'


def test_real_pnj_2025_extended_b01_reconciles_both_columns_without_fabricating_zero():
    source = Path(__file__).resolve().parents[1] / '.cache/pnj_precode/p7_main_113_115.json'
    if not source.is_file():
        pytest.skip('Local PNJ 2025 Docling development payload unavailable')
    spec = PdfDocumentSpec('PNJ', 'PNJ', '0' * 64, str(source), ReportScope.CONSOLIDATED,
                           'TT200', date(2025, 1, 1), date(2025, 12, 31))
    result = extract_docling_tables(json.loads(source.read_text(encoding='utf-8')),
                                    spec, extended_codes=True)
    for year in (2024, 2025):
        checks = reconcile_statement(result.facts, 'B01-DN', year)
        assert len(checks) == 8 and all(c['status'] == 'MATH_CONSISTENT' for c in checks)
        assert {f.line_code.code for f in result.facts if f.line_code.form_code == 'B01-DN'
                and f.period.fiscal_year_label == f'FY{year}'} >= {'150', '410'}
        assert not any(f.line_code.code in {'230', '430'} for f in result.facts
                       if f.line_code.form_code == 'B01-DN' and
                       f.period.fiscal_year_label == f'FY{year}')
