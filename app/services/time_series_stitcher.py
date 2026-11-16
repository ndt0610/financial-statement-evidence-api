"""Explicit overlap resolution for multi-document financial time series."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.adapters.pdf_adapter import PdfIngestionResult
from app.services.analysis_book import AnalysisBook, SourceDatum, union_evidence, weakest, RANK
from app.services.qa_service import DocumentIndex


RestatementPolicy = Literal["ORIGINAL_AS_REPORTED", "LATEST_COMPARATIVE"]
SIGN_CONVENTION_CONCEPTS = frozenset({"cogs", "finance_expense", "interest_expense",
                                     "selling", "admin", "current_tax", "deferred_tax"})


def sign_convention_equivalent(concept: str, versions: list[tuple[int, SourceDatum, str]],
                               pdf_hashes: set[str]) -> bool:
    """Resolve only reviewed PDF expenses/taxes with identical absolute values."""
    values = [datum.value_vnd for _, datum, _ in versions]
    return (concept in SIGN_CONVENTION_CONCEPTS and len(versions) >= 2
            and all(document_hash in pdf_hashes and datum.verification_status == "HUMAN_VERIFIED"
                    for _, datum, document_hash in versions)
            and all(value is not None and value != 0 for value in values)
            and len(set(values)) == 2 and len({abs(value) for value in values}) == 1)


@dataclass(frozen=True)
class StitchedSeries:
    book: AnalysisBook
    conflicts: list[dict]
    documents: list[dict]
    policy: RestatementPolicy


def stitch_indexes(indexes: list[DocumentIndex], *, policy: RestatementPolicy="ORIGINAL_AS_REPORTED", include_comparative_periods: bool=False) -> StitchedSeries:
    if not indexes:
        raise ValueError("at least one indexed document is required")
    if policy not in {"ORIGINAL_AS_REPORTED", "LATEST_COMPARATIVE"}:
        raise ValueError("unknown restatement policy")
    identities=set();scope_set=set();fiscal_ends=set();documents=[]
    for index in indexes:
        if not index.result.facts:raise ValueError("document has no financial facts")
        context=index.result.facts[0].document
        identities.add(context.company_id)
        scope_set.add(context.report_scope)
        fiscal_ends.add((context.fiscal_period_end.month,context.fiscal_period_end.day))
        documents.append({"document_hash":index.result.document_hash,"company_id":context.company_id,
                          "company_name":context.company_name,
                          "report_scope":context.report_scope.value,
                          "fiscal_year":int(context.fiscal_year_label.removeprefix("FY")),
                          "fiscal_period_end":context.fiscal_period_end.isoformat(),
                          "data_origin":context.data_origin,
                          "source_kind":"pdf" if isinstance(index.result,PdfIngestionResult) else "excel",
                          "verified_facts":sum(f.state.value=="VERIFIED" for f in index.result.facts),
                          "extracted_facts":sum(f.state.value=="EXTRACTED" for f in index.result.facts),
                          "verified_supplemental":sum(f.state.value=="VERIFIED" for f in index.supplemental_facts)})
    if len(identities)!=1:raise ValueError("cannot combine different company IDs")
    if len(scope_set)!=1:raise ValueError("cannot combine SEPARATE and CONSOLIDATED reports")
    if len(fiscal_ends)!=1:raise ValueError("fiscal year ends differ across documents")
    if len({d["document_hash"] for d in documents})!=len(documents):
        raise ValueError("duplicate document hashes")
    grouped:dict[tuple[str,int],list[tuple[int,SourceDatum,str]]]={}
    for index,document in zip(indexes,documents):
        # Single-document normalization already checks ambiguous duplicates and
        # comparative B01 opening dates.
        sub=AnalysisBook.from_indexes([index])
        for datum in sub.by_key.values():
            grouped.setdefault((datum.concept,datum.year),[]).append((document["fiscal_year"],datum,document["document_hash"]))
    selected=[];conflicts=[]
    pdf_hashes = {item["document_hash"] for item in documents if item["source_kind"] == "pdf"}
    for (concept,year),versions in sorted(grouped.items()):
        ordered=sorted(versions,key=lambda x:(x[0],x[2]))
        if len({datum.period_end for _,datum,_ in ordered})>1:
            raise ValueError(f"period end conflict for {concept}@{year}")
        values={datum.value_vnd for _,datum,_ in ordered}
        if len(values)==1:
            # Matching alternatives need not downgrade an already reviewed
            # source: select only the strongest independently sourced copies.
            # The unreviewed alternatives remain unchanged in their indexes.
            best=max(RANK.get(d.verification_status,-1) for _,d,_ in ordered)
            preferred=[entry for entry in ordered if RANK.get(entry[1].verification_status,-1)==best]
            chosen=preferred[0][1]
            if len(preferred)>1:
                chosen=SourceDatum(concept,year,chosen.value_vnd,
                                   tuple(union_evidence(*(d.evidences for _,d,_ in preferred))),
                                   weakest(*(d.verification_status for _,d,_ in preferred)),
                                   chosen.company_id,chosen.report_scope,chosen.period_end,chosen.source_line)
        else:
            original=min(ordered,key=lambda v:(v[0]!=year,abs(v[0]-year),v[0],v[2]))
            latest=max(ordered,key=lambda v:(v[0],v[2]))
            preferred=original if policy=="ORIGINAL_AS_REPORTED" else latest
            chosen=preferred[1]
            classification = ("SIGN_CONVENTION_EQUIVALENT" if sign_convention_equivalent(concept, ordered, pdf_hashes)
                              else "VERSION_OR_RESTATEMENT_CONFLICT_UNDETERMINED")
            conflicts.append({"concept":concept,"fiscal_year":year,"policy":policy,
                              "selected_document_hash":preferred[2],"selected_value_vnd":str(chosen.value_vnd),
                              "delta_latest_minus_original_vnd":str(latest[1].value_vnd-original[1].value_vnd)
                              if latest[1].value_vnd is not None and original[1].value_vnd is not None else None,
                              "classification":classification,
                              "versions":[{"report_year":report_year,"document_hash":document_hash,
                                           "value_vnd":str(datum.value_vnd),"verification_status":datum.verification_status,
                                           "evidences":[e.model_dump(mode="json") for e in datum.evidences]}
                                          for report_year,datum,document_hash in ordered]})
        selected.append(chosen)
    # Comparative duration columns can contain a valid previous fiscal period
    # even when only one source document was uploaded. Opening balance-only
    # years remain available as inputs without implying a complete report year.
    duration_years={d.year for d in selected if d.concept in {"revenue", "net_income", "cfo"} and d.value_vnd is not None} if include_comparative_periods else set()
    book=AnalysisBook(selected,analysis_years=sorted({d["fiscal_year"] for d in documents} | duration_years))
    return StitchedSeries(book,conflicts,documents,policy)
