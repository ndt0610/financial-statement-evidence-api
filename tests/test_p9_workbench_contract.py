"""P9 accounting decisions stay explicit when a source line is absent or signed differently."""
from dataclasses import replace
from decimal import Decimal

from app.models.full_analysis import MetricStatus
from app.services.analysis_calculator import calculate_metric
from app.services.financial_bridges import profit_bridge
from app.services.full_analysis_report import build_full_report
from tests.p5_fixture import pnj_book


def test_debt_to_equity_short_borrowings_proxy_until_long_debt_is_signed():
    book = pnj_book()
    pending = calculate_metric(book, "D03", 2025)
    assert pending.status == MetricStatus.PROXY
    assert pending.value == book.get("borrowings", 2025).value_vnd / book.get("equity", 2025).value_vnd * 100
    assert "B01:338" in pending.reason
    assert "long_borrowings@2025" not in pending.inputs

    book.by_key[("long_borrowings", 2025)] = replace(
        book.get("long_borrowings", 2025), verification_status="HUMAN_VERIFIED")
    signed = calculate_metric(book, "D03", 2025)
    assert signed.status == MetricStatus.COMPUTED
    assert signed.value == pending.value
    assert "long_borrowings@2025" in signed.inputs


def test_profit_bridge_orients_tax_only_against_same_year_ebt_to_income_identity():
    book = pnj_book()
    # The acceptance ledger records source-line values; some historical PDF rows
    # use a negative tax expense convention. The identity must decide the sign.
    for year in (2021, 2022):
        b = profit_bridge(book, year)
        assert b.status == "BALANCED"
        assert b.unexplained_residual == Decimal(0)
        assert {"current_tax", "deferred_tax"}.issubset({step.code for step in b.steps})
    book.by_key[("current_tax", 2022)] = replace(
        book.get("current_tax", 2022), value_vnd=Decimal("1"))
    damaged = profit_bridge(book, 2022)
    assert damaged.status == "PARTIAL_COMPONENTS"
    assert any(step.code == "current_tax" and step.signed_impact is None for step in damaged.steps)


def test_selected_year_range_rebases_cagr_and_cumulative_cfo():
    book = pnj_book()
    report = build_full_report(book, company="PNJ", years=[2023, 2024, 2025])
    by_id = {m.metric_id: m for m in report.metrics if m.fiscal_year == 2025}
    start, end = book.get("revenue", 2023).value_vnd, book.get("revenue", 2025).value_vnd
    expected_cagr = ((end / start) ** (Decimal(1) / Decimal(2)) - 1) * 100
    assert abs(by_id["G03"].value - expected_cagr) < Decimal("0.0000000001")
    cfo = sum(book.get("cfo", year).value_vnd for year in (2023, 2024, 2025))
    income = sum(book.get("net_income", year).value_vnd for year in (2023, 2024, 2025))
    assert by_id["C07"].value == cfo / income * 100
