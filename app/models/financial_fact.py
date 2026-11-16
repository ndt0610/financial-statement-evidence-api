"""M0 contract for sourced Vietnamese non-bank financial statement facts.

`state` tracks value verification. `findings` records independent warnings, so a
reconciliation warning never conceals a missing or stale formula cache.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ReportScope(StrEnum):
    SEPARATE = "SEPARATE"
    CONSOLIDATED = "CONSOLIDATED"


class PeriodType(StrEnum):
    INSTANT = "INSTANT"
    DURATION = "DURATION"


class FactState(StrEnum):
    EXTRACTED = "EXTRACTED"
    VERIFIED = "VERIFIED"
    HUMAN_CORRECTED = "HUMAN_CORRECTED"
    REJECTED_OCR_ERROR = "REJECTED_OCR_ERROR"
    UNVERIFIED_MISSING_CACHE = "UNVERIFIED_MISSING_CACHE"
    STALE_CACHE_MISMATCH = "STALE_CACHE_MISMATCH"


class FindingCode(StrEnum):
    RECONCILIATION_WARNING = "RECONCILIATION_WARNING"


class AnswerStatus(StrEnum):
    ANSWERED = "ANSWERED"
    REFUSED_SCOPE_MISMATCH = "REFUSED_SCOPE_MISMATCH"
    REFUSED_MISSING_DATA = "REFUSED_MISSING_DATA"


class Period(StrictModel):
    period_type: PeriodType
    as_of_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    fiscal_year_label: str

    @model_validator(mode="after")
    def check_dates(self) -> Period:
        if self.period_type == PeriodType.INSTANT:
            if self.as_of_date is None or self.period_start is not None or self.period_end is not None:
                raise ValueError("instant requires only as_of_date")
        elif self.as_of_date is not None or self.period_start is None or self.period_end is None:
            raise ValueError("duration requires period_start and period_end only")
        elif self.period_start > self.period_end:
            raise ValueError("period_start must precede period_end")
        return self


class DocumentContext(StrictModel):
    company_name: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    report_scope: ReportScope
    accounting_regime: str = Field(min_length=1)
    form_code: str = Field(min_length=1)
    fiscal_year_label: str
    fiscal_period_start: date
    fiscal_period_end: date
    prepared_on: date | None = None
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str = Field(min_length=1)
    data_origin: Literal["SYNTHETIC", "REAL"]

    @model_validator(mode="after")
    def check_fiscal_period(self) -> DocumentContext:
        if self.fiscal_period_start > self.fiscal_period_end:
            raise ValueError("invalid fiscal period")
        return self


class LineItemCode(StrictModel):
    accounting_regime: str = Field(min_length=1)
    form_code: str = Field(min_length=1)
    code: str = Field(min_length=1)


class ExcelLocator(StrictModel):
    source_kind: Literal["excel"] = "excel"
    sheet_name: str = Field(min_length=1)
    cell_range: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*(?::[A-Z]+[1-9][0-9]*)?$")
    formula: str | None = None

    @field_validator("formula")
    @classmethod
    def check_formula(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("="):
            raise ValueError("Excel formula must start with =")
        return value


class PDFLocator(StrictModel):
    source_kind: Literal["pdf"] = "pdf"
    page_number: int = Field(ge=1)
    region: str | None = None
    table_id: str | None = None
    bounding_box: tuple[float, float, float, float] | None = None
    coordinate_origin: Literal["TOPLEFT", "BOTTOMLEFT"] | None = None

    @model_validator(mode="after")
    def check_region(self) -> PDFLocator:
        if self.bounding_box is None:
            if self.coordinate_origin is not None:
                raise ValueError("coordinate origin requires a bounding box")
            return self
        left, top, right, bottom = self.bounding_box
        if self.coordinate_origin is None or left < 0 or right <= left or min(top, bottom) < 0:
            raise ValueError("invalid PDF bounding box")
        if (self.coordinate_origin == "TOPLEFT" and top >= bottom) or (self.coordinate_origin == "BOTTOMLEFT" and top <= bottom):
            raise ValueError("PDF bounding box orientation does not match origin")
        return self


Locator = Annotated[ExcelLocator | PDFLocator, Field(discriminator="source_kind")]


class Evidence(StrictModel):
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: Locator
    observed_text: str | None = None


class Finding(StrictModel):
    code: FindingCode
    detail: str = Field(min_length=1)
    tolerance: Decimal = Field(ge=0)
    difference: Decimal = Field(gt=0)
    evidence: list[Evidence] = Field(min_length=1)


class VerificationRecord(StrictModel):
    source_checked: bool
    cache_present: bool | None = None
    independent_formula_match: bool | None = None


class FinancialFact(StrictModel):
    document: DocumentContext
    line_code: LineItemCode
    original_label: str = Field(min_length=1)
    normalized_label: str = Field(min_length=1)
    note_ref: str | None = None
    note_link_verified: bool = False
    note_evidence: Evidence | None = None
    period: Period
    raw_value: str | None = None
    zero_from_dash: bool = False  # PDF numeric cell explicitly printed as a dash, with located evidence
    value: Decimal | None = None
    currency: str = Field(min_length=1)
    unit_label: str = Field(min_length=1)
    unit_multiplier: Decimal = Field(gt=0)
    normalized_value: Decimal | None = None
    calculated_value: Decimal | None = None  # independent trial result; never substitutes for a missing cache
    evidence: list[Evidence] = Field(min_length=1)
    state: FactState = FactState.EXTRACTED
    verification: VerificationRecord | None = None
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_consistency(self) -> FinancialFact:
        doc = self.document
        if (self.line_code.accounting_regime, self.line_code.form_code) != (doc.accounting_regime, doc.form_code):
            raise ValueError("line code namespace must match document")
        # A FY2025 filing can contain a FY2024 comparative column.
        if any(item.document_hash != doc.document_hash for item in self.evidence):
            raise ValueError("evidence must point to document version")
        if self.note_link_verified and (not self.note_ref or self.note_evidence is None):
            raise ValueError("verified note link requires note_ref and located note evidence")
        if self.note_evidence is not None and self.note_evidence.document_hash != doc.document_hash:
            raise ValueError("note evidence must point to document version")
        if self.value is None and self.normalized_value is not None:
            raise ValueError("normalized value requires numeric value")
        if self.value is not None and self.normalized_value != self.value * self.unit_multiplier:
            raise ValueError("normalized value must equal value × unit_multiplier")
        if self.zero_from_dash:
            if self.raw_value not in {"-", "–", "—"} or self.value != 0:
                raise ValueError("zero_from_dash requires an explicit dash and numeric zero")
            if not any(isinstance(item.locator, PDFLocator) and item.locator.bounding_box is not None
                       and item.observed_text in {"-", "–", "—"} for item in self.evidence):
                raise ValueError("zero_from_dash requires a located PDF dash in the numeric cell")
        has_formula = any(isinstance(item.locator, ExcelLocator) and item.locator.formula for item in self.evidence)
        if self.calculated_value is not None and not has_formula:
            raise ValueError("calculated_value requires formula evidence")
        if has_formula and self.calculated_value is not None:
            if self.state == FactState.VERIFIED and self.calculated_value != self.value:
                raise ValueError("verified cache must equal independent calculation")
            if self.state == FactState.STALE_CACHE_MISMATCH and self.calculated_value == self.value:
                raise ValueError("stale cache must differ from independent calculation")
        check = self.verification
        if self.state == FactState.EXTRACTED:
            if check is not None:
                raise ValueError("extracted fact cannot claim completed verification")
        elif check is None or not check.source_checked:
            raise ValueError("post-extraction state requires source check")
        elif self.state == FactState.UNVERIFIED_MISSING_CACHE:
            if not has_formula or check.cache_present is not False or self.value is not None:
                raise ValueError("missing cache requires formula, absent cache and absent value")
        elif self.state == FactState.STALE_CACHE_MISMATCH:
            if not has_formula or check.cache_present is not True or check.independent_formula_match is not False or self.value is None:
                raise ValueError("stale cache requires independent mismatch and cached value")
        elif self.state == FactState.VERIFIED:
            if self.value is None or (has_formula and (check.cache_present is not True or check.independent_formula_match is not True)):
                raise ValueError("verified formula requires independently matching cache")
        elif self.state == FactState.HUMAN_CORRECTED:
            if self.value is None or not any(isinstance(item.locator, PDFLocator) for item in self.evidence):
                raise ValueError("human correction requires numeric value and PDF evidence")
        elif self.state == FactState.REJECTED_OCR_ERROR:
            if not any(isinstance(item.locator, PDFLocator) for item in self.evidence):
                raise ValueError("OCR rejection requires PDF evidence")
        if self.findings and self.state != FactState.VERIFIED:
            raise ValueError("reconciliation findings require a verified fact")
        if any(finding.difference <= finding.tolerance for finding in self.findings):
            raise ValueError("reconciliation difference must exceed its stated tolerance")
        return self

    def transition(
        self,
        to: FactState,
        *,
        source_checked: bool = False,
        cache_present: bool | None = None,
        independent_formula_match: bool | None = None,
    ) -> FinancialFact:
        """Apply a guarded verification result; formula checks cover supported formulas only.

        A verified formula requires a present cache and independent agreement. A
        non-formula fact can be verified after its source and period/unit are checked.
        Missing/stale facts can recover only after a new source check.
        """
        if to == FactState.EXTRACTED or not source_checked:
            raise ValueError("transition requires a new source check and non-extracted target")
        has_formula = any(isinstance(item.locator, ExcelLocator) and item.locator.formula for item in self.evidence)
        if to == FactState.UNVERIFIED_MISSING_CACHE:
            if not has_formula or cache_present is not False or self.value is not None:
                raise ValueError("missing-cache transition requires formula and absent value/cache")
        elif to == FactState.STALE_CACHE_MISMATCH:
            if not has_formula or cache_present is not True or independent_formula_match is not False or self.value is None:
                raise ValueError("stale-cache transition requires independently checked mismatch")
        elif to == FactState.VERIFIED:
            if self.value is None or (has_formula and (cache_present is not True or independent_formula_match is not True)):
                raise ValueError("verification needs value and, for formulas, independent cache agreement")
        else:
            raise ValueError("unknown state")
        return FinancialFact.model_validate({
            **self.model_dump(),
            "state": to,
            "verification": VerificationRecord(
                source_checked=source_checked,
                cache_present=cache_present,
                independent_formula_match=independent_formula_match,
            ).model_dump(),
        })

    def with_reconciliation_warning(self, finding: Finding) -> FinancialFact:
        if finding.code != FindingCode.RECONCILIATION_WARNING or self.state != FactState.VERIFIED:
            raise ValueError("reconciliation warning requires a verified fact")
        if finding.difference <= finding.tolerance:
            raise ValueError("difference must exceed tolerance")
        return FinancialFact.model_validate({**self.model_dump(), "findings": [*self.findings, finding]})


class AnswerResult(StrictModel):
    status: AnswerStatus
    explanation: str = Field(min_length=1)
    value: Decimal | None = None
    unit: str | None = None
    period: Period | None = None
    report_scope: ReportScope | None = None
    calculation: str | None = None
    evidences: list[Evidence] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_answer(self) -> AnswerResult:
        if self.status == AnswerStatus.ANSWERED:
            if self.value is None or self.unit is None or self.period is None or self.report_scope is None or not self.evidences:
                raise ValueError("answered result requires value, unit, period, scope and evidence")
        elif self.value is not None or self.unit is not None or self.calculation is not None:
            raise ValueError("refusal cannot assert a numeric answer")
        return self
