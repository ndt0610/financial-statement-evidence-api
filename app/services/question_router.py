"""Small rule-based Vietnamese router over known financial line codes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.models import ReportScope
from app.services.vas_normalizer import fold


class QuestionRoute(StrEnum):
    FACT_LOOKUP = "FACT_LOOKUP"
    METRIC_COMPUTATION = "METRIC_COMPUTATION"
    HYBRID_EXPLANATION = "HYBRID_EXPLANATION"
    REFUSAL = "REFUSAL"


@dataclass(frozen=True)
class QuestionIntent:
    route: QuestionRoute
    target: tuple[str, str] | None
    years: tuple[int, ...]
    requested_scope: ReportScope | None
    metric: str | None = None
    note_ref: str | None = None
    refusal_reason: str | None = None
    opening_balance: bool = False


ALIASES: tuple[tuple[str, tuple[str, str]], ...] = (
    ("cac khoan giam tru doanh thu", ("B02-DN", "02")),
    ("doanh thu thuan", ("B02-DN", "10")),
    ("doanh thu ban hang", ("B02-DN", "01")),
    ("gia von hang ban", ("B02-DN", "11")),
    ("tong nguon von", ("B01-DN", "440")),
    ("tong tai san", ("B01-DN", "270")),
    ("tong cong tai san", ("B01-DN", "270")),
    ("tien va cac khoan tuong duong tien", ("B01-DN", "110")),
    ("tien va tuong duong tien", ("B01-DN", "110")),
    ("hang ton kho", ("B01-DN", "140")),
    ("no phai tra", ("B01-DN", "300")),
    ("von chu so huu", ("B01-DN", "400")),
    ("von thuoc chu so huu", ("B01-DN", "400")),
    ("loi nhuan gop", ("B02-DN", "20")),
    ("loi nhuan sau thue", ("B02-DN", "60")),
)


def normalize_note_ref(value: str) -> str:
    match = re.fullmatch(r"([IVX]+)\.(\d+)", value.strip(), re.IGNORECASE)
    if match is None:
        raise ValueError("invalid note reference")
    return f"{match.group(1).upper()}.{int(match.group(2))}"


def route_question(
    question: str,
    *,
    available_years: set[int],
    available_scopes: set[ReportScope],
) -> QuestionIntent:
    query = " ".join(fold(question).split())
    years = tuple(dict.fromkeys(int(value) for value in re.findall(r"\b20\d{2}\b", query)))
    requested_scope = ReportScope.CONSOLIDATED if "hop nhat" in query else ReportScope.SEPARATE if "rieng" in query else None
    target = next((code for phrase, code in ALIASES if phrase in query), None)
    opening_balance = bool(target and target[0] == "B01-DN" and ("dau nam" in query or "dau ky" in query))
    note_match = re.search(r"\b([ivx]+\.\d+)\b", query)
    note_ref = normalize_note_ref(note_match.group(1)) if note_match else None
    if requested_scope is not None and requested_scope not in available_scopes:
        return QuestionIntent(QuestionRoute.REFUSAL, target, years, requested_scope, refusal_reason="SCOPE_MISMATCH", opening_balance=opening_balance)
    if any(year not in available_years for year in years):
        return QuestionIntent(QuestionRoute.REFUSAL, target, years, requested_scope, refusal_reason="MISSING_PERIOD", opening_balance=opening_balance)
    if "thuyet minh" in query or (note_ref and any(word in query for word in ("mo ta", "gom", "vi sao"))):
        return QuestionIntent(QuestionRoute.HYBRID_EXPLANATION, target, years, requested_scope, note_ref=note_ref, opening_balance=opening_balance)
    metric = None
    if "roa" in query:
        metric = "ROA"
    elif "roe" in query:
        metric = "ROE"
    elif "gross margin" in query or "bien loi nhuan gop" in query:
        metric = "GROSS_MARGIN"
    elif "net margin" in query or "bien loi nhuan rong" in query:
        metric = "NET_MARGIN"
    elif "no phai tra" in query and "von chu so huu" in query and any(word in query for word in ("ty so", "ty le", "he so")):
        metric = "DEBT_TO_EQUITY"
    elif "doi chieu" in query or "can doi" in query:
        metric = "BALANCE_CHECK"
    elif "tang truong" in query or "yoy" in query or ("tang" in query and "%" in query):
        metric = "GROWTH_YOY"
    elif any(word in query for word in ("tang bao nhieu", "giam bao nhieu", "chenh lech", "so voi")):
        metric = "ABSOLUTE_DIFFERENCE"
    return QuestionIntent(
        QuestionRoute.METRIC_COMPUTATION if metric else QuestionRoute.FACT_LOOKUP,
        target, years, requested_scope, metric=metric, note_ref=note_ref, opening_balance=opening_balance,
    )
