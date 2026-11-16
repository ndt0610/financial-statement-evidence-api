"""Compose the multi-year P5 report without changing existing M4/M5 contracts."""

from __future__ import annotations

from collections import Counter

from app.models.full_analysis import FullAnalysisReport
from app.services.analysis_book import AnalysisBook
from app.services.analysis_calculator import METRIC_IDS, calculate_all, calculate_dupont, calculate_supplements
from app.services.financial_bridges import calculate_bridges
from app.services.interpretation_engine import interpret
from app.services.p10_metrics import calculate_p10
from app.services.p10_diagnostics import diagnostics
from app.services.p11_narrative import build_p11_findings


def build_full_report(book: AnalysisBook, *, company: str, years: list[int] | None = None) -> FullAnalysisReport:
    years = sorted(years or book.analysis_years)
    if not years or any(y not in book.years for y in years):
        raise ValueError("requested years have no source facts")
    # Cumulative measures such as CAGR and multi-year CFO/NI must use the
    # selected analysis window, while prior-year balance inputs stay available.
    if years != book.analysis_years:
        book = AnalysisBook(list(book.by_key.values()), analysis_years=years)
    core = calculate_all(book, years)
    metrics = list(core)
    for year in years:
        metrics.extend(calculate_p10(book, year))
        metrics.extend(calculate_supplements(book, year))
        metrics.extend(calculate_dupont(book, year).values())
    bridges = [bridge for year in years for bridge in calculate_bridges(book, year)]
    findings = interpret(book, metrics, bridges)
    coverage = {year: dict(Counter(item.status.value for item in core if item.fiscal_year == year))
                for year in years}
    for year, counts in coverage.items():
        if sum(counts.values()) != len(METRIC_IDS):
            raise AssertionError(f"metric coverage lost an ID for {year}")
    return FullAnalysisReport(company=company, company_id=book.company_id, report_scope=book.scope,
                              years=years, metrics=metrics, bridges=bridges, findings=findings, role_diagnostics=diagnostics(metrics,years), analytical_findings=[f for i in range(len(years)) for f in build_p11_findings(book,metrics,bridges,years[:i+1])],
                              coverage_by_year=coverage,
                              limitations=[
                                  "Reported cash is not certified unrestricted; deposit-inclusive net debt is a conditional scenario.",
                                  "Proxy DSO/DPO use revenue/COGS, not credit sales/purchases.",
                                  "Missing note disclosures and contractual schedules remain MISSING_INPUT; no DSCR is synthesized.",
                              ])
