"""Company-independent semantic input layer for the P5 calculator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.models import Evidence, FactState, FinancialFact, ReportScope
from app.services.qa_service import DocumentIndex
from app.services.fact_identity import pdf_fact_id
from app.services.vas_normalizer import fold, shift_year


LINE_CONCEPTS: dict[tuple[str, str], str] = {
    **{("B01-DN", code): name for code, name in {
        "100": "current_assets", "110": "cash", "120": "short_investments", "123": "short_held_to_maturity",
        "130": "all_receivables", "131": "trade_receivables", "137": "ar_provision",
        "140": "inventory", "141": "inventory_gross", "149": "inventory_provision",
        "220": "fixed_assets", "270": "assets", "300": "liabilities", "310": "current_liabilities",
        "311": "trade_payables", "313": "tax_payable", "320": "borrowings",
        "330": "long_liabilities", "338": "long_borrowings", "400": "equity", "429": "nci_equity",
    }.items()},
    **{("B02-DN", code): name for code, name in {
        "10": "revenue", "11": "cogs", "20": "gross_profit", "21": "finance_income",
        "22": "finance_expense", "23": "interest_expense", "25": "selling", "26": "admin",
        "30": "operating_reported", "40": "other_net", "50": "ebt", "51": "current_tax",
        "52": "deferred_tax", "60": "net_income", "61": "net_income_parent",
    }.items()},
    **{("B03-DN", code): name for code, name in {
        "01": "cf_ebt", "02": "da", "03": "cf_provisions", "04": "cf_fx",
        "05": "cf_investment", "06": "cf_interest", "08": "pre_wc",
        "09": "cf_receivables", "10": "cf_inventory", "11": "cf_payables",
        "12": "cf_prepaid", "13": "cf_securities", "14": "interest_paid",
        "15": "tax_paid", "16": "other_operating_received", "17": "other_operating_paid", "20": "cfo",
        "21": "capex", "22": "asset_sales", "23": "deposits_loans_out",
        "24": "deposits_loans_in", "25": "equity_investment", "27": "interest_received",
        "30": "cfi", "31": "share_issuance", "32": "buybacks",
        "33": "borrowing_in", "34": "principal_paid", "36": "dividends_paid",
        "40": "cff", "50": "cash_change", "61": "cash_fx", "70": "cash_end",
    }.items()},
}


RANK = {
    "HUMAN_VERIFIED": 4,
    "VERIFIED": 3,
    "HUMAN_CORRECTED": 4,
    "REJECTED_OCR_ERROR": 0,
    "analyst_transcribed_pending_separate_signoff": 2,
    "EXTRACTED": 2,
    "UNVERIFIED_MISSING_CACHE": 1,
    "STALE_CACHE_MISMATCH": 0,
}


def weakest(*statuses: str) -> str:
    return min(statuses, key=lambda s: RANK.get(s, -1)) if statuses else "MISSING_INPUT"


def union_evidence(*groups: tuple[Evidence, ...] | list[Evidence]) -> list[Evidence]:
    seen: set[str] = set()
    result: list[Evidence] = []
    for group in groups:
        for evidence in group:
            key = evidence.model_dump_json()
            if key not in seen:
                seen.add(key)
                result.append(evidence)
    return result


def _b03_method(facts: list[FinancialFact], year: int) -> str:
    """Classify cash-flow presentation from located row labels, not code alone."""
    labels = {(f.line_code.code, fold(f.original_label)) for f in facts
              if f.line_code.form_code == "B03-DN" and
              f.period.fiscal_year_label == f"FY{year}"}
    indirect = any(code == "02" and any(word in label for word in ("khau hao", "khu hao"))
                   or code == "01" and "nhuan" in label and "thue" in label
                   or code == "08" and "nhuan" in label and "von" in label
                   for code, label in labels)
    direct = any(code in {"01", "02"} and
                 ("tien thu" in label or "tien chi" in label or "chi tra" in label)
                 for code, label in labels)
    return "INDIRECT" if indirect and not direct else "DIRECT" if direct and not indirect else "UNKNOWN"


@dataclass(frozen=True)
class SourceDatum:
    concept: str
    year: int
    value_vnd: Decimal | None
    evidences: tuple[Evidence, ...]
    verification_status: str
    company_id: str
    report_scope: ReportScope
    period_end: str | None = None
    source_line: str | None = None


class AnalysisBook:
    def __init__(self, datums: list[SourceDatum], *, analysis_years: list[int] | None = None):
        if not datums:
            raise ValueError("analysis requires sourced data")
        self.company_id = datums[0].company_id
        self.scope = datums[0].report_scope
        self.by_key: dict[tuple[str, int], SourceDatum] = {}
        self.years = sorted({d.year for d in datums})
        self.analysis_years = sorted(analysis_years or self.years)
        period_ends: dict[int, set[str]] = {}
        for datum in datums:
            if datum.company_id != self.company_id or datum.report_scope != self.scope:
                raise ValueError("cannot combine companies or report scopes")
            if not datum.evidences:
                raise ValueError("every input needs source evidence")
            if datum.period_end is not None:
                period_ends.setdefault(datum.year,set()).add(datum.period_end)
            key = (datum.concept, datum.year)
            if key in self.by_key:
                old = self.by_key[key]
                if old.value_vnd != datum.value_vnd or old.period_end != datum.period_end:
                    raise ValueError(f"conflicting source values for {key}")
                self.by_key[key] = SourceDatum(
                    datum.concept, datum.year, datum.value_vnd,
                    tuple(union_evidence(old.evidences, datum.evidences)),
                    weakest(old.verification_status, datum.verification_status),
                    datum.company_id, datum.report_scope, datum.period_end, datum.source_line,
                )
            else:
                self.by_key[key] = datum
        if any(len(ends)>1 for ends in period_ends.values()):
            raise ValueError("statement period ends differ within a fiscal year")

    def get(self, concept: str, year: int) -> SourceDatum | None:
        return self.by_key.get((concept, year))

    @classmethod
    def from_indexes(cls, indexes: list[DocumentIndex]) -> "AnalysisBook":
        datums: list[SourceDatum] = []
        company_names={fact.document.company_name for index in indexes for fact in index.result.facts}
        if len(company_names)!=1:
            raise ValueError("cannot combine different company identities")
        for index in indexes:
            history=getattr(index.result,"review_history",())
            b03_methods = {int(f.period.fiscal_year_label.removeprefix("FY")):
                           _b03_method(index.result.facts, int(f.period.fiscal_year_label.removeprefix("FY")))
                           for f in index.result.facts if f.line_code.form_code == "B03-DN"}
            signed_ids = {event.get("result_fact_id") for event in history if event.get("decision") in {"CONFIRM", "BATCH_CONFIRM", "CORRECT"}}
            for event in history:
                if event.get("decision")=="CONFIRM_NOTE" and event.get("fact_id") in signed_ids:
                    signed_ids.add(event.get("result_fact_id"))
            for fact in index.result.facts:
                concept = LINE_CONCEPTS.get((fact.line_code.form_code, fact.line_code.code))
                if concept is None:
                    continue
                # Codes 01–17 change their meaning under the direct method.
                # Only the indirect method may feed the B03 working-capital
                # bridge or depreciation-based D07 calculation.
                if (fact.line_code.form_code == "B03-DN" and
                        fact.line_code.code.isdigit() and 1 <= int(fact.line_code.code) <= 17 and
                        b03_methods.get(int(fact.period.fiscal_year_label.removeprefix("FY"))) != "INDIRECT"):
                    continue
                year = int(fact.period.fiscal_year_label.removeprefix("FY"))
                state = fact.state.value
                if fact.state in {FactState.VERIFIED, FactState.HUMAN_CORRECTED} and signed_ids:
                    # A reviewed PDF differs from an Excel source/formula check.
                    if pdf_fact_id(fact) in signed_ids:
                        state = "HUMAN_CORRECTED" if fact.state == FactState.HUMAN_CORRECTED else "HUMAN_VERIFIED"
                end = fact.period.as_of_date or fact.period.period_end
                # TT200 comparative B01 often says "01/01/current FY" for the
                # opening balance. Economically this is the prior FY close.
                if (fact.period.as_of_date == fact.document.fiscal_period_start
                        and year == int(fact.document.fiscal_year_label.removeprefix("FY")) - 1):
                    end = end - timedelta(days=1)
                datums.append(SourceDatum(concept, year, None if fact.state == FactState.REJECTED_OCR_ERROR else fact.normalized_value, tuple(fact.evidence), state,
                                          fact.document.company_id, fact.document.report_scope, end.isoformat() if end else None,
                                          f"{fact.line_code.form_code}:{fact.line_code.code}"))
            if index.result.facts:
                context=index.result.facts[0].document
                for item in index.supplemental_facts:
                    if item.reconciliation_status=="REJECTED":
                        continue
                    datums.append(SourceDatum(item.concept,item.fiscal_year,item.value_vnd,(item.evidence,),
                                              "HUMAN_VERIFIED" if item.state==FactState.VERIFIED else "EXTRACTED",
                                              context.company_id,context.report_scope,
                                              shift_year(context.fiscal_period_end,item.fiscal_year-context.fiscal_period_end.year).isoformat(),item.parent_vas_code))
        report_years = sorted({int(f.document.fiscal_year_label.removeprefix("FY"))
                               for index in indexes for f in index.result.facts})
        sourced={(d.concept,d.year):d for d in datums}
        for year in {d.year for d in datums}:
            term=sourced.get(("term_deposits",year))
            restricted=sourced.get(("restricted_term_deposits",year))
            if (term and restricted and ("eligible_term_deposits",year) not in sourced
                    and term.verification_status==restricted.verification_status=="HUMAN_VERIFIED"):
                eligible=term.value_vnd-restricted.value_vnd
                if eligible<0:
                    raise ValueError("restricted term deposits exceed signed term deposits")
                datums.append(SourceDatum("eligible_term_deposits",year,eligible,
                                          tuple(union_evidence(term.evidences,restricted.evidences)),"HUMAN_VERIFIED",
                                          term.company_id,term.report_scope,term.period_end,"B09:term_deposits-restricted"))
            cash=sourced.get(("cash",year))
            restricted_cash=sourced.get(("restricted_cash",year))
            if (cash and restricted_cash and ("unrestricted_cash",year) not in sourced
                    and cash.verification_status in {"VERIFIED","HUMAN_VERIFIED","HUMAN_CORRECTED"}
                    and restricted_cash.verification_status=="HUMAN_VERIFIED"):
                free=cash.value_vnd-restricted_cash.value_vnd
                if free<0:
                    raise ValueError("restricted cash exceeds reported cash")
                datums.append(SourceDatum("unrestricted_cash",year,free,
                                          tuple(union_evidence(cash.evidences,restricted_cash.evidences)),
                                          weakest(cash.verification_status,restricted_cash.verification_status),
                                          cash.company_id,cash.report_scope,cash.period_end,"B01:110-restricted_cash"))
        return cls(datums, analysis_years=report_years)
