"""Four accounting bridges with explicit sign conventions and visible residuals."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from app.models.full_analysis import BridgeStep, FinancialBridge
from app.services.analysis_book import AnalysisBook, SourceDatum, union_evidence, weakest


def _sources(book: AnalysisBook, *keys: tuple[str, int]) -> list[SourceDatum]:
    return [datum for concept,year in keys if (datum := book.get(concept,year)) is not None]


def _value(book: AnalysisBook, concept: str, year: int) -> Decimal | None:
    datum=book.get(concept,year)
    return datum.value_vnd if datum else None


def _step(book: AnalysisBook, code: str, label: str, raw: Decimal | None, impact: Decimal | None,
          *keys: tuple[str,int]) -> BridgeStep:
    sources=_sources(book,*keys)
    years=sorted({year for _,year in keys})
    same_concept=len({concept for concept,_ in keys})==1
    prior=_value(book,keys[0][0],years[0]) if same_concept and len(years)==2 else None
    current=_value(book,keys[0][0],years[-1]) if same_concept and len(years)==2 else (
        _value(book,keys[0][0],years[0]) if same_concept and len(years)==1 else None)
    return BridgeStep(code=code,label=label,raw_change=raw,signed_impact=impact,
                      prior_value=prior,current_value=current,
                      evidences=union_evidence(*(d.evidences for d in sources)),
                      verification_status=weakest(*(d.verification_status for d in sources)))


def _delta(book: AnalysisBook, concept: str, year: int) -> Decimal | None:
    a,b=_value(book,concept,year),_value(book,concept,year-1)
    return a-b if a is not None and b is not None else None


def _cost_delta(book: AnalysisBook, concept: str, year: int) -> tuple[Decimal | None, Decimal | None]:
    a,b=_value(book,concept,year),_value(book,concept,year-1)
    if a is None or b is None:return None,None
    return a-b,-(abs(a)-abs(b))


def _tax_component_delta(book: AnalysisBook, concept: str, year: int) -> Decimal | None:
    """Orient B02 tax lines only when their sum matches EBT minus net income.

    Some source PDFs encode both tax rows as negative amounts. The bridge may
    reverse both signs only when that convention is proven by the same-year
    B02:50/B02:60 identity; an unmatched year remains an unknown component.
    """
    def signed_expense(period: int) -> Decimal | None:
        keys=("current_tax","deferred_tax","ebt","net_income")
        values=[_value(book,key,period) for key in keys]
        if any(value is None for value in values):return None
        current,deferred,ebt,income=values
        total=current+deferred
        expected=ebt-income
        if total==expected:return _value(book,concept,period)
        if -total==expected:return -_value(book,concept,period)
        return None
    now,prior=signed_expense(year),signed_expense(year-1)
    return now-prior if now is not None and prior is not None else None


def _assemble(book: AnalysisBook, bridge_id: str, year: int, target: Decimal | None,
              steps: list[BridgeStep], target_sources: list[SourceDatum], *, uses: list[BridgeStep] | None = None,
              memo_steps: list[BridgeStep] | None = None, equity_components: list[BridgeStep] | None = None,
              component_residuals: dict[str, Decimal] | None = None, explanation: str | None = None) -> FinancialBridge:
    known=sum((step.signed_impact for step in steps if step.signed_impact is not None),Decimal(0))
    residual=target-known if target is not None else None
    incomplete=target is None or any(step.signed_impact is None for step in steps)
    components=component_residuals or {}
    status="MISSING_INPUT" if target is None else "PARTIAL_COMPONENTS" if incomplete or any(x != 0 for x in components.values()) else "RECONCILIATION_WARNING" if residual != 0 else "BALANCED"
    all_steps=[*steps,*(uses or []),*(memo_steps or []),*(equity_components or [])]
    evidence=union_evidence(*(step.evidences for step in all_steps),*(d.evidences for d in target_sources))
    return FinancialBridge(bridge_id=bridge_id,fiscal_year=year,target_delta=target,steps=steps,
                           unexplained_residual=residual,status=status,
                           verification_status=weakest(*(step.verification_status for step in all_steps),
                                                       *(d.verification_status for d in target_sources)),
                           evidences=evidence,uses=uses or [],memo_steps=memo_steps or [],
                           equity_components=equity_components or [],component_residuals=components,explanation=explanation)


def profit_bridge(book: AnalysisBook, year: int) -> FinancialBridge:
    prior=year-1
    rev,rev0=_value(book,"revenue",year),_value(book,"revenue",prior)
    gross,gross0=_value(book,"gross_profit",year),_value(book,"gross_profit",prior)
    # The second term is the exact complement of the first under this decomposition.
    with localcontext() as context:
        context.rounding=ROUND_HALF_EVEN
        revenue_effect=((rev-rev0)*gross0/rev0).quantize(Decimal("1"),rounding=ROUND_HALF_EVEN) if None not in (rev,rev0,gross0) and rev0 else None
    margin_effect=(gross-gross0)-revenue_effect if None not in (gross,gross0,revenue_effect) else None
    margin_raw=(gross/rev)-(gross0/rev0) if None not in (gross,rev,gross0,rev0) and rev and rev0 else None
    steps=[
        _step(book,"revenue_effect","Doanh thu, giữ biên gộp kỳ trước",rev-rev0 if rev is not None and rev0 is not None else None,revenue_effect,("revenue",year),("revenue",prior),("gross_profit",prior)),
        _step(book,"margin_effect","Biên gộp trên doanh thu kỳ này",margin_raw,margin_effect,("revenue",year),("revenue",prior),("gross_profit",year),("gross_profit",prior)),
    ]
    for concept,label in (("selling","Chi phí bán hàng"),("admin","Chi phí quản lý"),
                          ("finance_expense","Chi phí tài chính")):
        raw,impact=_cost_delta(book,concept,year)
        steps.append(_step(book,concept,label,raw,impact,(concept,year),(concept,prior)))
    for concept,label in (("current_tax","Thuế hiện hành"),("deferred_tax","Thuế hoãn lại")):
        raw=_tax_component_delta(book,concept,year)
        steps.append(_step(book,concept,label,raw,-raw if raw is not None else None,
                           (concept,year),(concept,prior),("ebt",year),("ebt",prior),
                           ("net_income",year),("net_income",prior),
                           ("current_tax",year),("current_tax",prior),
                           ("deferred_tax",year),("deferred_tax",prior)))
    for concept,label in (("finance_income","Doanh thu tài chính"),("other_net","Lợi nhuận khác")):
        raw=_delta(book,concept,year)
        steps.append(_step(book,concept,label,raw,raw,(concept,year),(concept,prior)))
    target=_delta(book,"net_income",year)
    interest_delta=_delta(book,"interest_expense",year)
    memo=[_step(book,"interest_expense_memo","Lãi vay nằm trong chi phí tài chính",
                interest_delta,interest_delta,("interest_expense",year),("interest_expense",prior))]
    return _assemble(book,"PROFIT",year,target,steps,_sources(book,("net_income",year),("net_income",prior)),
                     memo_steps=memo,
                     explanation="ΔLNST = Δlợi nhuận hoạt động lõi + Δtài chính + Δkhoản khác − Δthuế. Dấu B02:51/52 chỉ được chuẩn hóa khi tổng hai dòng khớp tuyệt đối B02:50 − B02:60 của từng năm; nếu không, thành phần thuế bị giữ ở trạng thái thiếu. Tác động doanh thu được làm tròn đến đồng theo quy tắc nửa về số chẵn (half-even); phần bù số học được gán cho biên gộp.")


def cfo_bridge(book: AnalysisBook, year: int) -> FinancialBridge:
    keys=("pre_wc","cf_receivables","cf_inventory","cf_payables","cf_prepaid","cf_securities",
          "interest_paid","tax_paid", "other_operating_received", "other_operating_paid")
    if book.get("other_operating_received",year) is None:
        keys=tuple(key for key in keys if key!="other_operating_received")
    steps=[_step(book,key,key,_value(book,key,year),_value(book,key,year),(key,year)) for key in keys]
    return _assemble(book,"CFO",year,_value(book,"cfo",year),steps,_sources(book,("cfo",year)),
                     explanation="Trong từng năm: B03:08 cộng các dòng B03:09–17 có dấu bằng B03:20. Chi tiền trên B03 giữ dấu âm của báo cáo. Cột chênh lệch chỉ so sánh mức đóng góp giữa hai năm.")


def funding_bridge(book: AnalysisBook, year: int) -> FinancialBridge:
    prior=year-1
    liabilities=_delta(book,"liabilities",year);equity=_delta(book,"equity",year)
    steps=[_step(book,"liabilities","Δ nợ phải trả",liabilities,liabilities,("liabilities",year),("liabilities",prior)),
           _step(book,"equity","Δ vốn chủ",equity,equity,("equity",year),("equity",prior))]
    use_keys=("inventory","term_deposits","cash")
    uses=[_step(book,key,f"Δ {key}",_delta(book,key,year),_delta(book,key,year),(key,year),(key,prior)) for key in use_keys]
    target=_delta(book,"assets",year)
    components={}
    if target is not None and all(step.signed_impact is not None for step in uses):
        components["unclassified_assets"]=target-sum((s.signed_impact for s in uses),Decimal(0))
    debt_keys=("borrowings","trade_payables","tax_payable")
    known_debt=[_delta(book,key,year) for key in debt_keys]
    if liabilities is not None and all(x is not None for x in known_debt):
        components["unclassified_liabilities"]=liabilities-sum(known_debt,Decimal(0))
    equity_names=("equity_net_income","new_share_capital_net","declared_dividends",
                  "welfare_allocation","board_management_allocation")
    equity_rows=[_value(book,key,year) for key in equity_names]
    equity_components=[_step(book,key,key,value,value,(key,year)) for key,value in zip(equity_names,equity_rows)]
    if equity is not None and all(value is not None for value in equity_rows):
        components["unclassified_equity"]=equity-sum(equity_rows,Decimal(0))
    return _assemble(book,"FUNDING",year,target,steps,_sources(book,("assets",year),("assets",prior)),
                     uses=uses,equity_components=equity_components,component_residuals=components,
                     explanation="Biến động tài sản bằng biến động nợ phải trả cộng biến động vốn chủ. Phần tài sản, nợ và vốn chủ chưa phân loại được giữ riêng, không gán thành dòng nguồn chưa có.")


def cash_bridge(book: AnalysisBook, year: int) -> FinancialBridge:
    keys=("cfo","cfi","cff","cash_fx")
    steps=[_step(book,key,key,_value(book,key,year),_value(book,key,year),(key,year)) for key in keys]
    target=_delta(book,"cash",year)
    return _assemble(book,"CASH",year,target,steps,_sources(book,("cash",year),("cash",year-1)),
                     explanation="Dòng tiền kinh doanh B03:20 cộng đầu tư B03:30, tài chính B03:40 và ảnh hưởng tỷ giá B03:61 bằng biến động tiền B01:110.")


def calculate_bridges(book: AnalysisBook, year: int) -> list[FinancialBridge]:
    return [profit_bridge(book,year),cfo_bridge(book,year),funding_bridge(book,year),cash_bridge(book,year)]
