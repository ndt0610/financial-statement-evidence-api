"""Explicit TT200 identities; arithmetic checks never change fact state."""
from collections import defaultdict
from decimal import Decimal

from app.models import FactState

# Each expression is target = sum(coefficient * input). B02 expenses are
# displayed as positive costs; a signed negative cost is ambiguous and must be
# reviewed rather than silently applying abs(). B03 values retain their signs.
IDENTITIES = {
    "B01-DN": {
        "current_assets": ("100", (("110", 1), ("120", 1), ("130", 1), ("140", 1), ("150", 1))),
        "noncurrent_assets": ("200", (("210", 1), ("220", 1), ("230", 1), ("240", 1), ("250", 1), ("260", 1))),
        "total_assets": ("270", (("100", 1), ("200", 1))),
        "liabilities": ("300", (("310", 1), ("330", 1))),
        "equity": ("400", (("410", 1), ("430", 1))),
        "total_funding": ("440", (("300", 1), ("400", 1))),
        "balance_sheet_equation": ("270", (("300", 1), ("400", 1))),
        "balance": ("270", (("440", 1),)),
    },
    "B02-DN": {
        "net_revenue": ("10", (("01", 1), ("02", -1))),
        "gross_profit": ("20", (("10", 1), ("11", -1))),
        "operating_profit": ("30", (("20", 1), ("21", 1), ("22", -1), ("24", 1), ("25", -1), ("26", -1))),
        "other_profit": ("40", (("31", 1), ("32", -1))),
        "pretax_profit": ("50", (("30", 1), ("40", 1))),
        "net_profit": ("60", (("50", 1), ("51", -1), ("52", -1))),
    },
    "B03-DN": {
        "operating_cash": ("20", tuple([( "08", 1)] + [(f"{i:02}", 1) for i in range(9, 18)])),
        "investing_cash": ("30", tuple((f"{i:02}", 1) for i in range(21, 28))),
        "financing_cash": ("40", tuple((f"{i:02}", 1) for i in range(31, 37))),
        "net_cash_change": ("50", (("20", 1), ("30", 1), ("40", 1))),
        "closing_cash": ("70", (("50", 1), ("60", 1), ("61", 1))),
    },
}

# Only these top-level B01 components can be omitted from a printed table.
# An exact parent/observed-child residual supports their *aggregate* zero;
# it never creates an individual zero-valued FinancialFact or verifies OCR.
OPTIONAL_B01_COMPONENTS = {
    "current_assets": {"120", "130", "140", "150"},
    "noncurrent_assets": {"210", "220", "230", "240", "250", "260"},
    "liabilities": {"330"},
    "equity": {"430"},
}


def reconcile_statement(facts, form_code, fiscal_year, tolerance=Decimal("0")):
    """Return scoped checks and reasons for missing/duplicate/ambiguous inputs."""
    by_code = defaultdict(list)
    for fact in facts:
        if (fact.line_code.form_code == form_code and
                fact.period.fiscal_year_label == f"FY{fiscal_year}" and
                fact.state != FactState.REJECTED_OCR_ERROR):
            by_code[fact.line_code.code].append(fact)
    checks = []
    for name, (target, terms) in IDENTITIES.get(form_code, {}).items():
        required = [target, *(code for code, _ in terms)]
        missing = [code for code in required if not by_code[code] or by_code[code][0].normalized_value is None]
        duplicate = [code for code in required if len(by_code[code]) > 1]
        ambiguous = [code for code, coefficient in terms if form_code == "B02-DN" and coefficient < 0
                     and len(by_code[code]) == 1 and by_code[code][0].normalized_value is not None
                     and by_code[code][0].normalized_value < 0 and code != "52"]
        missing_terms = set(missing) - {target}
        optional = OPTIONAL_B01_COMPONENTS.get(name, set()) if form_code == "B01-DN" else set()
        inferable = bool(missing_terms) and missing_terms <= optional and target not in missing and any(
            code not in missing_terms for code, _ in terms)
        residual = None
        if target not in missing and not duplicate and not ambiguous and (not missing or inferable):
            residual = by_code[target][0].normalized_value - sum(
                (Decimal(coefficient) * by_code[code][0].normalized_value
                 for code, coefficient in terms if code not in missing_terms), Decimal(0))
        omitted_zero = sorted(missing_terms) if inferable and residual is not None and abs(residual) <= tolerance else []
        status = ("DUPLICATE_INPUT" if duplicate else "AMBIGUOUS_SIGN" if ambiguous else
                  "MISSING_INPUT" if missing and not omitted_zero else
                  "MATH_CONSISTENT" if residual is not None and abs(residual) <= tolerance else "MISMATCH")
        checks.append({"check": name, "target": target, "status": status,
                       "residual_vnd": str(residual) if residual is not None else None,
                       "missing_codes": missing, "duplicate_codes": duplicate, "ambiguous_sign_codes": ambiguous,
                       "omitted_zero_codes": omitted_zero,
                       "zero_inference_scope": "AGGREGATE_ONLY_NOT_A_FACT" if omitted_zero else None,
                       "input_codes": required})
    return checks
