"""The 52 versioned measures from the financial-analysis blueprint.

The formulas operate on semantic statement concepts, never company names. Missing
inputs stay missing; note-only concepts cannot be synthesized from nearby totals.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.full_analysis import AnalysisMetric, MetricStatus
from app.services.analysis_book import AnalysisBook, SourceDatum, union_evidence, weakest
from app.services.supplemental_guard import CONCEPT_ANCHORS


METRIC_IDS = tuple(
    f"{prefix}{number:02d}"
    for prefix, count in (("G", 7), ("L", 7), ("D", 9), ("E", 7), ("P", 9), ("C", 8), ("Q", 5))
    for number in range(1, count + 1)
)
assert len(METRIC_IDS) == 52

FORMULA_BY_ID = dict(zip(METRIC_IDS, (
    "Δrevenue", "revenue YoY", "revenue CAGR", "inventory/assets", "liabilities/(liabilities+equity)", "selling/revenue", "Δinventory/Δassets",
    "current assets/current liabilities", "(reported cash+trade AR)/current liabilities", "reported cash/current liabilities", "current assets-current liabilities", "operating CA-operating CL", "CFO/average CL", "liquid assets/daily operating cash cost",
    "liabilities/assets", "liabilities/equity", "interest debt/equity", "interest debt-reported cash", "short borrowings/interest debt", "EBIT/interest", "net debt/EBITDA", "CFO/average interest debt", "CFADS/scheduled debt service",
    "COGS/average net inventory", "average net inventory/COGS×365", "average trade AR/revenue×365", "average trade AP/COGS×365", "DIO+DSO_proxy-DPO_proxy", "revenue/average assets", "revenue/average fixed assets",
    "gross profit/revenue", "core operating profit/revenue", "EBT/revenue", "NI/revenue", "NI/average assets", "NI/average equity", "parent NI/average parent equity", "NOPAT/average invested capital", "EBIT/average(A-CL)",
    "CFO/NI", "CFO/revenue", "CFO-NI", "(NI-CFO)/average assets", "CFO-cash capex", "CFO/cash capex", "sum CFO/sum NI", "(CFO-cash capex)/cash dividends",
    "AR provision/matched gross AR", "overdue AR/matched gross AR", "inventory provision/gross inventory", "related-party AR/matched AR", "pledged assets book value/assets",
)))
assert len(FORMULA_BY_ID)==52
MONEY_IDS={"G01","L04","D04","C03","C05"}
DAY_IDS={"L07","E02","E03","E04","E05"}
MULTIPLE_IDS={"L01","L02","L03","D06","D07","D09","E01","E06","E07","C06","C08"}


def _unit_for(mid: str) -> str:
    return "VND" if mid in MONEY_IDS else "ngày" if mid in DAY_IDS else "lần" if mid in MULTIPLE_IDS else "%"


class MissingInput(Exception):
    pass


class NotApplicable(Exception):
    pass


class Eval:
    def __init__(self, book: AnalysisBook, year: int):
        self.book, self.year = book, year
        self.inputs: list[SourceDatum] = []

    def v(self, concept: str, year: int | None = None) -> Decimal:
        year = self.year if year is None else year
        datum = self.book.get(concept, year)
        if datum is None or datum.value_vnd is None:
            raise MissingInput(f"{concept}@{year}")
        if concept in CONCEPT_ANCHORS and datum.verification_status == "EXTRACTED":
            raise MissingInput(f"{concept}@{year}: supplemental source pending signoff")
        self.inputs.append(datum)
        return datum.value_vnd

    def avg(self, concept: str) -> Decimal:
        return (self.v(concept, self.year - 1) + self.v(concept)) / 2

    def signed_note(self, concept: str, year: int | None = None) -> bool:
        datum = self.book.get(concept, self.year if year is None else year)
        return datum is not None and datum.verification_status in {"HUMAN_VERIFIED", "HUMAN_CORRECTED", "VERIFIED"}

    def ratio(self, numerator: Decimal, denominator: Decimal, *, percent: bool = False,
              positive: bool = False) -> Decimal:
        if denominator == 0 or (positive and denominator < 0):
            raise NotApplicable("zero or nonpositive denominator")
        return numerator / denominator * (Decimal("100") if percent else Decimal("1"))


def _eval(mid: str, e: Eval) -> tuple[Decimal, str, str, bool]:
    """Return value, unit, formula, proxy flag; all VND inputs are Decimal."""
    v, avg, ratio = e.v, e.avg, e.ratio
    y = e.year
    pct, x, money, days = "%", "lần", "VND", "ngày"
    if mid == "G01": return v("revenue") - v("revenue", y - 1), money, "DT_t − DT_t−1", False
    if mid == "G02": return ratio(v("revenue") - v("revenue", y - 1), v("revenue", y - 1), percent=True, positive=True), pct, "(DT_t/DT_t−1 − 1) × 100", False
    if mid == "G03":
        revenue_years=[z for z in e.book.analysis_years if e.book.get("revenue", z)]
        if not revenue_years: raise MissingInput("revenue history")
        first = min(revenue_years)
        if first == y: raise MissingInput("at least two annual observations")
        if any(e.book.get("revenue", z) is None or e.book.get("revenue", z).value_vnd is None for z in range(first,y+1)):
            raise MissingInput("CAGR requires consecutive annual revenue")
        start, end = v("revenue", first), v("revenue")
        if start <= 0 or end <= 0: raise NotApplicable("CAGR needs positive endpoints")
        return ((end / start) ** (Decimal(1) / Decimal(y - first)) - 1) * 100, pct, f"((DT_{y}/DT_{first})^(1/{y-first}) − 1) × 100", False
    if mid == "G04": return ratio(v("inventory"), v("assets"), percent=True, positive=True), pct, "B01:140 / B01:270 × 100", False
    if mid == "G05": return ratio(v("liabilities"), v("liabilities") + v("equity"), percent=True, positive=True), pct, "L / (L+E) × 100", False
    if mid == "G06": return ratio(abs(v("selling")), v("revenue"), percent=True, positive=True), pct, "|B02:25| / B02:10 × 100", False
    if mid == "G07": return ratio(v("inventory")-v("inventory", y-1), v("assets")-v("assets", y-1), percent=True), pct, "ΔB01:140 / ΔB01:270 × 100", False
    if mid == "L01": return ratio(v("current_assets"), v("current_liabilities"), positive=True), x, "B01:100 / B01:310", False
    if mid == "L02":
        qualified=e.signed_note("unrestricted_cash")
        cash=v("unrestricted_cash") if qualified else v("cash")
        return ratio(cash+v("trade_receivables"),v("current_liabilities"),positive=True),x,"(eligible cash+trade AR)/CL" if qualified else "(B01:110+B01:131)/B01:310; reported-cash proxy",not qualified
    if mid == "L03":
        qualified=e.signed_note("unrestricted_cash")
        cash=v("unrestricted_cash") if qualified else v("cash")
        return ratio(cash,v("current_liabilities"),positive=True),x,"eligible cash/CL" if qualified else "B01:110/CL; reported-cash proxy",not qualified
    if mid == "L04": return v("current_assets")-v("current_liabilities"), money, "B01:100−B01:310", False
    if mid == "L05":
        return (v("current_assets")-v("cash")-v("short_investments"))-(v("current_liabilities")-v("borrowings")), money, "(B01:100−110−120)−(B01:310−320); VAS_NOWC_V1", False
    if mid == "L06": return ratio(v("cfo"), avg("current_liabilities"), percent=True, positive=True), pct, "B03:20/BQ(B01:310)×100", False
    if mid == "L07": return ratio(v("cash")+v("trade_receivables"), v("daily_operating_cash_cost"), positive=True), days, "(reported cash+eligible AR)/daily operating cash cost", False
    if mid == "D01": return ratio(v("liabilities"), v("assets"), percent=True, positive=True), pct, "B01:300/B01:270×100", False
    if mid == "D02": return ratio(v("liabilities"), v("equity"), percent=True, positive=True), pct, "B01:300/B01:400×100", False
    if mid == "D03":
        short_debt = v("borrowings")
        equity = v("equity")
        long_debt = e.book.get("long_borrowings", y)
        if long_debt is None or long_debt.value_vnd is None or long_debt.verification_status not in {"HUMAN_VERIFIED", "HUMAN_CORRECTED", "VERIFIED"}:
            return ratio(short_debt, equity, percent=True, positive=True), pct, "B01:320/B01:400×100; short-borrowings-only proxy", True
        return ratio(short_debt+v("long_borrowings"), equity, percent=True, positive=True), pct, "(B01:320+B01:338)/B01:400×100", False
    if mid == "D04":
        qualified=e.signed_note("unrestricted_cash") and e.signed_note("eligible_term_deposits")
        cash=v("unrestricted_cash")+v("eligible_term_deposits") if qualified else v("cash")
        long_item=e.book.get("long_borrowings",y)
        long_known=long_item is not None and long_item.value_vnd is not None
        debt=v("borrowings")+(v("long_borrowings") if long_known else Decimal(0))
        return debt-cash,money,("ID" if long_known else "short debt only")+("−eligible cash−eligible term deposits" if qualified else "−reported B01:110; cash-only proxy"),not qualified or not long_known
    if mid == "D05": return ratio(v("borrowings"), v("borrowings")+v("long_borrowings"), percent=True, positive=True), pct, "B01:320/ID×100", False
    if mid == "D06": return ratio(v("ebt")+abs(v("interest_expense")), abs(v("interest_expense")), positive=True), x, "(B02:50+|B02:23|)/|B02:23|", False
    if mid == "D07":
        qualified=e.signed_note("unrestricted_cash") and e.signed_note("eligible_term_deposits")
        cash=v("unrestricted_cash")+v("eligible_term_deposits") if qualified else v("cash")
        return ratio(v("borrowings")+v("long_borrowings")-cash,v("ebt")+abs(v("interest_expense"))+v("da"),positive=True),x,"net debt/(EBT+interest+D&A); EBITDA approximation" if qualified else "cash-only net debt/(EBT+interest+D&A); proxy",True
    if mid == "D08":
        complete=all(e.signed_note("long_borrowings", z) for z in (y-1,y))
        debt=sum((v("borrowings", z)+(v("long_borrowings", z) if complete else Decimal(0)) for z in (y-1,y)),Decimal(0))/2
        return ratio(v("cfo"), debt, percent=True, positive=True), pct, "B03:20/BQ(ID)×100" if complete else "B03:20/BQ(short debt)×100; proxy", not complete
    if mid == "D09": return ratio(v("cfads"), v("contractual_debt_service"), positive=True), x, "CFADS/contractual scheduled principal+interest", False
    if mid == "E01": return ratio(abs(v("cogs")), avg("inventory"), positive=True), x, "|B02:11|/BQ(B01:140)", False
    if mid == "E02": return ratio(avg("inventory"), abs(v("cogs")), positive=True)*365, days, "BQ(B01:140)/|B02:11|×365", False
    if mid == "E03":
        credit=e.book.get("credit_sales",y) is not None
        return ratio(avg("trade_receivables"),v("credit_sales") if credit else v("revenue"),positive=True)*365,days,"BQ(AR)/credit sales×365" if credit else "BQ(B01:131)/DT×365; credit-sales proxy",not credit
    if mid == "E04":
        credit=e.book.get("credit_purchases",y) is not None
        return ratio(avg("trade_payables"),v("credit_purchases") if credit else abs(v("cogs")),positive=True)*365,days,"BQ(AP)/credit purchases×365" if credit else "BQ(B01:311)/|GV|×365; credit-purchases proxy",not credit
    if mid == "E05":
        dio=ratio(avg("inventory"), abs(v("cogs")), positive=True)*365
        credit_sales=e.book.get("credit_sales",y) is not None
        credit_purchases=e.book.get("credit_purchases",y) is not None
        dso=ratio(avg("trade_receivables"), v("credit_sales") if credit_sales else v("revenue"), positive=True)*365
        dpo=ratio(avg("trade_payables"), v("credit_purchases") if credit_purchases else abs(v("cogs")), positive=True)*365
        return dio+dso-dpo,days,"DIO+DSO−DPO" if credit_sales and credit_purchases else "DIO+DSO_proxy−DPO_proxy",not(credit_sales and credit_purchases)
    if mid == "E06": return ratio(v("revenue"), avg("assets"), positive=True), x, "B02:10/BQ(B01:270)", False
    if mid == "E07": return ratio(v("revenue"), avg("fixed_assets"), positive=True), x, "B02:10/BQ(B01:220)", False
    if mid == "P01": return ratio(v("gross_profit"), v("revenue"), percent=True, positive=True), pct, "B02:20/B02:10×100", False
    if mid == "P02": return ratio(v("gross_profit")-abs(v("selling"))-abs(v("admin")), v("revenue"), percent=True, positive=True), pct, "(B02:20−|25|−|26|)/B02:10×100", False
    if mid == "P03": return ratio(v("ebt"), v("revenue"), percent=True, positive=True), pct, "B02:50/B02:10×100", False
    if mid == "P04": return ratio(v("net_income"), v("revenue"), percent=True, positive=True), pct, "B02:60/B02:10×100", False
    if mid == "P05": return ratio(v("net_income"), avg("assets"), percent=True, positive=True), pct, "B02:60/BQ(B01:270)×100", False
    if mid == "P06": return ratio(v("net_income"), avg("equity"), percent=True, positive=True), pct, "B02:60/BQ(B01:400)×100", False
    if mid == "P07":
        parent_open=v("equity",y-1)-v("nci_equity",y-1)
        parent_close=v("equity")-v("nci_equity")
        return ratio(v("net_income_parent"),(parent_open+parent_close)/2,percent=True,positive=True),pct,"B02:61/BQ(B01:400−B01:429)×100",False
    if mid == "P08":
        ebt=v("ebt")
        etr=ratio(v("current_tax")+v("deferred_tax"),ebt,positive=True)
        core=v("gross_profit")-abs(v("selling"))-abs(v("admin"))
        qualified=all(e.book.get(k,z) is not None for z in (y-1,y) for k in ("unrestricted_cash","eligible_term_deposits"))
        def invested(z:int)->Decimal:
            cash=v("unrestricted_cash",z)+v("eligible_term_deposits",z) if qualified else v("cash",z)
            return v("borrowings",z)+v("long_borrowings",z)+v("equity",z)-cash
        result=ratio(core*(1-etr),(invested(y-1)+invested(y))/2,percent=True,positive=True)
        return result,pct,"Core_OP×(1−ETR)/BQ(ID+E−Cash*); VAS_CORE_ROIC_V1; WACC separate",True
    if mid == "P09": return ratio(v("ebt")+abs(v("interest_expense")), (v("assets",y-1)-v("current_liabilities",y-1)+v("assets")-v("current_liabilities"))/2, percent=True, positive=True), pct, "EBIT/BQ(A−CL)×100", False
    if mid == "C01": return ratio(v("cfo"), v("net_income"), percent=True, positive=True), pct, "B03:20/B02:60×100", False
    if mid == "C02": return ratio(v("cfo"), v("revenue"), percent=True, positive=True), pct, "B03:20/B02:10×100", False
    if mid == "C03": return v("cfo")-v("net_income"), money, "B03:20−B02:60", False
    if mid == "C04": return ratio(v("net_income")-v("cfo"), avg("assets"), percent=True, positive=True), pct, "(B02:60−B03:20)/BQ(A)×100", False
    if mid == "C05": return v("cfo")-abs(v("capex")), money, "B03:20−|B03:21|", False
    if mid == "C06": return ratio(v("cfo"), abs(v("capex")), positive=True), x, "B03:20/|B03:21|", False
    if mid == "C07":
        history=[z for z in e.book.analysis_years if e.book.get("cfo",z) and e.book.get("net_income",z)]
        if not history: raise MissingInput("CFO and net income history")
        first=min(history)
        if first == y: raise MissingInput("multi-year CFO/NI requires at least two fiscal years")
        years=range(first,y+1)
        return ratio(sum((v("cfo",z) for z in years),Decimal(0)), sum((v("net_income",z) for z in years),Decimal(0)),percent=True,positive=True),pct,f"ΣB03:20/ΣB02:60 ({first}–{y})×100",False
    if mid == "C08": return ratio(v("cfo")-abs(v("capex")), abs(v("dividends_paid")), positive=True), x, "(B03:20−|B03:21|)/|B03:36|", False
    if mid == "Q01":
        v("trade_receivables")  # signed note pair must still inherit the parent's OCR state
        return ratio(v("trade_receivable_provision"), v("gross_trade_receivables"), percent=True, positive=True), pct, "signed trade AR provision/signed gross trade AR×100", False
    if mid == "Q02":
        v("trade_receivables")
        return ratio(v("overdue_trade_receivables_gross"), v("gross_trade_receivables"), percent=True, positive=True), pct, "signed overdue gross trade AR/signed gross trade AR×100", False
    if mid == "Q03": return ratio(abs(v("inventory_provision")), v("inventory_gross"), percent=True, positive=True), pct, "|B01:149|/B01:141×100", False
    if mid == "Q04": return ratio(v("related_party_trade_receivables"), v("trade_receivables"), percent=True, positive=True), pct, "signed related-party net trade AR/B01:131 net trade AR×100", False
    if mid == "Q05": return ratio(v("pledged_assets_book_value"), v("assets"), percent=True, positive=True), pct, "pledged assets at book value/B01:270×100", False
    raise ValueError(f"unknown metric {mid}")


def calculate_metric(book: AnalysisBook, metric_id: str, year: int) -> AnalysisMetric:
    if metric_id not in METRIC_IDS:
        raise ValueError(f"unknown core metric {metric_id}")
    e=Eval(book,year)
    try:
        value,unit,formula,proxy=_eval(metric_id,e)
        status=MetricStatus.PROXY if proxy else MetricStatus.COMPUTED
        reason=None
        if proxy and metric_id == "D03":
            reason="Chỉ tính vay ngắn hạn B01:320 đang có trong nguồn; cần xác nhận vay dài hạn B01:338 để tính tổng nợ vay. Đây là chỉ báo thay thế, không phải tỷ số nợ vay đầy đủ. Kiểm tra trạng thái xác minh của từng đầu vào trong chứng cứ."
        if proxy and metric_id == "D08":
            reason="Chỉ dùng vay ngắn hạn bình quân; vay dài hạn đầu kỳ hoặc cuối kỳ chưa được xác nhận."
        if proxy and metric_id in {"L02","L03"}:
            reason=("Dùng số tiền và tương đương tiền B01:110 vì chưa xác nhận phần tiền không bị hạn chế; "
                    "số dư báo cáo có thể không hoàn toàn khả dụng. "+
                    ("Tỷ số còn cộng phải thu khách hàng B01:131." if metric_id=='L02' else "Tỷ số chỉ xét tiền so với nợ ngắn hạn."))
        if proxy and metric_id in {"D04","D07"}:
            missing=[]
            if not e.signed_note('unrestricted_cash'):missing.append('chưa xác nhận số tiền không bị hạn chế')
            if not e.signed_note('eligible_term_deposits'):missing.append('chưa xác nhận phần tiền gửi kỳ hạn được khấu trừ')
            if metric_id=='D04' and (e.book.get('long_borrowings',year) is None or e.book.get('long_borrowings',year).value_vnd is None):
                missing.append('chưa có vay dài hạn B01:338 nên chỉ dùng vay ngắn hạn')
            elif metric_id=='D04' and not e.signed_note('long_borrowings'):
                missing.append('có dùng số vay dài hạn B01:338 mới trích xuất, chưa xác nhận')
            if metric_id=='D07':missing.append('EBITDA được xấp xỉ bằng lợi nhuận trước thuế cộng chi phí lãi vay và khấu hao')
            reason='Chỉ báo thay thế: '+('; '.join(missing) if missing else 'phạm vi tiền dùng khấu trừ chưa đầy đủ')+'.'
        if proxy and metric_id=='E03':
            reason='Dùng tổng doanh thu thuần thay doanh thu bán chịu vì thiếu doanh thu bán chịu; số ngày phải thu chỉ là chỉ báo thay thế.'
        if proxy and metric_id=='E04':
            reason='Dùng giá vốn thay giá trị mua chịu vì thiếu dữ liệu mua chịu; số ngày phải trả chỉ là chỉ báo thay thế.'
        if proxy and metric_id=='E05':
            absent=[]
            if e.book.get('credit_sales',year) is None:absent.append('doanh thu bán chịu được thay bằng doanh thu thuần')
            if e.book.get('credit_purchases',year) is None:absent.append('mua chịu được thay bằng giá vốn')
            reason='Chu kỳ chuyển đổi tiền là chỉ báo thay thế vì '+ '; '.join(absent)+'.'
        if proxy and metric_id=='P08':
            reason='ROIC dùng lợi nhuận hoạt động cốt lõi sau thuế suất hiệu dụng và vốn đầu tư bình quân đầu–cuối kỳ; đây là công thức ước tính, phụ thuộc phạm vi tiền được khấu trừ và không phải WACC.'
    except MissingInput as error:
        value,unit,formula,status,reason=None,_unit_for(metric_id),FORMULA_BY_ID[metric_id],MetricStatus.MISSING_INPUT,str(error)
    except NotApplicable as error:
        value,unit,formula,status,reason=None,_unit_for(metric_id),FORMULA_BY_ID[metric_id],MetricStatus.NOT_APPLICABLE,str(error)
    formula_version="VAS_NOWC_V1" if metric_id=="L05" else "VAS_CORE_ROIC_V1" if metric_id=="P08" else "P5_V1"
    verification = weakest(*(d.verification_status for d in e.inputs))
    input_status = ("MISSING_INPUT" if status == MetricStatus.MISSING_INPUT else
                    "UNVERIFIED_INPUT" if verification not in {"VERIFIED", "HUMAN_VERIFIED", "HUMAN_CORRECTED"}
                    else "VERIFIED_INPUT")
    return AnalysisMetric(metric_id=metric_id,fiscal_year=year,value=value,unit=unit,formula=formula,formula_version=formula_version,
                          status=status,verification_status=verification,input_status=input_status,
                          inputs=list(dict.fromkeys(f"{d.concept}@{d.year}" for d in e.inputs)),
                          evidences=union_evidence(*(d.evidences for d in e.inputs)),reason=reason)


def calculate_all(book: AnalysisBook, years: list[int] | None = None) -> list[AnalysisMetric]:
    years = years or [y for y in book.analysis_years if book.get("revenue",y)]
    return [calculate_metric(book,mid,y) for y in years for mid in METRIC_IDS]


def calculate_supplements(book: AnalysisBook, year: int) -> list[AnalysisMetric]:
    e=Eval(book,year)
    specs=[
        ("DIO_GROSS",lambda: e.ratio(e.avg("inventory_gross"),abs(e.v("cogs")),positive=True)*365,"ngày","BQ(B01:141)/|B02:11|×365"),
        ("ETR",lambda: e.ratio(e.v("current_tax")+e.v("deferred_tax"),e.v("ebt"),percent=True,positive=True),"%","(B02:51+B02:52)/B02:50×100"),
        ("ID_A",lambda: e.ratio(e.v("borrowings")+e.v("long_borrowings"),e.v("assets"),percent=True,positive=True),"%","ID/A×100"),
        ("NET_DEBT_ALL_TERM_SCENARIO",lambda: e.v("borrowings")+e.v("long_borrowings")-e.v("cash")-e.v("term_deposits"),"VND","ID−reported cash−all term deposits; conditional scenario"),
        ("FCCR_ACCRUAL",lambda: e.ratio(e.v("ebt")+abs(e.v("interest_expense"))+e.v("store_rent"),abs(e.v("interest_expense"))+e.v("store_rent"),positive=True),"lần","(EBT+interest+recognized store rent)/(interest+recognized store rent)"),
    ]
    out=[]
    for mid,fn,unit,formula in specs:
        e.inputs=[]
        try:value=fn();status=MetricStatus.COMPUTED;reason=None
        except MissingInput as error:value=None;status=MetricStatus.MISSING_INPUT;reason=str(error)
        except NotApplicable as error:value=None;status=MetricStatus.NOT_APPLICABLE;reason=str(error)
        out.append(AnalysisMetric(metric_id=mid,fiscal_year=year,value=value,unit=unit,formula=formula,
                  status=status,verification_status=weakest(*(d.verification_status for d in e.inputs)),
                  inputs=list(dict.fromkeys(f"{d.concept}@{d.year}" for d in e.inputs)),
                  evidences=union_evidence(*(d.evidences for d in e.inputs)),reason=reason))
    return out


def calculate_dupont(book: AnalysisBook, year: int) -> dict[str, AnalysisMetric]:
    e=Eval(book,year)
    formulas={
        "net_margin":lambda: e.ratio(e.v("net_income"),e.v("revenue")),
        "asset_turnover":lambda: e.ratio(e.v("revenue"),e.avg("assets")),
        "equity_multiplier":lambda: e.ratio(e.avg("assets"),e.avg("equity")),
        "tax_burden":lambda: e.ratio(e.v("net_income"),e.v("ebt")),
        "earnings_burden":lambda: e.ratio(e.v("ebt"),e.v("ebt")+abs(e.v("interest_expense"))),
        "ebit_margin":lambda: e.ratio(e.v("ebt")+abs(e.v("interest_expense")),e.v("revenue")),
        "roe":lambda: e.ratio(e.v("net_income"),e.avg("equity")),
    }
    results={}
    for name,fn in formulas.items():
        e.inputs=[]
        try:value=fn();status=MetricStatus.COMPUTED;reason=None
        except MissingInput as error:value=None;status=MetricStatus.MISSING_INPUT;reason=str(error)
        except NotApplicable as error:value=None;status=MetricStatus.NOT_APPLICABLE;reason=str(error)
        results[name]=AnalysisMetric(metric_id=f"DUPONT_{name.upper()}",fiscal_year=year,value=value,unit="ratio",
            formula=name,status=status,verification_status=weakest(*(d.verification_status for d in e.inputs)),
            inputs=list(dict.fromkeys(f"{d.concept}@{d.year}" for d in e.inputs)),
            evidences=union_evidence(*(d.evidences for d in e.inputs)),reason=reason)
    return results
