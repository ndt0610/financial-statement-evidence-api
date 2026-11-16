"""Deterministic M1 formula-cache checks and scoped accounting reconciliation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal

from openpyxl.utils.cell import range_boundaries

from app.models import Evidence, FactState, FinancialFact, Finding, FindingCode
from app.models.financial_fact import ExcelLocator


CELL = r"[A-Z]{1,3}[1-9][0-9]*"
SUM_RANGE = re.compile(rf"^SUM\(({CELL}):({CELL})\)$", re.IGNORECASE)
CELL_CHAIN = re.compile(rf"^{CELL}(?:[+-]{CELL})*$")
CELL_TOKEN = re.compile(rf"[+-]?{CELL}")
FactIndex = Mapping[tuple[str, str], FinancialFact]


def _formula(fact: FinancialFact) -> str | None:
    for evidence in fact.evidence:
        if isinstance(evidence.locator, ExcelLocator) and evidence.locator.formula:
            return evidence.locator.formula
    return None


def _key(fact: FinancialFact) -> tuple[str, str]:
    locator = fact.evidence[0].locator
    if not isinstance(locator, ExcelLocator):
        raise ValueError("M1 guard requires Excel evidence")
    return locator.sheet_name, locator.cell_range


def calculate_formula(
    formula: str,
    sheet_name: str,
    facts: FactIndex,
    *,
    visiting: frozenset[tuple[str, str]] = frozenset(),
) -> Decimal | None:
    """Evaluate only SUM(A1:A5) and A1+B2-C3 using extracted numeric cells.

    `None` means unsupported syntax, missing operand, or circular dependency.
    Formula dependencies are calculated recursively without trusting their cache.
    """
    if not formula.startswith("="):
        return None
    body = formula[1:].replace(" ", "").upper()
    if match := SUM_RANGE.fullmatch(body):
        min_col, min_row, max_col, max_row = range_boundaries(f"{match.group(1)}:{match.group(2)}")
        if min_col > max_col or min_row > max_row:
            return None
        from openpyxl.utils.cell import get_column_letter

        operands = [f"{get_column_letter(col)}{row}" for row in range(min_row, max_row + 1) for col in range(min_col, max_col + 1)]
        signs = [1] * len(operands)
    elif CELL_CHAIN.fullmatch(body):
        tokens = CELL_TOKEN.findall(body)
        operands = [token.lstrip("+-") for token in tokens]
        signs = [-1 if token.startswith("-") else 1 for token in tokens]
    else:
        return None
    total = Decimal("0")
    for address, sign in zip(operands, signs):
        key = (sheet_name, address)
        if key in visiting or key not in facts:
            return None
        operand = facts[key]
        if nested := _formula(operand):
            value = calculate_formula(nested, sheet_name, facts, visiting=visiting | {key})
        else:
            value = operand.value
        if value is None:
            return None
        total += sign * value
    return total


def verify_facts(facts: Sequence[FinancialFact]) -> list[FinancialFact]:
    """Move extracted facts through the M0 state guards after checking their cells."""
    index = {_key(fact): fact for fact in facts}
    if len(index) != len(facts):
        raise ValueError("duplicate fact cell in one workbook")
    checked: list[FinancialFact] = []
    for fact in facts:
        if fact.state != FactState.EXTRACTED:
            raise ValueError("verify_facts accepts extracted facts only")
        formula = _formula(fact)
        if formula is None:
            checked.append(fact.transition(FactState.VERIFIED, source_checked=True) if fact.value is not None else fact)
            continue
        sheet_name, _ = _key(fact)
        calculated = calculate_formula(formula, sheet_name, index, visiting=frozenset({_key(fact)}))
        fact = FinancialFact.model_validate({**fact.model_dump(), "calculated_value": calculated})
        if fact.value is None:
            checked.append(fact.transition(FactState.UNVERIFIED_MISSING_CACHE, source_checked=True, cache_present=False))
        elif calculated is None:
            checked.append(fact)  # unsupported formula: no independent claim
        else:
            matches = fact.value == calculated
            checked.append(fact.transition(
                FactState.VERIFIED if matches else FactState.STALE_CACHE_MISMATCH,
                source_checked=True, cache_present=True, independent_formula_match=matches,
            ))
    return checked


def reconcile_facts(facts: Sequence[FinancialFact], *, tolerance_vnd: Decimal) -> list[FinancialFact]:
    """Compare B01 assets/debt/equity and B03 closing cash to B01 cash.

    Unverified cached values are deliberately excluded. `tolerance_vnd` is
    chosen by the caller for the report's rounding policy, not a global rule.
    """
    if tolerance_vnd < 0:
        raise ValueError("tolerance_vnd must be nonnegative")
    result = list(facts)
    groups: dict[tuple[str, str, object, str], dict[str, tuple[int, FinancialFact]]] = {}
    for index, fact in enumerate(result):
        if fact.document.form_code != "B01-DN" or fact.state != FactState.VERIFIED:
            continue
        key = (fact.document.document_hash, fact.document.report_scope, fact.period.as_of_date, fact.document.accounting_regime)
        groups.setdefault(key, {})[fact.line_code.code] = index, fact
    for group in groups.values():
        if "270" not in group:
            continue
        asset_index, asset = group["270"]
        comparisons: list[tuple[str, Decimal, list[Evidence]]] = []
        if "440" in group:
            source = group["440"][1]
            comparisons.append(("440", source.normalized_value, source.evidence))
        if "300" in group and "400" in group:
            debt, equity = group["300"][1], group["400"][1]
            comparisons.append(("300+400", debt.normalized_value + equity.normalized_value, debt.evidence + equity.evidence))
        for label, other_value, evidence in comparisons:
            difference = abs(asset.normalized_value - other_value)
            if difference > tolerance_vnd:
                finding = Finding(
                    code=FindingCode.RECONCILIATION_WARNING,
                    detail=f"B01-DN code 270 differs from {label} for {asset.period.as_of_date}",
                    tolerance=tolerance_vnd,
                    difference=difference,
                    evidence=asset.evidence + evidence,
                )
                result[asset_index] = result[asset_index].with_reconciliation_warning(finding)
    cash_by_period: dict[tuple[str, object, object, str], FinancialFact] = {}
    for fact in result:
        if fact.document.form_code == "B01-DN" and fact.line_code.code == "110" and fact.state == FactState.VERIFIED:
            key = (fact.document.document_hash, fact.document.report_scope, fact.period.as_of_date, fact.document.accounting_regime)
            cash_by_period[key] = fact
    for index, fact in enumerate(result):
        if fact.document.form_code != "B03-DN" or fact.line_code.code != "70" or fact.state != FactState.VERIFIED:
            continue
        key = (fact.document.document_hash, fact.document.report_scope, fact.period.period_end, fact.document.accounting_regime)
        balance_cash = cash_by_period.get(key)
        if balance_cash is None:
            continue
        difference = abs(fact.normalized_value - balance_cash.normalized_value)
        if difference > tolerance_vnd:
            result[index] = fact.with_reconciliation_warning(Finding(
                code=FindingCode.RECONCILIATION_WARNING,
                detail=f"B03-DN code 70 differs from B01-DN code 110 at {fact.period.period_end}",
                tolerance=tolerance_vnd, difference=difference,
                evidence=fact.evidence + balance_cash.evidence,
            ))
    return result
