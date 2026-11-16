"""Deterministic, evidence-linked financial health indicators; no fraud verdicts."""

from __future__ import annotations

from decimal import Decimal

from app.models import FactState, FinancialFact, PeriodType
from app.models.health_review import (
    CheckStatus, DocumentMetadata, HealthIndicator, HealthReportResponse,
    IndicatorInput, IndicatorStatus, ReconciliationCheck, ReviewSignal, SignalStatus,
)
from app.services.financial_calculator import VerificationStatus, status_for_facts
from app.services.qa_service import DocumentIndex


def _evidences(*facts: FinancialFact | None) -> list:
    return [evidence for fact in facts if fact is not None for evidence in fact.evidence]


def _input(fact: FinancialFact) -> IndicatorInput:
    return IndicatorInput(
        form_code=fact.line_code.form_code, line_code=fact.line_code.code,
        period=fact.period, value_vnd=fact.normalized_value,
        state=fact.state.value, evidences=fact.evidence,
    )


def _indicator(
    code: str, label: str, formula: str, unit: str,
    facts: tuple[FinancialFact | None, ...],
    *, numerator: Decimal | None = None,
    denominator: Decimal | None = None,
    direct: Decimal | None = None,
    percent: bool = False,
    positive_denominator: bool = False,
    period_fact: FinancialFact | None = None,
) -> HealthIndicator:
    present = tuple(fact for fact in facts if fact is not None)
    verification = status_for_facts(*present) if len(present) == len(facts) else VerificationStatus.NOT_APPLICABLE
    status = IndicatorStatus.COMPUTED
    value = None
    if len(present) != len(facts):
        status = IndicatorStatus.INSUFFICIENT_DATA
    elif verification != VerificationStatus.VERIFIED:
        status = IndicatorStatus.UNVERIFIED_INPUT
    elif denominator is not None:
        if denominator == 0 or (positive_denominator and denominator < 0):
            status = IndicatorStatus.INVALID_DENOMINATOR
        elif numerator is not None:
            value = numerator / denominator * (Decimal("100") if percent else Decimal("1"))
    elif direct is not None:
        value = direct
    else:
        status = IndicatorStatus.INSUFFICIENT_DATA
    return HealthIndicator(
        code=code, label=label, formula=formula,
        period=(period_fact or next(iter(present), None)).period if present else None,
        value=value, unit=unit, status=status, verification_status=verification,
        inputs=[_input(fact) for fact in present], input_evidences=_evidences(*facts),
    )


def _amount(fact: FinancialFact | None) -> Decimal | None:
    return fact.normalized_value if fact is not None else None


def _sum_or_none(*amounts: Decimal | None) -> Decimal | None:
    return sum(amounts, Decimal("0")) if all(value is not None for value in amounts) else None


def _difference(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    return left - right if left is not None and right is not None else None


def _growth(current: Decimal | None, prior: Decimal | None) -> Decimal | None:
    return _difference(current, prior)


def _check(code: str, facts: tuple[FinancialFact | None, ...], *, difference: Decimal | None, tolerance: Decimal) -> ReconciliationCheck:
    present = tuple(fact for fact in facts if fact is not None)
    if len(present) != len(facts):
        status = CheckStatus.INSUFFICIENT_DATA
    elif any(fact.state not in {FactState.VERIFIED, FactState.HUMAN_CORRECTED} for fact in present):
        status = CheckStatus.UNVERIFIED_INPUT
    elif difference is None:
        status = CheckStatus.INSUFFICIENT_DATA
    else:
        status = CheckStatus.WARNING if abs(difference) > tolerance else CheckStatus.PASS
    return ReconciliationCheck(
        code=code, status=status, difference_vnd=difference if status in {CheckStatus.PASS, CheckStatus.WARNING} else None,
        tolerance_vnd=tolerance, input_evidences=_evidences(*facts),
    )


def review_health(index: DocumentIndex, *, year: int | None = None, tolerance_vnd: Decimal = Decimal("0")) -> HealthReportResponse:
    if tolerance_vnd < 0:
        raise ValueError("tolerance_vnd must be nonnegative")
    if not index.result.facts:
        raise ValueError("document has no financial facts")
    scopes = {fact.document.report_scope for fact in index.result.facts}
    if len(scopes) != 1:
        raise ValueError("health review requires exactly one report scope")
    scope = next(iter(scopes))
    year = year or max(index.years)
    if year != max(int(fact.document.fiscal_year_label.removeprefix("FY")) for fact in index.result.facts):
        raise ValueError("health review currently supports the document fiscal year only")

    def fact(form: str, line: str, fiscal_year: int = year) -> FinancialFact | None:
        return index.get(form, line, fiscal_year, scope)

    a100, a110, a130, a131, a136, a140, a216, a270 = (fact("B01-DN", code) for code in ("100", "110", "130", "131", "136", "140", "216", "270"))
    a300, a310, a400 = (fact("B01-DN", code) for code in ("300", "310", "400"))
    revenue, income, cfo, closing_cash = (fact(form, code) for form, code in (("B02-DN", "10"), ("B02-DN", "60"), ("B03-DN", "20"), ("B03-DN", "70")))
    if a100 and revenue and a100.period.as_of_date != revenue.period.period_end:
        raise ValueError("B01 and B02 statement periods do not align")
    if revenue and cfo and (revenue.period.period_start, revenue.period.period_end) != (cfo.period.period_start, cfo.period.period_end):
        raise ValueError("B02 and B03 statement periods do not align")
    closing_cash_date = (
        closing_cash.period.as_of_date if closing_cash and closing_cash.period.period_type == PeriodType.INSTANT
        else closing_cash.period.period_end if closing_cash else None
    )
    if a110 and closing_cash and a110.period.as_of_date != closing_cash_date:
        raise ValueError("B01 and B03 statement periods do not align")
    prior_revenue, prior_ar, prior_total_ar, prior_inventory = (fact(form, code, year - 1) for form, code in (("B02-DN", "10"), ("B01-DN", "131"), ("B01-DN", "130"), ("B01-DN", "140")))

    indicators: list[HealthIndicator] = []

    def add(code: str, label: str, formula: str, unit: str, facts: tuple[FinancialFact | None, ...], *, direct=None, numerator=None, denominator=None, percent=False, positive_denominator=False, period_fact=None) -> None:
        indicators.append(_indicator(code, label, formula, unit, facts, direct=direct, numerator=numerator,
                                     denominator=denominator, percent=percent, positive_denominator=positive_denominator,
                                     period_fact=period_fact))

    add("net_working_capital", "Vốn lưu động ròng", "B01:100 - B01:310", "đồng", (a100, a310), direct=_difference(_amount(a100), _amount(a310)))
    add("current_ratio", "Hệ số thanh toán hiện hành", "B01:100 / B01:310", "lần", (a100, a310), numerator=_amount(a100), denominator=_amount(a310), positive_denominator=True)
    add("quick_ratio_simple", "Hệ số thanh toán nhanh giản lược", "(B01:100 - B01:140) / B01:310", "lần", (a100, a140, a310), numerator=_difference(_amount(a100), _amount(a140)), denominator=_amount(a310), positive_denominator=True)
    add("cash_ratio", "Hệ số thanh toán tức thời", "B01:110 / B01:310", "lần", (a110, a310), numerator=_amount(a110), denominator=_amount(a310), positive_denominator=True)
    add("cfo_to_net_income", "CFO / lợi nhuận sau thuế", "B03:20 / B02:60", "lần", (cfo, income), numerator=_amount(cfo), denominator=_amount(income), positive_denominator=True, period_fact=income)
    for code, label, current, prior, form_code in (
        ("revenue_growth", "Tăng trưởng doanh thu thuần", revenue, prior_revenue, "B02:10"),
        ("trade_receivables_growth", "Tăng trưởng phải thu khách hàng", a131, prior_ar, "B01:131"),
        ("total_receivables_growth", "Tăng trưởng tổng phải thu ngắn hạn", a130, prior_total_ar, "B01:130"),
        ("inventory_growth", "Tăng trưởng hàng tồn kho", a140, prior_inventory, "B01:140"),
    ):
        add(code, label, f"({form_code} kỳ này - {form_code} kỳ trước) / {form_code} kỳ trước × 100", "%",
            (current, prior), numerator=_growth(_amount(current), _amount(prior)), denominator=_amount(prior),
            percent=True, positive_denominator=True, period_fact=current)
    add("other_receivables_short_share", "Phải thu ngắn hạn khác / tổng tài sản", "B01:136 / B01:270 × 100", "%",
        (a136, a270), numerator=_amount(a136), denominator=_amount(a270), percent=True, positive_denominator=True)
    add("other_receivables_total_share", "Phải thu khác ngắn và dài hạn / tổng tài sản", "(B01:136 + B01:216) / B01:270 × 100", "%",
        (a136, a216, a270), numerator=_sum_or_none(_amount(a136), _amount(a216)), denominator=_amount(a270), percent=True, positive_denominator=True)

    checks = [
        _check("balance_sheet", (a270, a300, a400), difference=_difference(_amount(a270), _sum_or_none(_amount(a300), _amount(a400))), tolerance=tolerance_vnd),
        _check("closing_cash_b03_vs_b01", (closing_cash, a110), difference=_difference(_amount(closing_cash), _amount(a110)), tolerance=tolerance_vnd),
    ]
    by_code = {item.code: item for item in indicators}
    signals: list[ReviewSignal] = []
    if cfo is None or income is None or cfo.state not in {FactState.VERIFIED, FactState.HUMAN_CORRECTED} or income.state not in {FactState.VERIFIED, FactState.HUMAN_CORRECTED}:
        signals.append(ReviewSignal(code="earnings_cash_flow", status=SignalStatus.INSUFFICIENT_DATA,
                                    explanation="Thiếu hoặc chưa kiểm chứng được lợi nhuận/CFO.",
                                    supporting_indicators=["cfo_to_net_income"], input_evidences=_evidences(cfo, income)))
    elif income.normalized_value > 0 and cfo.normalized_value < 0:
        signals.append(ReviewSignal(code="positive_net_income_negative_operating_cash_flow", status=SignalStatus.NEEDS_REVIEW,
                                    explanation="Lợi nhuận dương nhưng dòng tiền kinh doanh âm; cần xem biến động vốn lưu động và thuyết minh.",
                                    supporting_indicators=["cfo_to_net_income"], input_evidences=_evidences(income, cfo)))
    elif income.normalized_value <= 0:
        signals.append(ReviewSignal(code="nonpositive_net_income", status=SignalStatus.NEEDS_REVIEW,
                                    explanation="Lợi nhuận không dương; tỷ số CFO/lợi nhuận không được tính. Xem trực tiếp dấu và giá trị của cả hai.",
                                    supporting_indicators=["cfo_to_net_income"], input_evidences=_evidences(income, cfo)))

    receivable_growth = by_code["trade_receivables_growth"]
    proxy_growth = by_code["total_receivables_growth"]
    revenue_growth = by_code["revenue_growth"]
    used_proxy = receivable_growth.value is None and proxy_growth.value is not None
    selected_growth = proxy_growth if used_proxy else receivable_growth
    compared_codes = [selected_growth.code, "revenue_growth"]
    if selected_growth.value is None or revenue_growth.value is None:
        signals.append(ReviewSignal(code="receivables_vs_revenue", status=SignalStatus.INSUFFICIENT_DATA,
                                    explanation="Không đủ số đã kiểm chứng để so sánh tăng trưởng phải thu khách hàng và doanh thu.",
                                    supporting_indicators=compared_codes,
                                    input_evidences=selected_growth.input_evidences + revenue_growth.input_evidences))
    elif selected_growth.value > revenue_growth.value:
        signals.append(ReviewSignal(
            code="total_receivables_proxy_grow_faster_than_revenue" if used_proxy else "trade_receivables_grow_faster_than_revenue",
            status=SignalStatus.NEEDS_REVIEW,
            explanation=("Không có B01:131; dùng tổng phải thu B01:130 như chỉ báo thay thế, gồm cả các khoản không phải tín dụng bán hàng."
                         if used_proxy else "Phải thu khách hàng tăng nhanh hơn doanh thu; cần xem thời hạn thu tiền và thuyết minh."),
            supporting_indicators=compared_codes,
            input_evidences=selected_growth.input_evidences + revenue_growth.input_evidences,
        ))
    for check in checks:
        if check.status == CheckStatus.WARNING:
            signals.append(ReviewSignal(code=f"reconciliation_{check.code}", status=SignalStatus.NEEDS_REVIEW,
                                        explanation="Hai chỉ tiêu đối chiếu vượt dung sai; cần xác minh nguồn.",
                                        supporting_indicators=[], parameter_thresholds_used={"tolerance_vnd": check.tolerance_vnd},
                                        input_evidences=check.input_evidences))

    first = index.result.facts[0].document
    periods = sorted({fact.period.fiscal_year_label for fact in index.result.facts})
    return HealthReportResponse(
        document_metadata=DocumentMetadata(
            document_hash=index.result.document_hash, company=first.company_name,
            report_scope=scope, accounting_regime=first.accounting_regime,
            periods_covered=periods, data_origin=first.data_origin,
        ),
        reconciliation_checks=checks, indicators=indicators, review_signals=signals,
    )
