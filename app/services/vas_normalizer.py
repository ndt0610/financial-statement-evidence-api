"""Small, explicit Vietnamese financial-statement normalization rules for M1."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.models import Period, PeriodType


UNIT_FACTORS = {
    "dong": ("đồng", Decimal("1")),
    "nghin dong": ("nghìn đồng", Decimal("1000")),
    "trieu dong": ("triệu đồng", Decimal("1000000")),
    "ty dong": ("tỷ đồng", Decimal("1000000000")),
}
DATE_DMY = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
DATE_WORDS = re.compile(r"\bngay\s+(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s+(\d{4})\b")
YEAR = re.compile(r"\b(?:nam\s+)?(20\d{2})\b")


def fold(text: str) -> str:
    """Accent-insensitive matching, retaining original text in the fact/evidence."""
    text = text.casefold().replace("đ", "d")
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")


def normalize_label(text: str) -> str:
    return " ".join(text.strip().casefold().split())


def normalize_unit(text: str) -> tuple[str, Decimal]:
    value = " ".join(fold(text).strip().split())
    if ":" in value:
        value = value.split(":", 1)[1].strip()
    if value not in UNIT_FACTORS:
        raise ValueError(f"unsupported accounting unit: {text!r}")
    return UNIT_FACTORS[value]


def parse_accounting_number(value: object) -> Decimal | None:
    """Parse Excel numerics and VN/US formatted strings without float arithmetic."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a financial amount")
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    if not isinstance(value, str):
        raise ValueError(f"unsupported amount type: {type(value).__name__}")
    raw = value.strip().replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    if raw in {"", "-", "–", "—"}:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1]
    elif raw.startswith("-"):
        negative = True
        raw = raw[1:]
    if not raw or not re.fullmatch(r"\d[\d.,]*", raw):
        raise ValueError(f"invalid accounting amount: {value!r}")
    if "." in raw and "," in raw:
        decimal_mark = "." if raw.rfind(".") > raw.rfind(",") else ","
        thousands_mark = "," if decimal_mark == "." else "."
        raw = raw.replace(thousands_mark, "").replace(decimal_mark, ".")
    elif "," in raw:
        parts = raw.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(",", ".")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            raw = raw.replace(".", "")
    try:
        amount = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError(f"invalid accounting amount: {value!r}") from error
    return -amount if negative else amount


def extract_date(text: str) -> date | None:
    normalized = fold(text)
    match = DATE_DMY.search(normalized) or DATE_WORDS.search(normalized)
    if match is None:
        return None
    day, month, year = map(int, match.groups())
    return date(year, month, day)


def shift_year(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:  # 29 February -> 28 February in a non-leap year
        return value.replace(year=value.year + years, day=28)


def fiscal_start(fiscal_end: date) -> date:
    return shift_year(fiscal_end, -1) + timedelta(days=1)


def period_from_column(header: str, form_code: str, fiscal_end: date) -> Period | None:
    """Map an explicit date/year or relative heading to an instant/duration."""
    label = " ".join(fold(header).split())
    is_balance = form_code.startswith("B01")
    is_income = form_code.startswith(("B02", "B03"))
    if not (is_balance or is_income):
        return None
    start = fiscal_start(fiscal_end)
    if is_balance:
        explicit = extract_date(header)
        if explicit is not None:
            return Period(period_type=PeriodType.INSTANT, as_of_date=explicit, fiscal_year_label=f"FY{explicit.year}")
        if "so cuoi nam" in label or "cuoi ky" in label:
            return Period(period_type=PeriodType.INSTANT, as_of_date=fiscal_end, fiscal_year_label=f"FY{fiscal_end.year}")
        if "so dau nam" in label or "dau ky" in label:
            return Period(period_type=PeriodType.INSTANT, as_of_date=start, fiscal_year_label=f"FY{fiscal_end.year - 1}")
        return None
    if "nam nay" in label:
        end = fiscal_end
        beginning = start
    elif "nam truoc" in label:
        end = shift_year(fiscal_end, -1)
        beginning = shift_year(start, -1)
    elif (match := YEAR.search(label)) is not None:
        year = int(match.group(1))
        end = shift_year(fiscal_end, year - fiscal_end.year)
        beginning = shift_year(start, year - fiscal_end.year)
    else:
        return None
    return Period(period_type=PeriodType.DURATION, period_start=beginning, period_end=end, fiscal_year_label=f"FY{end.year}")
