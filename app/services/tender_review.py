"""Deterministic tender criteria; evaluates configured criteria, not award decisions."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import Evidence, FactState, FinancialFact, ReportScope
from app.services.qa_service import DocumentIndex


Metric = Literal["NET_ASSETS", "WORKING_CAPITAL", "CURRENT_RATIO", "AVERAGE_REVENUE", "NET_INCOME", "CFO", "CFO_TO_NET_INCOME"]


class TenderCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    metric: Metric
    years: list[int] = Field(min_length=1)
    minimum: Decimal = Field(allow_inf_nan=False)
    operator: Literal[">=", ">"] = ">="
    revenue_code: Literal["01", "10"] | None = None

    @model_validator(mode="after")
    def validate_criterion(self):
        if len(set(self.years)) != len(self.years) or any(not 1900 <= y <= 2100 for y in self.years):
            raise ValueError("years must be unique and between 1900 and 2100")
        if self.metric != "AVERAGE_REVENUE" and len(self.years) != 1:
            raise ValueError("only average revenue accepts multiple years")
        if (self.metric == "AVERAGE_REVENUE") != (self.revenue_code is not None):
            raise ValueError("revenue_code is required only for average revenue")
        return self


class TenderCriteriaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str = Field(min_length=1)
    report_scope: ReportScope
    criteria_source: str = Field(min_length=1)
    criteria: list[TenderCriterion] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [criterion.id for criterion in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion IDs must be unique")
        return self


class TenderFinding(BaseModel):
    id: str
    metric: Metric
    status: Literal["PASS", "FAIL", "NEEDS_REVIEW"]
    reason: str | None = None
    value: Decimal | None = None
    unit: str
    minimum: Decimal
    operator: str
    formula: str
    years: list[int]
    evidences: list[Evidence] = Field(default_factory=list)


class TenderReviewResponse(BaseModel):
    company_id: str
    report_scope: ReportScope
    criteria_source: str
    findings: list[TenderFinding]


def review_tender(indexes: list[DocumentIndex], config: TenderCriteriaConfig) -> TenderReviewResponse:
    facts = [fact for index in indexes for fact in index.result.facts]
    findings = []
    for criterion in config.criteria:
        metric, years = criterion.metric, criterion.years
        mapping = {"NET_ASSETS": [("B01-DN", "270"), ("B01-DN", "300")],
                   "WORKING_CAPITAL": [("B01-DN", "100"), ("B01-DN", "310")],
                   "CURRENT_RATIO": [("B01-DN", "100"), ("B01-DN", "310")],
                   "AVERAGE_REVENUE": [("B02-DN", criterion.revenue_code)],
                   "NET_INCOME": [("B02-DN", "60")], "CFO": [("B03-DN", "20")],
                   "CFO_TO_NET_INCOME": [("B03-DN", "20"), ("B02-DN", "60")]}
        formulas = {"NET_ASSETS": "B01:270 - B01:300", "WORKING_CAPITAL": "B01:100 - B01:310",
                    "CURRENT_RATIO": "B01:100 / B01:310", "AVERAGE_REVENUE": f"trung bình B02:{criterion.revenue_code}",
                    "NET_INCOME": "B02:60", "CFO": "B03:20",
                    "CFO_TO_NET_INCOME": "B03:20 / B02:60 × 100"}
        selected: list[FinancialFact] = []
        reason = None
        for year in years:
            for form, code in mapping[metric]:
                matches = [f for f in facts if f.document.company_id == config.company_id
                           and f.document.report_scope == config.report_scope and f.line_code.form_code == form
                           and f.line_code.code == code and f.period.fiscal_year_label == f"FY{year}"
                           and f.document.fiscal_year_label == f"FY{year}"]
                if len(matches) != 1:
                    reason = "MISSING_OR_AMBIGUOUS_FACT"
                    continue
                fact = matches[0]
                if (fact.document.fiscal_period_end.year != year
                    or (form == "B01-DN") != (fact.period.period_type.value == "INSTANT")
                    or fact.period.period_type.value == "INSTANT" and fact.period.as_of_date != fact.document.fiscal_period_end
                    or fact.period.period_type.value == "DURATION" and
                    (fact.period.period_start, fact.period.period_end) != (fact.document.fiscal_period_start, fact.document.fiscal_period_end)):
                    reason = "PERIOD_MISMATCH"
                if fact.state not in {FactState.VERIFIED, FactState.HUMAN_CORRECTED} or fact.findings or fact.normalized_value is None or fact.currency != "VND":
                    reason = "UNVERIFIED_OR_INVALID_INPUT"
                selected.append(fact)
        for year in years:
            year_facts = [f for f in selected if f.document.fiscal_year_label == f"FY{year}"]
            if len({(f.document.fiscal_period_start, f.document.fiscal_period_end) for f in year_facts}) > 1:
                reason = "PERIOD_MISMATCH"
        value = None
        if reason is None:
            amounts = [fact.normalized_value for fact in selected]
            if metric in {"NET_ASSETS", "WORKING_CAPITAL"}:
                value = amounts[0] - amounts[1]
            elif metric == "CURRENT_RATIO":
                if amounts[1] <= 0:
                    reason = "INVALID_DENOMINATOR"
                else:
                    value = amounts[0] / amounts[1]
            elif metric == "AVERAGE_REVENUE":
                value = sum(amounts, Decimal(0)) / Decimal(len(amounts))
            elif metric == "CFO_TO_NET_INCOME":
                if amounts[1] <= 0:
                    reason = "INVALID_DENOMINATOR"
                else:
                    value = amounts[0] / amounts[1] * Decimal(100)
            else:
                value = amounts[0]
        status = "NEEDS_REVIEW" if reason else "PASS" if (value >= criterion.minimum if criterion.operator == ">=" else value > criterion.minimum) else "FAIL"
        findings.append(TenderFinding(id=criterion.id, metric=metric, status=status, reason=reason, value=value,
                                      unit="lần" if metric == "CURRENT_RATIO" else "%" if metric == "CFO_TO_NET_INCOME" else "đồng", minimum=criterion.minimum,
                                      operator=criterion.operator, formula=formulas[metric], years=years,
                                      evidences=[e for fact in selected for e in fact.evidence]))
    return TenderReviewResponse(company_id=config.company_id, report_scope=config.report_scope,
                                criteria_source=config.criteria_source, findings=findings)
