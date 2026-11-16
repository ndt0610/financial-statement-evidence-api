"""Question execution over one versioned Excel fact and note index."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import Field

from app.adapters import ExcelIngestionResult, SheetHeader
from app.adapters.pdf_adapter import PdfIngestionResult
from app.models.supplemental_fact import SupplementalFact
from app.models import AnswerResult, AnswerStatus, Evidence, FactState, FinancialFact, Period, PeriodType, ReportScope
from app.services.evidence_guard import check_evidence
from app.services.financial_calculator import (
    CalculationResult,
    VerificationStatus,
    absolute_difference,
    balance_difference,
    debt_to_equity,
    gross_margin,
    growth_yoy,
    net_margin,
    roa,
    roe,
)
from app.services.question_router import QuestionIntent, QuestionRoute, route_question
from app.services.vas_normalizer import fiscal_start, shift_year
from app.services.vas_normalizer import fold


class QAResponse(AnswerResult):
    answer: str = Field(min_length=1)
    route: QuestionRoute
    verification_status: VerificationStatus
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class DocumentIndex:
    result: ExcelIngestionResult | PdfIngestionResult
    by_line: dict[tuple[str, str, int, ReportScope], list[FinancialFact]]
    supplemental_facts: tuple[SupplementalFact, ...] = ()

    @classmethod
    def from_ingestion(cls, result: ExcelIngestionResult | PdfIngestionResult, *, supplemental_facts: tuple[SupplementalFact, ...] = ()) -> DocumentIndex:
        indexed: dict[tuple[str, str, int, ReportScope], list[FinancialFact]] = defaultdict(list)
        for fact in result.facts:
            indexed[(fact.line_code.form_code, fact.line_code.code, int(fact.period.fiscal_year_label.removeprefix("FY")), fact.document.report_scope)].append(fact)
        return cls(result, dict(indexed), supplemental_facts)

    @property
    def years(self) -> set[int]:
        return {key[2] for key in self.by_line}

    @property
    def scopes(self) -> set[ReportScope]:
        return {key[3] for key in self.by_line}

    def get(self, form: str, code: str, year: int, scope: ReportScope) -> FinancialFact | None:
        candidates = self.by_line.get((form, code, year, scope), [])
        if len(candidates) > 1:
            return None  # ambiguity is insufficient evidence, never choose a row arbitrarily
        return candidates[0] if candidates else None

    def to_json_dict(self) -> dict:
        return {
            "source_kind": "pdf" if isinstance(self.result, PdfIngestionResult) else "excel",
            "review_history": list(self.result.review_history) if isinstance(self.result, PdfIngestionResult) else [],
            "diagnostics": list(self.result.diagnostics) if isinstance(self.result, PdfIngestionResult) else [],
            "document_hash": self.result.document_hash,
            "facts": [fact.model_dump(mode="json") for fact in self.result.facts],
            "supplemental_facts": [fact.model_dump(mode="json") for fact in self.supplemental_facts],
            "notes": {key: evidence.model_dump(mode="json") for key, evidence in self.result.notes.items()},
            "headers": {
                name: {
                    "sheet_name": header.sheet_name,
                    "form_code": header.form_code,
                    "company_name": header.company_name,
                    "company_evidence": header.company_evidence.model_dump(mode="json"),
                    "report_scope": header.report_scope.value,
                    "scope_evidence": header.scope_evidence.model_dump(mode="json"),
                    "unit_label": header.unit_label,
                    "unit_multiplier": str(header.unit_multiplier),
                    "unit_evidence": header.unit_evidence.model_dump(mode="json"),
                    "fiscal_end": header.fiscal_end.isoformat(),
                    "period_columns": {str(column): period.model_dump(mode="json") for column, period in header.period_columns.items()},
                    "data_start_row": header.data_start_row,
                    "label_column": header.label_column,
                    "code_column": header.code_column,
                    "note_column": header.note_column,
                }
                for name, header in self.result.headers.items()
            },
        }

    @classmethod
    def from_json_dict(cls, data: dict) -> DocumentIndex:
        headers = {
            name: SheetHeader(
                sheet_name=item["sheet_name"], form_code=item["form_code"],
                company_name=item["company_name"],
                company_evidence=Evidence.model_validate(item["company_evidence"]),
                report_scope=ReportScope(item["report_scope"]),
                scope_evidence=Evidence.model_validate(item["scope_evidence"]),
                unit_label=item["unit_label"], unit_multiplier=Decimal(item["unit_multiplier"]),
                unit_evidence=Evidence.model_validate(item["unit_evidence"]),
                fiscal_end=date.fromisoformat(item["fiscal_end"]),
                period_columns={int(column): Period.model_validate(period) for column, period in item["period_columns"].items()},
                data_start_row=item["data_start_row"], label_column=item["label_column"],
                code_column=item["code_column"], note_column=item["note_column"],
            )
            for name, item in data["headers"].items()
        }
        result_class = PdfIngestionResult if data.get("source_kind") == "pdf" else ExcelIngestionResult
        if data.get("source_kind", "excel") not in {"pdf", "excel"}:
            raise ValueError("unsupported index source kind")
        extra = {"review_history": tuple(data.get("review_history", [])),
                 "diagnostics": tuple(data.get("diagnostics", []))} if result_class is PdfIngestionResult else {}
        result = result_class(document_hash=data["document_hash"], headers=headers,
                              facts=[FinancialFact.model_validate(item) for item in data["facts"]],
                              notes={key: Evidence.model_validate(item) for key, item in data["notes"].items()}, **extra)
        if any(fact.document.document_hash != result.document_hash for fact in result.facts):
            raise ValueError("index facts do not match document hash")
        supplements=tuple(SupplementalFact.model_validate(item) for item in data.get("supplemental_facts",[]))
        if any(item.document_hash!=result.document_hash for item in supplements):
            raise ValueError("supplemental facts do not match document hash")
        return cls.from_ingestion(result,supplemental_facts=supplements)


def _period_for(index: DocumentIndex, intent: QuestionIntent) -> Period | None:
    if intent.target is None:
        return None
    header = next((item for item in index.result.headers.values() if item.form_code == intent.target[0]), None)
    if header is None:
        return None
    year = intent.years[0] if intent.years else max(index.years)
    end = shift_year(header.fiscal_end, year - header.fiscal_end.year)
    if intent.target[0] == "B01-DN":
        return Period(period_type=PeriodType.INSTANT, as_of_date=fiscal_start(end) if intent.opening_balance else end,
                      fiscal_year_label=f"FY{year - 1 if intent.opening_balance else year}")
    return Period(period_type=PeriodType.DURATION, period_start=fiscal_start(end), period_end=end, fiscal_year_label=f"FY{year}")


def _response(
    index: DocumentIndex,
    intent: QuestionIntent,
    status: AnswerStatus,
    answer: str,
    *,
    value: Decimal | None = None,
    unit: str | None = None,
    period: Period | None = None,
    scope: ReportScope | None = None,
    calculation: str | None = None,
    verification: VerificationStatus = VerificationStatus.NOT_APPLICABLE,
    evidences: list[Evidence] | None = None,
    facts: tuple[FinancialFact, ...] = (),
) -> QAResponse:
    return QAResponse(
        status=status, explanation=answer, answer=answer, value=value, unit=unit,
        period=period, report_scope=scope, calculation=calculation,
        evidences=evidences or [], findings=[finding for fact in facts for finding in fact.findings],
        route=intent.route, verification_status=verification, document_hash=index.result.document_hash,
        warnings=list(dict.fromkeys(fact.state.value for fact in facts if fact.state not in {FactState.VERIFIED, FactState.HUMAN_CORRECTED})),
    )


def _missing(index: DocumentIndex, intent: QuestionIntent, scope: ReportScope, *facts: FinancialFact) -> QAResponse:
    decision = check_evidence(*facts)
    return _response(
        index, intent, AnswerStatus.REFUSED_MISSING_DATA, decision.reason or "MISSING_DATA",
        period=facts[0].period if facts else _period_for(index, intent), scope=scope,
        verification=decision.verification_status if not decision.allowed else VerificationStatus.NOT_APPLICABLE,
        evidences=decision.evidences, facts=facts,
    )


def _from_calculation(index: DocumentIndex, intent: QuestionIntent, result: CalculationResult, *facts: FinancialFact) -> QAResponse:
    decision = check_evidence(*facts)
    if not decision.allowed or result.verification_status != VerificationStatus.VERIFIED or result.value is None:
        return _missing(index, intent, result.report_scope, *facts)
    return _response(
        index, intent, AnswerStatus.ANSWERED, result.calculation or "Đã tính từ các fact có chứng cứ",
        value=result.value, unit=result.unit, period=result.period, scope=result.report_scope,
        calculation=result.calculation, verification=result.verification_status,
        evidences=result.evidences, facts=facts,
    )


def answer_question(index: DocumentIndex, question: str) -> QAResponse:
    intent = route_question(question, available_years=index.years, available_scopes=index.scopes)
    scope = intent.requested_scope or (next(iter(index.scopes)) if len(index.scopes) == 1 else None)
    if intent.refusal_reason == "SCOPE_MISMATCH":
        header = next((item for item in index.result.headers.values() if item.form_code == (intent.target or ("B01-DN", ""))[0]), None)
        evidence = [header.scope_evidence] if header else []
        return _response(index, intent, AnswerStatus.REFUSED_SCOPE_MISMATCH, "File chỉ chứa báo cáo riêng" if ReportScope.SEPARATE in index.scopes else "File không chứa phạm vi yêu cầu", period=_period_for(index, intent), scope=intent.requested_scope, evidences=evidence)
    if intent.refusal_reason == "MISSING_PERIOD":
        return _response(index, intent, AnswerStatus.REFUSED_MISSING_DATA, "Không có kỳ báo cáo được hỏi", period=_period_for(index, intent), scope=scope)
    if scope is None:
        return _response(index, intent, AnswerStatus.REFUSED_MISSING_DATA, "Cần chỉ rõ báo cáo riêng hay hợp nhất")
    year = intent.years[0] if intent.years else max(index.years)
    target = intent.target
    lookup_year = year - 1 if intent.opening_balance else year
    if intent.route == QuestionRoute.FACT_LOOKUP:
        fact = index.get(*target, lookup_year, scope) if target else None
        if fact is None:
            return _missing(index, intent, scope)
        decision = check_evidence(fact)
        if not decision.allowed:
            return _missing(index, intent, scope, fact)
        return _response(index, intent, AnswerStatus.ANSWERED, f"{fact.original_label}: {fact.value} {fact.unit_label}", value=fact.value, unit=fact.unit_label, period=fact.period, scope=scope, verification=decision.verification_status, evidences=decision.evidences, facts=(fact,))
    if intent.route == QuestionRoute.HYBRID_EXPLANATION:
        fact = index.get(*target, lookup_year, scope) if target else None
        if fact is None:
            return _missing(index, intent, scope)
        note_ref = intent.note_ref or fact.note_ref
        if note_ref is None:
            return _missing(index, intent, scope, fact)
        indexed_note = index.result.notes.get(fact.note_ref) if fact.note_ref else None
        if indexed_note is None or indexed_note != fact.note_evidence:
            return _response(index, intent, AnswerStatus.REFUSED_MISSING_DATA, "MISSING_NOTE_EVIDENCE", period=fact.period, scope=scope, verification=VerificationStatus.UNVERIFIED, evidences=fact.evidence, facts=(fact,))
        decision = check_evidence(fact, require_note=note_ref)
        if not decision.allowed:
            return _response(index, intent, AnswerStatus.REFUSED_MISSING_DATA, decision.reason or "MISSING_NOTE_EVIDENCE", period=fact.period, scope=scope, verification=decision.verification_status, evidences=decision.evidences, facts=(fact,))
        note_text = indexed_note.observed_text
        return _response(index, intent, AnswerStatus.ANSWERED, f"{fact.original_label}: {fact.value} {fact.unit_label}. Thuyết minh {fact.note_ref}: {note_text}", value=fact.value, unit=fact.unit_label, period=fact.period, scope=scope, verification=decision.verification_status, evidences=decision.evidences, facts=(fact,))
    if intent.route != QuestionRoute.METRIC_COMPUTATION:
        return _missing(index, intent, scope)
    metric = intent.metric
    try:
        if metric in {"ABSOLUTE_DIFFERENCE", "GROWTH_YOY"} and target:
            current = index.get(*target, year, scope)
            prior_year = intent.years[1] if len(intent.years) > 1 else year - 1
            prior = index.get(*target, prior_year, scope)
            if current is None or prior is None:
                return _missing(index, intent, scope, *(fact for fact in (current, prior) if fact))
            result = absolute_difference(current, prior, year_over_year=True) if metric == "ABSOLUTE_DIFFERENCE" else growth_yoy(current, prior)
            return _from_calculation(index, intent, result, current, prior)
        if metric == "DEBT_TO_EQUITY":
            debt, equity = index.get("B01-DN", "300", year, scope), index.get("B01-DN", "400", year, scope)
            if debt is None or equity is None:
                return _missing(index, intent, scope, *(fact for fact in (debt, equity) if fact))
            return _from_calculation(index, intent, debt_to_equity(debt, equity), debt, equity)
        if metric == "BALANCE_CHECK":
            query_text = fold(question)
            if "tong nguon von" not in query_text and "no phai tra" in query_text and "von chu so huu" in query_text:
                assets = index.get("B01-DN", "270", year, scope)
                debt = index.get("B01-DN", "300", year, scope)
                equity = index.get("B01-DN", "400", year, scope)
                operands = tuple(fact for fact in (assets, debt, equity) if fact)
                if len(operands) != 3:
                    return _missing(index, intent, scope, *operands)
                decision = check_evidence(*operands, block_reconciliation=True)
                if not decision.allowed:
                    return _response(index, intent, AnswerStatus.REFUSED_MISSING_DATA,
                                     decision.reason or "MISSING_DATA", period=assets.period, scope=scope,
                                     verification=decision.verification_status,
                                     evidences=decision.evidences, facts=operands)
                return _from_calculation(index, intent, balance_difference(assets, debt, equity), *operands)
            source = index.get("B01-DN", "440", year, scope)
            debt = index.get("B01-DN", "300", year, scope)
            equity = index.get("B01-DN", "400", year, scope)
            operands = tuple(fact for fact in (source, debt, equity) if fact)
            if source is None or not check_evidence(*operands).allowed:
                return _missing(index, intent, scope, *operands)
            assets = index.get("B01-DN", "270", year, scope)
            if assets is None:
                return _missing(index, intent, scope, *operands)
            return _from_calculation(index, intent, absolute_difference(assets, source), assets, source)
        if metric in {"GROSS_MARGIN", "NET_MARGIN"}:
            numerator_code = "20" if metric == "GROSS_MARGIN" else "60"
            numerator = index.get("B02-DN", numerator_code, year, scope)
            revenue = index.get("B02-DN", "10", year, scope)
            if numerator is None or revenue is None:
                return _missing(index, intent, scope, *(fact for fact in (numerator, revenue) if fact))
            result = gross_margin(numerator, revenue) if metric == "GROSS_MARGIN" else net_margin(numerator, revenue)
            return _from_calculation(index, intent, result, numerator, revenue)
        if metric in {"ROA", "ROE"}:
            income = index.get("B02-DN", "60", year, scope)
            code = "270" if metric == "ROA" else "400"
            closing = index.get("B01-DN", code, year, scope)
            if income is None or closing is None:
                return _missing(index, intent, scope, *(fact for fact in (income, closing) if fact))
            query_text = question.casefold()
            basis = "closing" if any(phrase in query_text for phrase in ("cuối kỳ", "cuối năm", "closing")) else "average"
            opening = index.get("B01-DN", code, year - 1, scope) if basis == "average" else None
            if basis == "average" and opening is None:
                return _missing(index, intent, scope, income, closing)
            result = roa(income, closing, basis=basis, opening_assets=opening) if metric == "ROA" else roe(income, closing, basis=basis, opening_equity=opening)
            return _from_calculation(index, intent, result, *(fact for fact in (income, closing, opening) if fact))
    except ValueError as error:
        return _response(index, intent, AnswerStatus.REFUSED_MISSING_DATA, str(error), period=_period_for(index, intent), scope=scope)
    return _missing(index, intent, scope)
