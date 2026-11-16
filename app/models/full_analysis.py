"""Evidence-bearing outputs for multi-period financial analysis."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from app.models.financial_fact import Evidence, ReportScope, StrictModel


class MetricStatus(StrEnum):
    COMPUTED = "COMPUTED"
    PROXY = "PROXY"
    MISSING_INPUT = "MISSING_INPUT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class StatementType(StrEnum):
    MATH_OBSERVATION = "MATH_OBSERVATION"
    DIRECT_DISCLOSURE = "DIRECT_DISCLOSURE"
    ANALYTICAL_INFERENCE = "ANALYTICAL_INFERENCE"


class AnalysisMetric(StrictModel):
    metric_id: str
    fiscal_year: int
    value: Decimal | None
    unit: str
    formula: str
    formula_version: str = "P5_V1"
    status: MetricStatus
    verification_status: str
    input_status: str = "UNSPECIFIED"
    inputs: list[str] = Field(default_factory=list)
    evidences: list[Evidence] = Field(default_factory=list)
    reason: str | None = None


class BridgeStep(StrictModel):
    code: str
    label: str
    raw_change: Decimal | None
    signed_impact: Decimal | None
    prior_value: Decimal | None = None
    current_value: Decimal | None = None
    evidences: list[Evidence] = Field(default_factory=list)
    verification_status: str


class FinancialBridge(StrictModel):
    bridge_id: str
    fiscal_year: int
    target_delta: Decimal | None
    steps: list[BridgeStep]
    unexplained_residual: Decimal | None
    status: str
    verification_status: str
    evidences: list[Evidence] = Field(default_factory=list)
    uses: list[BridgeStep] = Field(default_factory=list)
    memo_steps: list[BridgeStep] = Field(default_factory=list)
    equity_components: list[BridgeStep] = Field(default_factory=list)
    component_residuals: dict[str, Decimal] = Field(default_factory=dict)
    explanation: str | None = None


class StructuredFinding(StrictModel):
    finding_id: str
    topic: str
    statement_type: StatementType
    primary_metrics: list[str]
    counter_evidence_checked: list[str]
    conclusion_key: str
    narrative_vi: str
    fiscal_year: int
    verification_status: str
    evidences: list[Evidence] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    trend: dict = Field(default_factory=dict)
    comparison: dict = Field(default_factory=dict)
    drivers: list[dict] = Field(default_factory=list)
    counterpoints: list[dict] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    analysis_sections: dict = Field(default_factory=dict)


class FullAnalysisReport(StrictModel):
    company: str
    company_id: str
    report_scope: ReportScope
    years: list[int]
    metrics: list[AnalysisMetric]
    bridges: list[FinancialBridge]
    findings: list[StructuredFinding]
    coverage_by_year: dict[int, dict[str, int]]
    limitations: list[str] = Field(default_factory=list)
    role_diagnostics: dict[str, list[dict]] = Field(default_factory=dict)
    analytical_findings: list[dict] = Field(default_factory=list)
