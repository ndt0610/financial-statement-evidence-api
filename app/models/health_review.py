"""Portable health-review contract; evidence locators may be Excel or PDF."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from app.models.financial_fact import Evidence, Period, ReportScope, StrictModel
from app.services.financial_calculator import VerificationStatus


class IndicatorStatus(StrEnum):
    COMPUTED = "COMPUTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    UNVERIFIED_INPUT = "UNVERIFIED_INPUT"
    INVALID_DENOMINATOR = "INVALID_DENOMINATOR"


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    UNVERIFIED_INPUT = "UNVERIFIED_INPUT"


class SignalStatus(StrEnum):
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class DocumentMetadata(StrictModel):
    document_hash: str
    company: str
    report_scope: ReportScope
    accounting_regime: str
    periods_covered: list[str]
    data_origin: str


class IndicatorInput(StrictModel):
    form_code: str
    line_code: str
    period: Period
    value_vnd: Decimal | None
    state: str
    evidences: list[Evidence]


class HealthIndicator(StrictModel):
    code: str
    label: str
    formula: str
    period: Period | None
    value: Decimal | None
    unit: str
    status: IndicatorStatus
    verification_status: VerificationStatus
    inputs: list[IndicatorInput] = Field(default_factory=list)
    input_evidences: list[Evidence] = Field(default_factory=list)


class ReconciliationCheck(StrictModel):
    code: str
    status: CheckStatus
    difference_vnd: Decimal | None
    tolerance_vnd: Decimal
    input_evidences: list[Evidence] = Field(default_factory=list)


class ReviewSignal(StrictModel):
    code: str
    status: SignalStatus
    explanation: str
    supporting_indicators: list[str]
    parameter_thresholds_used: dict[str, Decimal] = Field(default_factory=dict)
    input_evidences: list[Evidence] = Field(default_factory=list)


class HealthReportResponse(StrictModel):
    document_metadata: DocumentMetadata
    reconciliation_checks: list[ReconciliationCheck]
    indicators: list[HealthIndicator]
    review_signals: list[ReviewSignal]
