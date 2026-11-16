"""Deterministic financial arithmetic with explicit period and evidence rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from app.models import Evidence, FactState, FinancialFact, Period, PeriodType, ReportScope
from app.services.vas_normalizer import shift_year


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    UNVERIFIED_MISSING_CACHE = "UNVERIFIED_MISSING_CACHE"
    STALE_CACHE_MISMATCH = "STALE_CACHE_MISMATCH"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class CalculationResult:
    value: Decimal | None
    unit: str
    period: Period
    report_scope: ReportScope
    calculation: str | None
    verification_status: VerificationStatus
    evidences: list[Evidence]
    warnings: tuple[FactState, ...] = ()


def status_for_facts(*facts: FinancialFact) -> VerificationStatus:
    states = {fact.state for fact in facts}
    if FactState.STALE_CACHE_MISMATCH in states:
        return VerificationStatus.STALE_CACHE_MISMATCH
    if FactState.UNVERIFIED_MISSING_CACHE in states:
        return VerificationStatus.UNVERIFIED_MISSING_CACHE
    if states <= {FactState.VERIFIED, FactState.HUMAN_CORRECTED}:
        return VerificationStatus.VERIFIED
    return VerificationStatus.UNVERIFIED


def _warnings(*facts: FinancialFact) -> tuple[FactState, ...]:
    return tuple(dict.fromkeys(fact.state for fact in facts if fact.state not in {FactState.VERIFIED, FactState.HUMAN_CORRECTED}))


def _check_context(*facts: FinancialFact, allow_yoy: bool = False, mixed_period: bool = False) -> None:
    if not facts:
        raise ValueError("calculation needs facts")
    first = facts[0]
    for fact in facts[1:]:
        if fact.document.company_id != first.document.company_id or fact.document.company_name != first.document.company_name:
            raise ValueError("cannot combine different companies")
        if fact.document.document_hash != first.document.document_hash:
            raise ValueError("cannot combine document versions")
        if fact.document.report_scope != first.document.report_scope:
            raise ValueError("cannot combine separate and consolidated facts")
        if fact.document.accounting_regime != first.document.accounting_regime:
            raise ValueError("cannot combine accounting regimes")
        if fact.currency != first.currency:
            raise ValueError("cannot combine currencies")
        if not allow_yoy and fact.period.fiscal_year_label != first.period.fiscal_year_label:
            raise ValueError("cross-year arithmetic requires explicit YoY operation")
        if not mixed_period and fact.period.period_type != first.period.period_type:
            raise ValueError("cannot combine instant and duration without an explicit metric")
        if not mixed_period and fact.period.period_type == PeriodType.INSTANT and fact.period.as_of_date != first.period.as_of_date and not allow_yoy:
            raise ValueError("instant dates differ")
        if not mixed_period and fact.period.period_type == PeriodType.DURATION and (
            fact.period.period_start != first.period.period_start or fact.period.period_end != first.period.period_end
        ) and not allow_yoy:
            raise ValueError("duration dates differ")


def _same_line_yoy(current: FinancialFact, prior: FinancialFact) -> None:
    _check_context(current, prior, allow_yoy=True)
    if current.line_code != prior.line_code:
        raise ValueError("YoY requires the same namespaced line code")
    current_year = int(current.period.fiscal_year_label.removeprefix("FY"))
    previous_year = int(prior.period.fiscal_year_label.removeprefix("FY"))
    if current_year != previous_year + 1:
        raise ValueError("YoY requires consecutive fiscal years")
    if current.period.period_type == PeriodType.INSTANT:
        if prior.period.as_of_date not in {
            shift_year(current.period.as_of_date, -1), current.document.fiscal_period_start,
        }:
            raise ValueError("YoY instant dates must align one year apart")
    elif (prior.period.period_start != shift_year(current.period.period_start, -1)
          or prior.period.period_end != shift_year(current.period.period_end, -1)):
        raise ValueError("YoY durations must align one year apart")


def _trial_vnd(fact: FinancialFact) -> Decimal | None:
    if fact.state in {FactState.VERIFIED, FactState.HUMAN_CORRECTED}:
        return fact.normalized_value
    if fact.calculated_value is not None:
        return fact.calculated_value * fact.unit_multiplier
    return fact.normalized_value  # retained only as an unverified trial value


def _evidences(*facts: FinancialFact) -> list[Evidence]:
    return [evidence for fact in facts for evidence in fact.evidence]


def _format(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


def absolute_difference(current: FinancialFact, other: FinancialFact, *, year_over_year: bool = False) -> CalculationResult:
    if year_over_year:
        _same_line_yoy(current, other)
    else:
        _check_context(current, other)
    first, second = _trial_vnd(current), _trial_vnd(other)
    value = (first - second) / current.unit_multiplier if first is not None and second is not None else None
    left = first / current.unit_multiplier if first is not None else None
    right = second / current.unit_multiplier if second is not None else None
    formula = f"{_format(left)} - {_format(right)} = {_format(value)}" if value is not None else None
    return CalculationResult(value, current.unit_label, current.period, current.document.report_scope, formula, status_for_facts(current, other), _evidences(current, other), _warnings(current, other))


def growth_yoy(current: FinancialFact, prior: FinancialFact) -> CalculationResult:
    _same_line_yoy(current, prior)
    first, second = _trial_vnd(current), _trial_vnd(prior)
    if second is not None and second <= 0:
        raise ValueError("YoY percentage requires a positive prior value")
    value = (first - second) / second * Decimal("100") if first is not None and second is not None else None
    formula = f"({_format(first)} - {_format(second)}) / {_format(second)} × 100 = {_format(value)}" if value is not None else None
    return CalculationResult(value, "%", current.period, current.document.report_scope, formula, status_for_facts(current, prior), _evidences(current, prior), _warnings(current, prior))


def _ratio(numerator: FinancialFact, denominator: FinancialFact, *, percent: bool) -> CalculationResult:
    _check_context(numerator, denominator)
    top, bottom = _trial_vnd(numerator), _trial_vnd(denominator)
    if bottom == 0:
        raise ValueError("ratio denominator is zero")
    factor = Decimal("100") if percent else Decimal("1")
    value = top / bottom * factor if top is not None and bottom is not None else None
    same_unit = numerator.unit_multiplier == denominator.unit_multiplier
    shown_top = top / numerator.unit_multiplier if top is not None and same_unit else top
    shown_bottom = bottom / denominator.unit_multiplier if bottom is not None and same_unit else bottom
    formula = f"{_format(shown_top)} / {_format(shown_bottom)}{' × 100' if percent else ''} = {_format(value)}" if value is not None else None
    return CalculationResult(value, "%" if percent else "lần", numerator.period, numerator.document.report_scope, formula, status_for_facts(numerator, denominator), _evidences(numerator, denominator), _warnings(numerator, denominator))


def debt_to_equity(debt: FinancialFact, equity: FinancialFact) -> CalculationResult:
    if (debt.line_code.form_code, debt.line_code.code, equity.line_code.form_code, equity.line_code.code) != ("B01-DN", "300", "B01-DN", "400"):
        raise ValueError("Debt_to_Equity requires B01 codes 300 and 400")
    return _ratio(debt, equity, percent=False)


def balance_difference(assets: FinancialFact, debt: FinancialFact, equity: FinancialFact) -> CalculationResult:
    if [(fact.line_code.form_code, fact.line_code.code) for fact in (assets, debt, equity)] != [
        ("B01-DN", "270"), ("B01-DN", "300"), ("B01-DN", "400")
    ]:
        raise ValueError("balance check requires B01 codes 270, 300 and 400")
    _check_context(assets, debt, equity)
    asset_value, debt_value, equity_value = (_trial_vnd(fact) for fact in (assets, debt, equity))
    value = (asset_value - debt_value - equity_value) / assets.unit_multiplier if all(
        item is not None for item in (asset_value, debt_value, equity_value)
    ) else None
    formula = (
        f"{_format(asset_value / assets.unit_multiplier)} - "
        f"({_format(debt_value / assets.unit_multiplier)} + {_format(equity_value / assets.unit_multiplier)}) = {_format(value)}"
        if value is not None else None
    )
    return CalculationResult(value, assets.unit_label, assets.period, assets.document.report_scope, formula,
                             status_for_facts(assets, debt, equity), _evidences(assets, debt, equity),
                             _warnings(assets, debt, equity))


def gross_margin(gross_profit: FinancialFact, net_revenue: FinancialFact) -> CalculationResult:
    if (gross_profit.line_code.form_code, gross_profit.line_code.code, net_revenue.line_code.form_code, net_revenue.line_code.code) != ("B02-DN", "20", "B02-DN", "10"):
        raise ValueError("Gross_Margin requires B02 codes 20 and 10")
    return _ratio(gross_profit, net_revenue, percent=True)


def net_margin(net_income: FinancialFact, net_revenue: FinancialFact) -> CalculationResult:
    if (net_income.line_code.form_code, net_income.line_code.code, net_revenue.line_code.form_code, net_revenue.line_code.code) != ("B02-DN", "60", "B02-DN", "10"):
        raise ValueError("Net_Margin requires after-tax income code 60 and net revenue code 10")
    return _ratio(net_income, net_revenue, percent=True)


def _return_on(
    net_income: FinancialFact,
    closing: FinancialFact,
    *,
    metric: Literal["ROA", "ROE"],
    basis: Literal["closing", "average"],
    opening: FinancialFact | None,
) -> CalculationResult:
    expected_code = "270" if metric == "ROA" else "400"
    if (net_income.line_code.form_code, net_income.line_code.code) != ("B02-DN", "60"):
        raise ValueError("ROA/ROE require after-tax income B02 code 60")
    if (closing.line_code.form_code, closing.line_code.code) != ("B01-DN", expected_code):
        raise ValueError(f"{metric} requires closing B01 code {expected_code}")
    _check_context(net_income, closing, mixed_period=True)
    if net_income.period.period_type != PeriodType.DURATION or closing.period.period_type != PeriodType.INSTANT:
        raise ValueError("return metric needs duration income and instant balance")
    if closing.period.as_of_date != net_income.period.period_end:
        raise ValueError("closing balance date must equal income period end")
    operands = [net_income, closing]
    if basis == "average":
        if opening is None:
            raise ValueError("average basis requires opening balance")
        _check_context(net_income, opening, allow_yoy=True, mixed_period=True)
        if (opening.line_code.form_code, opening.line_code.code) != ("B01-DN", expected_code):
            raise ValueError("opening balance has wrong line code")
        if opening.period.period_type != PeriodType.INSTANT or opening.period.as_of_date not in {
            net_income.period.period_start, net_income.period.period_start - timedelta(days=1)
        }:
            raise ValueError("opening date must align with duration start")
        current_year = int(net_income.period.fiscal_year_label.removeprefix("FY"))
        if opening.period.fiscal_year_label != f"FY{current_year - 1}":
            raise ValueError("opening balance must be the prior fiscal year")
        operands.append(opening)
    elif opening is not None:
        raise ValueError("closing basis must not provide opening balance")
    income_value, closing_value = _trial_vnd(net_income), _trial_vnd(closing)
    opening_value = _trial_vnd(opening) if opening is not None else None
    denominator = (opening_value + closing_value) / 2 if basis == "average" and opening_value is not None and closing_value is not None else closing_value if basis == "closing" else None
    if denominator == 0:
        raise ValueError("return metric denominator is zero")
    value = income_value / denominator * Decimal("100") if income_value is not None and denominator is not None else None
    denominator_label = f"({_format(opening_value)} + {_format(closing_value)}) / 2" if basis == "average" and denominator is not None else _format(closing_value) if denominator is not None else "?"
    formula = f"{metric} ({basis}) = {_format(income_value)} / ({denominator_label}) × 100 = {_format(value)}" if value is not None else None
    return CalculationResult(value, "%", net_income.period, net_income.document.report_scope, formula, status_for_facts(*operands), _evidences(*operands), _warnings(*operands))


def roa(net_income: FinancialFact, closing_assets: FinancialFact, *, basis: Literal["closing", "average"], opening_assets: FinancialFact | None = None) -> CalculationResult:
    return _return_on(net_income, closing_assets, metric="ROA", basis=basis, opening=opening_assets)


def roe(net_income: FinancialFact, closing_equity: FinancialFact, *, basis: Literal["closing", "average"], opening_equity: FinancialFact | None = None) -> CalculationResult:
    return _return_on(net_income, closing_equity, metric="ROE", basis=basis, opening=opening_equity)
