"""Evidence gates for the 25 interpretation traps in the business contract.

These gates assess a proposed claim. They do not generate claims or upgrade a
fact's verification status. Unknown claims fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class ClaimContext:
    numbers: dict[str, Decimal] = field(default_factory=dict)
    flags: dict[str, bool] = field(default_factory=dict)

    def n(self, key: str) -> Decimal | None:
        return self.numbers.get(key)

    def yes(self, key: str) -> bool:
        return self.flags.get(key, False) is True


@dataclass(frozen=True)
class ClaimDecision:
    allowed: bool
    conclusion_key: str
    reason_code: str


def guard_conclusion(claim_key: str, context: ClaimContext) -> ClaimDecision:
    c=context
    n=c.n
    # A positive assertion requires its own compatible evidence. None never
    # behaves as zero, and a clean opinion never stands in for liquidity.
    rules={
        "INSOLVENT_FROM_CR": (False,"RATIO_NOT_PAYMENT_FORECAST"),
        "LIQUIDITY_IMPROVED": (n("cr_now") is not None and n("cr_before") is not None and n("qr_now") is not None and n("qr_before") is not None and n("cr_now")>n("cr_before") and n("qr_now")>=n("qr_before") and c.yes("cash_access_checked"),"CHECK_QUICK_RATIO_AND_ACCESS"),
        "ALL_CASH_FREE": (c.yes("cash_restriction_disclosure_checked") and n("restricted_cash")==0,"RESTRICTED_CASH_UNKNOWN_OR_POSITIVE"),
        "CANNOT_PAY_INTEREST_FROM_ICR": (False,"ICR_NOT_PAYMENT_TEST"),
        "INFINITE_ICR": (False,"ZERO_INTEREST_IS_NOT_APPLICABLE"),
        "COVENANT_BREACH": (n("dscr") is not None and n("covenant_min") is not None and n("dscr")<n("covenant_min") and c.yes("same_contract_definition") and c.yes("waiver_checked") and not c.yes("waiver_exists"),"NEED_CONTRACT_AND_WAIVER"),
        "LOW_LEVERAGE_FROM_NEGATIVE_MULTIPLE": (n("ebitda") is not None and n("ebitda")>0 and c.yes("peer_or_contract_target"),"NEGATIVE_EBITDA_INVALID_MULTIPLE"),
        "HEALTHY_ROE": (n("net_income") is not None and n("equity") is not None and n("net_income")>0 and n("equity")>0 and c.yes("roe_context_checked"),"LOSS_AND_NEGATIVE_EQUITY_TRAP"),
        "ROE_GAIN_IS_OPERATIONAL": (c.yes("dupont_checked") and c.yes("margin_or_turnover_improved") and c.yes("equity_base_checked"),"CHECK_DUPONT_AND_EQUITY_BASE"),
        "PARENT_ROE": (c.yes("parent_earnings") and c.yes("parent_equity"),"PARENT_SCOPE_MISMATCH"),
        "RECURRING_EARNINGS": (c.yes("nonrecurring_items_quantified") and c.yes("recurring_profit_positive"),"NONRECURRING_GAIN_NOT_REMOVED"),
        "ORGANIC_GROWTH": (c.yes("acquisition_impact_quantified") and c.yes("organic_growth_positive"),"ACQUISITION_EFFECT_UNKNOWN"),
        "CASH_QUALITY_IMPROVED": (c.yes("payables_effect_removed") and c.yes("multi_period_cfo_checked"),"PAYABLES_MAY_DRIVE_CFO"),
        "OBSOLETE_INVENTORY": (c.yes("inventory_aging_or_writeoff_evidence"),"VALUE_GROWTH_NOT_OBSOLESCENCE"),
        "OVERDUE_RECEIVABLES": (c.yes("aging_schedule_evidence"),"DSO_NOT_AGING"),
        "CAPEX_REDUCED_CFO": (False,"CAPEX_IS_INVESTING_CASH_FLOW"),
        "OPERATING_CASH_GROWTH": (n("cfo_now") is not None and n("cfo_before") is not None and n("cfo_now")>n("cfo_before"),"CASH_BALANCE_MAY_BE_FINANCED"),
        "STANDARD_YOY_GROWTH": (n("base") is not None and n("base")>0 and c.yes("comparable_periods"),"NONPOSITIVE_OR_NONCOMPARABLE_BASE"),
        "PERCENTAGE_POINT_CHANGE": (n("before_pct") is not None and n("after_pct") is not None and n("stated_pp") is not None and n("stated_pp")==n("after_pct")-n("before_pct"),"PERCENT_VS_PERCENTAGE_POINTS"),
        "YOY_COMPARABLE": (c.yes("same_period_length") and c.yes("same_scope"),"DURATION_OR_SCOPE_MISMATCH"),
        "B01_INVENTORY_EQUALS_B03_CASH_EFFECT": (n("balance_change") is not None and n("cash_effect") is not None and n("balance_change")==-n("cash_effect") and c.yes("reclassification_reconciled"),"BALANCE_NOT_CASH_FLOW"),
        "SECTOR_BENCHMARK": (n("peer_count") is not None and n("peer_count")>=5 and c.yes("peer_comparability_checked"),"INSUFFICIENT_PEER_SET"),
        "HEALTHY_FROM_CLEAN_AUDIT": (False,"AUDIT_OPINION_NOT_HEALTH_RATING"),
        "SUSTAINABLE_CFO_CONVERSION": (c.yes("multi_year_bridge_checked") and n("years_observed") is not None and n("years_observed")>=3 and c.yes("no_adverse_countertrend"),"ONE_YEAR_NOT_SUSTAINABILITY"),
        "INVENTORY_TURNOVER_IMPROVED": (n("gross_dio_now") is not None and n("gross_dio_before") is not None and n("net_dio_now") is not None and n("net_dio_before") is not None and n("gross_dio_now")<n("gross_dio_before") and n("net_dio_now")<n("net_dio_before") and c.yes("provision_effect_checked"),"NET_DIO_MAY_FALL_FROM_PROVISION"),
    }
    if claim_key not in rules:
        return ClaimDecision(False,claim_key,"UNKNOWN_CLAIM")
    allowed,reason=rules[claim_key]
    return ClaimDecision(bool(allowed),claim_key,"EVIDENCE_COMPLETE" if allowed else reason)
