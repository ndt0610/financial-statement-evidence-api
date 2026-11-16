"""Additional P10 measures calculated from sourced values using Decimal."""
from decimal import Decimal

from app.models.full_analysis import AnalysisMetric, MetricStatus
from app.services.analysis_book import union_evidence, weakest
from app.services.analysis_calculator import Eval, MissingInput, NotApplicable


SPECS = {
    'X01_ADMIN_TO_REV': ('%', '|B02:26| / B02:10 × 100'),
    'X02_SGA_TO_REV': ('%', '(|B02:25| + |B02:26|) / B02:10 × 100'),
    'X03_NON_CORE_PRETAX_DIFF': ('%', '(EBT − Core OP) / EBT × 100'),
    'X04_EFFECTIVE_TAX_RATE': ('%', 'Thuế đã đối chiếu / EBT × 100'),
    'X05_DUPONT_TAX_BURDEN': ('lần', 'LNST / EBT'),
    'X06_DUPONT_INTEREST_BURDEN': ('lần', 'EBT / EBIT xấp xỉ'),
    'X07_DUPONT_EBIT_MARGIN': ('%', 'EBIT xấp xỉ / doanh thu × 100'),
    'X08_CASH_DIVIDEND_TO_NI': ('%', '|B03:36| / LNST × 100'),
    'X09_NWC_TO_INVENTORY': ('%', '(B01:100 − B01:310) / B01:140 × 100'),
    'X10_BORROWING_INFLOW_TO_ST_DEBT': ('lần', 'B03:33 / B01:320'),
    'X11_REPAYMENT_TO_BORROWING_INFLOW': ('%', '|B03:34| / B03:33 × 100'),
    'X12_NET_WORTH': ('VND', 'B01:270 − B01:300'),
    'X13_NOWC_TO_REV': ('%', 'L05 / doanh thu × 100'),
    'X14_CAPEX_TO_REV': ('%', '|B03:21| / doanh thu × 100'),
}


def calculate_p10(book, year):
    out = []
    for concept in ('revenue', 'gross_profit', 'net_income', 'net_income_parent', 'cfo', 'borrowings', 'interest_expense', 'cf_inventory', 'inventory', 'cogs', 'selling', 'admin', 'assets', 'current_assets', 'current_liabilities', 'liabilities', 'equity', 'cash', 'short_investments', 'raw_materials', 'finished_goods', 'merchandise', 'pledged_assets_book_value', 'eligible_term_deposits', 'unrestricted_cash', 'rent_commitment_next_12m'):
        datum = book.get(concept, year)
        out.append(AnalysisMetric(metric_id='RAW_'+concept.upper(), fiscal_year=year,
            value=datum.value_vnd if datum else None, unit='VND', formula=datum.source_line or concept if datum else concept,
            formula_version='P10_V1', status=MetricStatus.COMPUTED if datum and datum.value_vnd is not None else MetricStatus.MISSING_INPUT,
            verification_status=datum.verification_status if datum else 'MISSING_INPUT', inputs=[f'{concept}@{year}'],
            evidences=list(datum.evidences) if datum else [], reason=None if datum else 'Thiếu số liệu nguồn'))
    for mid, (unit, formula) in SPECS.items():
        e = Eval(book, year)
        v, ratio = e.v, e.ratio
        def compute():
            if mid == 'X01_ADMIN_TO_REV': return ratio(abs(v('admin')), v('revenue'), percent=True, positive=True)
            if mid == 'X02_SGA_TO_REV': return ratio(abs(v('selling')) + abs(v('admin')), v('revenue'), percent=True, positive=True)
            if mid == 'X03_NON_CORE_PRETAX_DIFF':
                core = v('gross_profit') - abs(v('selling')) - abs(v('admin'))
                ebt = v('ebt')
                return ratio(ebt - core, ebt, percent=True, positive=True)
            if mid == 'X04_EFFECTIVE_TAX_RATE':
                current, deferred = v('current_tax'), v('deferred_tax')
                total = current + deferred
                ebt, ni = v('ebt'), v('net_income')
                expected = ebt - ni
                if expected not in {total, -total, abs(current) + deferred, abs(current) - deferred}:
                    raise MissingInput('Thuế chưa khớp B02:50 − B02:60')
                return ratio(expected, ebt, percent=True, positive=True)
            if mid in {'X05_DUPONT_TAX_BURDEN', 'X06_DUPONT_INTEREST_BURDEN', 'X07_DUPONT_EBIT_MARGIN'}:
                ni, ebt, interest, revenue = v('net_income'), v('ebt'), abs(v('interest_expense')), v('revenue')
                assets, equity = e.avg('assets'), e.avg('equity')
                ebit = ebt + interest
                if min(ebt, ebit, revenue, assets, equity) <= 0: raise NotApplicable('DuPont 5 bước cần mẫu số dương')
                return {'X05_DUPONT_TAX_BURDEN': ni / ebt, 'X06_DUPONT_INTEREST_BURDEN': ebt / ebit, 'X07_DUPONT_EBIT_MARGIN': ebit / revenue * 100}[mid]
            if mid == 'X08_CASH_DIVIDEND_TO_NI': return ratio(abs(v('dividends_paid')), v('net_income'), percent=True, positive=True)
            if mid == 'X09_NWC_TO_INVENTORY': return ratio(v('current_assets') - v('current_liabilities'), v('inventory'), percent=True, positive=True)
            if mid == 'X10_BORROWING_INFLOW_TO_ST_DEBT': return ratio(v('borrowing_in'), v('borrowings'), positive=True)
            if mid == 'X11_REPAYMENT_TO_BORROWING_INFLOW': return ratio(abs(v('principal_paid')), v('borrowing_in'), percent=True, positive=True)
            if mid == 'X12_NET_WORTH': return v('assets') - v('liabilities')
            if mid == 'X13_NOWC_TO_REV': return ratio((v('current_assets') - v('cash') - v('short_investments')) - (v('current_liabilities') - v('borrowings')), v('revenue'), percent=True, positive=True)
            if mid == 'X14_CAPEX_TO_REV': return ratio(abs(v('capex')), v('revenue'), percent=True, positive=True)
            raise ValueError(mid)
        try:
            value, status, reason = compute(), MetricStatus.COMPUTED, None
        except MissingInput as error:
            value, status, reason = None, MetricStatus.MISSING_INPUT, str(error)
        except NotApplicable as error:
            value, status, reason = None, MetricStatus.NOT_APPLICABLE, str(error)
        out.append(AnalysisMetric(metric_id=mid, fiscal_year=year, value=value, unit=unit, formula=formula,
            formula_version='P10_V1', status=status, verification_status=weakest(*(d.verification_status for d in e.inputs)),
            inputs=list(dict.fromkeys(f'{d.concept}@{d.year}' for d in e.inputs)),
            evidences=union_evidence(*(d.evidences for d in e.inputs)), reason=reason))
    return out
