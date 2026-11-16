"""Stable identity for a reviewed PDF fact revision."""

import hashlib
import json

from app.models import FinancialFact


def pdf_fact_id(fact: FinancialFact) -> str:
    payload = fact.model_dump(mode="json")
    # The flag was introduced after existing signed PDFs were indexed. A
    # default false flag must not invalidate their immutable review ledger.
    if payload.get("zero_from_dash") is False:
        payload.pop("zero_from_dash")
    encoded = json.dumps(payload, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
