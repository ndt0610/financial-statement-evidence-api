"""Require verified numerical inputs and a located note before answering."""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Evidence, FinancialFact, FindingCode
from app.services.financial_calculator import VerificationStatus, status_for_facts
from app.services.question_router import normalize_note_ref


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    verification_status: VerificationStatus
    reason: str | None
    evidences: list[Evidence]


def check_evidence(*facts: FinancialFact, require_note: str | None = None, block_reconciliation: bool = False) -> GuardDecision:
    if not facts:
        return GuardDecision(False, VerificationStatus.NOT_APPLICABLE, "MISSING_DATA", [])
    status = status_for_facts(*facts)
    evidences = [evidence for fact in facts for evidence in fact.evidence]
    if status != VerificationStatus.VERIFIED:
        return GuardDecision(False, status, status.value, evidences)
    if block_reconciliation and any(
        finding.code == FindingCode.RECONCILIATION_WARNING for fact in facts for finding in fact.findings
    ):
        return GuardDecision(False, VerificationStatus.UNVERIFIED, "RECONCILIATION_WARNING", evidences)
    if require_note is not None:
        key = normalize_note_ref(require_note)
        primary = facts[0]
        if (not primary.note_link_verified or primary.note_evidence is None or primary.note_ref is None
                or normalize_note_ref(primary.note_ref) != key):
            return GuardDecision(False, VerificationStatus.UNVERIFIED, "MISSING_NOTE_EVIDENCE", evidences)
        evidences.append(primary.note_evidence)
    return GuardDecision(True, VerificationStatus.VERIFIED, None, evidences)
