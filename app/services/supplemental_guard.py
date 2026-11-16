"""Anchor and scope checks for numeric disclosures extracted from notes."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from app.models.supplemental_fact import AnchorRelation, SupplementalFact
from app.services.qa_service import DocumentIndex


CONCEPT_ANCHORS: dict[str, set[str]] = {
    "term_deposits": {"B01:123"}, "restricted_cash": {"B01:110"},
    "cash_on_hand": {"B01:110"}, "demand_deposits": {"B01:110"}, "cash_equivalents": {"B01:110"},
    "restricted_term_deposits": {"B01:123"}, "unrestricted_cash": {"B01:110"},
    "eligible_term_deposits": {"B01:123"},
    "gross_trade_receivables": {"B01:131"}, "trade_receivable_provision": {"B01:131"},
    "overdue_trade_receivables_gross": {"B01:131"},
    "related_party_trade_receivables": {"B01:131"},
    "third_party_trade_receivables": {"B01:131"},
    "gross_other_receivables": {"B01:136"}, "other_receivable_provision": {"B01:137"},
    "overdue_other_receivables": {"B01:136"}, "related_party_receivables": {"B01:131","B01:136"},
    "raw_materials": {"B01:141"}, "finished_goods": {"B01:141"},
    "merchandise": {"B01:141"}, "inventory_provision_note": {"B01:149"},
    "work_in_progress": {"B01:141"}, "inventory_in_transit": {"B01:141"},
    "real_estate_inventory": {"B01:141"}, "inventory_tools": {"B01:141"},
    "consigned_inventory": {"B01:141"},
    "pledged_assets_book_value": {"B01:141"},
    "store_rent": {"B02:25"}, "rent_commitment_next_12m": {"B02:25"},
    "long_borrowings_confirmed_zero": {"B01:338"},
}


def supplemental_id(data: dict) -> str:
    payload={key:data[key] for key in ("document_hash","concept","fiscal_year","value_vnd","parent_vas_code","evidence")}
    encoded=json.dumps(payload,sort_keys=True,ensure_ascii=False,default=str,separators=(",",":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def check_anchor(index: DocumentIndex, item: SupplementalFact) -> str:
    if item.concept not in CONCEPT_ANCHORS or item.parent_vas_code not in CONCEPT_ANCHORS[item.concept]:
        return "INVALID_CONCEPT_ANCHOR"
    if item.concept in {"gross_trade_receivables", "overdue_trade_receivables_gross"} and item.anchor_relation!=AnchorRelation.SEMANTIC_ONLY:
        return "INVALID_RELATION_FOR_GROSS_AMOUNT"
    if item.concept == "related_party_trade_receivables" and item.anchor_relation!=AnchorRelation.SUBSET_OF_PARENT:
        return "INVALID_RELATION_FOR_NET_AMOUNT"
    form,code=item.parent_vas_code.split(":")
    scopes=index.scopes
    if len(scopes)!=1:return "MIXED_SCOPE"
    parent=index.get(form+"-DN",code,item.fiscal_year,next(iter(scopes)))
    if parent is None or parent.normalized_value is None:return "MISSING_PARENT"
    if item.anchor_relation==AnchorRelation.SEMANTIC_ONLY:
        return "SEMANTIC_ONLY"
    amount=abs(parent.normalized_value)
    child=abs(item.value_vnd)
    if item.anchor_relation==AnchorRelation.SUBSET_OF_PARENT:
        return "PASS" if child<=amount else "FAIL_EXCEEDS_PARENT"
    if item.anchor_relation==AnchorRelation.EXACT_PARENT:
        return "PASS" if child==amount else "FAIL_NOT_EQUAL_PARENT"
    return "INVALID_RELATION"


def check_receivable_subsets(facts: tuple[SupplementalFact,...], year: int) -> str:
    signed={item.concept:item for item in facts if item.fiscal_year==year and item.state=="VERIFIED"}
    gross=signed.get("gross_trade_receivables")
    overdue=signed.get("overdue_trade_receivables_gross")
    if gross and overdue and not Decimal(0)<=overdue.value_vnd<=gross.value_vnd:
        return "FAIL_OVERDUE_EXCEEDS_GROSS"
    return "PASS" if gross and overdue else "PENDING_GROUP"


def check_cash_group(index: DocumentIndex, facts: tuple[SupplementalFact,...], year: int) -> str:
    signed={item.concept:item.value_vnd for item in facts if item.fiscal_year==year and item.state=="VERIFIED"}
    term=signed.get("term_deposits")
    restricted_term=signed.get("restricted_term_deposits")
    if term is not None and restricted_term is not None:
        if not Decimal(0)<=restricted_term<=term:
            return "FAIL_RESTRICTED_EXCEEDS_TERM_DEPOSITS"
        eligible=signed.get("eligible_term_deposits")
        if eligible is not None and eligible+restricted_term!=term:
            return "FAIL_ELIGIBLE_TERM_RECONCILIATION"
    restricted_cash=signed.get("restricted_cash")
    free_cash=signed.get("unrestricted_cash")
    if restricted_cash is not None and free_cash is not None:
        if len(index.scopes)!=1:return "MIXED_SCOPE"
        cash=index.get("B01-DN","110",year,next(iter(index.scopes)))
        if cash is None or cash.normalized_value is None:return "MISSING_PARENT"
        if restricted_cash+free_cash!=cash.normalized_value:
            return "FAIL_UNRESTRICTED_CASH_RECONCILIATION"
    return "PASS" if term is not None and restricted_term is not None else "PENDING_GROUP"


def check_receivable_group(index: DocumentIndex, facts: tuple[SupplementalFact,...], year: int) -> str:
    scoped={item.concept:item for item in facts if item.fiscal_year==year and item.state=="VERIFIED"}
    gross=scoped.get("gross_trade_receivables")
    provision=scoped.get("trade_receivable_provision")
    if not gross or not provision:return "PENDING_GROUP"
    if len(index.scopes)!=1:return "MIXED_SCOPE"
    net=index.get("B01-DN","131",year,next(iter(index.scopes)))
    if net is None or net.normalized_value is None:return "MISSING_PARENT"
    return "PASS" if gross.value_vnd-provision.value_vnd==net.normalized_value else "FAIL_GROSS_NET_MISMATCH"
