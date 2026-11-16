"""Independent cache check for the fixture's simple addition formulas only."""

from __future__ import annotations

import re
from decimal import Decimal
from enum import StrEnum

from openpyxl.worksheet.worksheet import Worksheet


ADDITION = re.compile(r"^=([A-Z]+[1-9][0-9]*)\+([A-Z]+[1-9][0-9]*)$")


class CacheCheck(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    MISSING_CACHE = "MISSING_CACHE"
    UNSUPPORTED = "UNSUPPORTED"


def check_addition_cache(formula_sheet: Worksheet, value_sheet: Worksheet, cell: str) -> CacheCheck:
    """Compare an A1+A1 formula to source cells, using Decimal and no Excel engine.

    Only literal two-cell additions with numeric, non-formula operands are supported.
    All other formulas return UNSUPPORTED and must remain unverified elsewhere.
    """
    formula = formula_sheet[cell].value
    if not isinstance(formula, str) or (match := ADDITION.fullmatch(formula)) is None:
        return CacheCheck.UNSUPPORTED
    operands: list[Decimal] = []
    for address in match.groups():
        source_formula = formula_sheet[address].value
        source_value = value_sheet[address].value
        if isinstance(source_formula, str) and source_formula.startswith("="):
            return CacheCheck.UNSUPPORTED
        if isinstance(source_value, bool) or not isinstance(source_value, (int, Decimal)):
            return CacheCheck.UNSUPPORTED
        operands.append(Decimal(str(source_value)))
    cached = value_sheet[cell].value
    if cached is None:
        return CacheCheck.MISSING_CACHE
    if isinstance(cached, bool) or not isinstance(cached, (int, Decimal)):
        return CacheCheck.UNSUPPORTED
    return CacheCheck.MATCH if Decimal(str(cached)) == sum(operands) else CacheCheck.MISMATCH
