"""Auditable numeric facts from statement notes, separate from main-statement rows."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from app.models.financial_fact import Evidence, FactState, StrictModel


class AnchorRelation(StrEnum):
    SUBSET_OF_PARENT = "SUBSET_OF_PARENT"
    EXACT_PARENT = "EXACT_PARENT"
    SEMANTIC_ONLY = "SEMANTIC_ONLY"


class SupplementalFact(StrictModel):
    fact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    concept: str = Field(min_length=2)
    fiscal_year: int = Field(ge=1900, le=2100)
    value_vnd: Decimal = Field(allow_inf_nan=False)
    parent_vas_code: str = Field(pattern=r"^B0[123]:(?:[0-9]{2,3})$")
    anchor_relation: AnchorRelation
    evidence: Evidence
    state: FactState = FactState.EXTRACTED
    reconciliation_status: str = "PENDING"
    note_ref: str | None = None
    reviewer: str | None = None
    reviewed_at_utc: datetime | None = None

    @model_validator(mode="after")
    def check(self):
        if self.evidence.document_hash != self.document_hash:
            raise ValueError("supplemental evidence must refer to the same document")
        if self.state not in (FactState.EXTRACTED,FactState.VERIFIED):
            raise ValueError("supplemental facts use EXTRACTED or VERIFIED")
        if self.state==FactState.VERIFIED and (not self.reviewer or not self.reviewed_at_utc):
            raise ValueError("verified supplemental fact requires review record")
        return self
