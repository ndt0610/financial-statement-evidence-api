"""Structured, counter-checked findings from multi-year calculated measures."""

from __future__ import annotations

from decimal import Decimal

from app.models.full_analysis import AnalysisMetric, FinancialBridge, MetricStatus, StatementType, StructuredFinding
from app.services.analysis_book import AnalysisBook, union_evidence, weakest
from app.services.conclusion_guard import ClaimContext, guard_conclusion


FORBIDDEN_CONCLUSION_KEYS = frozenset({
    "INSOLVENT_FROM_CR", "POOR_PROFIT_FROM_REVENUE_DECLINE", "OBSOLETE_FINISHED_GOODS_FROM_INVENTORY_GROWTH",
    "CFO_DECLINE_ONLY_INVENTORY", "B01_INVENTORY_EQUALS_B03_CASH_EFFECT",
    "CURRENT_RATIO_GUARANTEES_LIQUIDITY", "CANNOT_PAY_INTEREST_FROM_ICR", "CFO_NEGATIVE_WHEN_POSITIVE",
    "CASH_INCREASED_FROM_PROFIT", "ALL_CFI_IS_CAPEX", "TRADE_AR_OVERDUE_WITHOUT_AGING",
    "ALL_52_IMPLEMENTED_WHEN_MISSING", "ROE_GAIN_ONLY_FROM_DEBT", "ALL_TERM_DEPOSITS_FREE",
    "FCCR_GUARANTEES_FUTURE_PAYMENTS", "NET_DIO_DROP_PROVES_TURNOVER",
})


def _cite(evidences) -> str:
    seen=[]
    for ev in evidences:
        loc=ev.locator
        if loc.source_kind == "pdf":
            token=f"{loc.table_id or 'PDF'} trang {loc.page_number}"
        else:
            token=f"{loc.sheet_name}!{loc.cell_range}"
        if token not in seen:seen.append(token)
    return "; ".join(seen[:8])


def interpret(book: AnalysisBook, metrics: list[AnalysisMetric], bridges: list[FinancialBridge]) -> list[StructuredFinding]:
    by={(m.metric_id,m.fiscal_year):m for m in metrics}
    years=sorted({m.fiscal_year for m in metrics})
    findings=[]

    def m(code,year):return by.get((code,year))

    def emit(key,topic,kind,year,primary,counter,text,questions=(),extra_sources=(),extra_metrics=()):
        if key in FORBIDDEN_CONCLUSION_KEYS:
            raise ValueError(f"forbidden conclusion key: {key}")
        source=[m(code,year) for code in [*primary,*counter] if m(code,year) is not None]
        source=[x for x in [*source,*extra_metrics] if x.value is not None and x.evidences]
        trend_code=next((code for code in primary if any(m(code,z) and m(code,z).value is not None for z in years if z<=year)),None)
        trend_items=[m(trend_code,z) for z in years if z<=year and trend_code and m(trend_code,z) and m(trend_code,z).value is not None]
        note_trend=sorted((d for d in extra_sources if d.concept=="raw_materials" and d.value_vnd is not None),key=lambda d:d.year) if not trend_items else []
        observations=[{"year":item.fiscal_year,"value":str(item.value),"unit":item.unit} for item in trend_items]
        if note_trend:
            trend_code="raw_materials"
            observations=[{"year":item.year,"value":str(item.value_vnd),"unit":"VND"} for item in note_trend]
        values=[item.value for item in trend_items] or [item.value_vnd for item in note_trend]
        changes=[values[i]-values[i-1] for i in range(1,len(values))]
        directions=[1 if delta>0 else -1 for delta in changes if delta!=0]
        reversals=sum(directions[i]!=directions[i-1] for i in range(1,len(directions)))
        first,last=(values[0],values[-1]) if values else (None,None)
        trend={"metric_id":trend_code,"observations":observations,"period_count":len(observations),
               "reversals":reversals,"absolute_change":str(last-first) if first is not None and last is not None else None,
               "relative_change_pct":str((last-first)/abs(first)*100)
               if first is not None and last is not None and first!=0 else None}
        basis="HEURISTIC" if key.startswith("LOW_CASH_CONVERSION") else "HISTORY" if len(observations)>1 else "MATH"
        comparison={"kind":basis,"source":"100% CFO/LNST diagnostic threshold; not a credit covenant" if basis=="HEURISTIC"
                    else "same-company same-scope observations in selected source documents" if basis=="HISTORY"
                    else "accounting identity/formula from sourced statement lines",
                    "formula":m(trend_code,year).formula if trend_code and m(trend_code,year) else
                    "current-year disclosure minus prior-year disclosure" if note_trend else None}
        bridge_name=None if kind==StatementType.DIRECT_DISCLOSURE else "PROFIT" if topic=="profitability" else "CFO" if topic=="cash_flow" else "FUNDING" if topic in {"inventory","liquidity_and_debt","asset_quality"} else None
        bridge=next((b for b in bridges if b.bridge_id==bridge_name and b.fiscal_year==year),None)
        components=[step for step in bridge.steps if step.signed_impact is not None] if bridge else []
        drivers=[{"source":"accounting_bridge","bridge_id":bridge.bridge_id,"code":step.code,
                  "signed_impact_vnd":str(step.signed_impact)} for step in components[:4]]
        for datum in extra_sources:
            if datum.concept in {"raw_materials","finished_goods","merchandise","term_deposits","restricted_term_deposits"}:
                drivers.append({"source":"supplemental_fact","code":datum.concept,
                                "fiscal_year":datum.year,"value_vnd":str(datum.value_vnd),
                                "verification_status":datum.verification_status})
        if not drivers:drivers=[{"source":"NONE","reason":"no sourced component bridge available"}]
        counterpoints=[{"metric_id":code,"value":str(item.value) if item.value is not None else None,
                        "status":item.status.value,"reason":item.reason} for code in counter if (item:=m(code,year))]
        missing=[f"{code}: {item.reason or item.status.value}" for code in [*primary,*counter]
                 if (item:=m(code,year)) and item.value is None]
        unreviewed=[f"{datum.concept}@{datum.year}: {datum.verification_status}" for datum in extra_sources
                    if datum.verification_status not in {"VERIFIED","HUMAN_VERIFIED"}]
        missing.extend(unreviewed)
        evidence=union_evidence(*(x.evidences for x in source),*(d.evidences for d in extra_sources),
                                *(x.evidences for x in trend_items),*(s.evidences for s in components))
        if not evidence:return
        narrative=text+f" [Nguồn: {_cite(evidence)}.]"
        findings.append(StructuredFinding(finding_id=f"{key}_{year}",topic=topic,statement_type=kind,
            primary_metrics=list(primary),counter_evidence_checked=[c for c in counter if m(c,year)]+
                [f"{d.concept}@{d.year}" for d in extra_sources],
            conclusion_key=key,narrative_vi=narrative,fiscal_year=year,
            verification_status=weakest(*(x.verification_status for x in source),
                                        *(d.verification_status for d in extra_sources),
                                        *(x.verification_status for x in trend_items)),
            evidences=evidence,follow_up_questions=list(questions),trend=trend,
            comparison=comparison,drivers=drivers,counterpoints=counterpoints,missing_inputs=missing,
            analysis_sections={"headline":text,"comparison":trend,"drivers":drivers,
                "cross_statement":{"sources":sorted({datum.source_line for datum in extra_sources if datum.source_line}),
                    "status":"AVAILABLE" if len({datum.source_line.split(':')[0] for datum in extra_sources
                                                  if datum.source_line and ':' in datum.source_line}) > 1
                    else "INSUFFICIENT_LINKED_INPUT"},
                "evidence_status":weakest(*(x.verification_status for x in source),
                                          *(d.verification_status for d in extra_sources),
                                          *(x.verification_status for x in trend_items)),
                "caveat":"Phân rã số học không xác định nguyên nhân kinh doanh; cần kiểm tra thuyết minh có chứng cứ.",
                "next_check":list(questions)}))

    for year in years:
        revenue_growth=m("G02",year); margin=m("P01",year); net=m("P04",year)
        prev_margin=m("P01",year-1);prev_net=m("P04",year-1)
        if revenue_growth and margin and net and prev_margin and prev_net and all(x.value is not None for x in (revenue_growth,margin,net,prev_margin,prev_net)):
            if revenue_growth.value<0 and margin.value>prev_margin.value and net.value>prev_net.value:
                # The bridge confirms the accounting identity; it does not identify gold price or volume effects.
                bridge=next((b for b in bridges if b.bridge_id=="PROFIT" and b.fiscal_year==year),None)
                context=f"; LNST thay đổi {bridge.target_delta} VND" if bridge and bridge.target_delta is not None else ""
                bridge_context = ("Cầu nối lợi nhuận đã khớp các cấu phần ghi nhận."
                                  if bridge and bridge.status == "BALANCED" else
                                  "Cầu nối lợi nhuận còn phần chưa đối chiếu; cần rà lại cấu phần trước khi quy nguyên nhân.")
                emit("MARGIN_OFFSETS_REVENUE_DECLINE","profitability",StatementType.ANALYTICAL_INFERENCE,year,
                     ("G02","P01","P04"),(),
                     f"Doanh thu giảm {abs(revenue_growth.value):.2f}%, trong khi biên gộp tăng từ {prev_margin.value:.2f}% lên {margin.value:.2f}% và biên ròng tăng từ {prev_net.value:.2f}% lên {net.value:.2f}%{context}. Biên lợi nhuận cao hơn làm giảm áp lực từ doanh thu suy giảm. {bridge_context} Cần phân tích thêm giá, sản lượng và cơ cấu sản phẩm.",
                     ("Biên gộp thay đổi do giá, sản lượng hay cơ cấu sản phẩm?",),
                     extra_metrics=(prev_margin,prev_net))
        at=m("E06",year);at0=m("E06",year-1);roe=m("P06",year);roe0=m("P06",year-1)
        if all(x and x.value is not None for x in (at,at0,roe,roe0,net,prev_net)) and year>years[0]:
            if roe.value>roe0.value and net.value>prev_net.value and at.value<at0.value:
                emit("DUPONT_MARGIN_LED_ROE","profitability",StatementType.ANALYTICAL_INFERENCE,year,
                     ("P06","P04","E06"),("P05",),
                     f"ROE tăng từ {roe0.value:.2f}% lên {roe.value:.2f}%; biên ròng tăng {prev_net.value:.2f}% → {net.value:.2f}% dù vòng quay tài sản giảm {at0.value:.2f} → {at.value:.2f} lần. Cần xem thêm đòn bẩy bình quân trước khi quy toàn bộ thay đổi cho hoạt động.",
                     extra_metrics=(roe0,prev_net,at0))
        cash=m("C01",year); income=m("P04",year)
        if cash and cash.value is not None and income and income.value and income.value>0 and cash.value<100:
            history=[m("C01",z) for z in years if z<=year]
            low=sum(x is not None and x.value is not None and x.value<100 for x in history)
            emit("LOW_CASH_CONVERSION_MULTI_YEAR" if low>=2 else "LOW_CASH_CONVERSION_SINGLE_YEAR",
                 "cash_flow",StatementType.MATH_OBSERVATION,year,("C01",),("C07",),
                 f"CFO/LNST năm {year} là {cash.value:.2f}%; {low}/{len(history)} kỳ quan sát có tỷ lệ dưới 100%. Đây là đối chiếu dòng tiền và lợi nhuận, chưa phải kết luận lợi nhuận sai hoặc không bền vững.",
                 ("Tồn kho, phải trả và khoản phi tiền tệ đã đóng góp bao nhiêu vào CFO?",),
                 extra_metrics=tuple(x for x in history if x is not None))
        cfo=next((b for b in bridges if b.bridge_id=="CFO" and b.fiscal_year==year),None)
        if cfo and cfo.target_delta is not None and cfo.evidences:
            components={x.code:x.signed_impact for x in cfo.steps}
            if components.get("cf_inventory") is not None and components["cf_inventory"]<0:
                emit("CFO_INVENTORY_CASH_USE","cash_flow",StatementType.ANALYTICAL_INFERENCE,year,
                     ("C01",),("C03",),
                     f"B03 ghi tác động tồn kho {components['cf_inventory']} VND đến CFO; tác động phải trả {components.get('cf_payables')} VND và phải thu {components.get('cf_receivables')} VND cũng đã được đối chiếu. Không đồng nhất dòng B03 với chênh lệch tồn kho cuối kỳ trên B01.",
                     extra_sources=tuple(d for code in ("cf_inventory","cf_payables","cf_receivables","cfo") if (d:=book.get(code,year))))
        inv=m("G04",year);dio=m("E02",year);gross=m("DIO_GROSS",year);dio0=m("E02",year-1)
        if inv and dio and gross and dio0 and all(x.value is not None for x in (inv,dio,gross,dio0)) and dio.value>dio0.value:
            emit("INVENTORY_DAYS_RISE_GROSS_AND_NET","inventory",StatementType.ANALYTICAL_INFERENCE,year,
                 ("E02","DIO_GROSS","G04"),("Q03",),
                 f"DIO thuần tăng {dio0.value:.2f} → {dio.value:.2f} ngày; DIO gộp năm {year} là {gross.value:.2f} ngày. Tồn kho chiếm {inv.value:.2f}% tài sản; số bình quân hai mốc không chứng minh hàng thành phẩm ế.",
                 ("Cơ cấu nguyên vật liệu, thành phẩm và dự phòng thay đổi thế nào?",),
                 extra_metrics=(dio0,))
        gross0=m("DIO_GROSS",year-1)
        if all(x and x.value is not None for x in (dio,dio0,gross,gross0)) and dio.value<dio0.value:
            verdict=guard_conclusion("INVENTORY_TURNOVER_IMPROVED",ClaimContext(
                numbers={"net_dio_now":dio.value,"net_dio_before":dio0.value,
                         "gross_dio_now":gross.value,"gross_dio_before":gross0.value},
                flags={"provision_effect_checked":m("Q03",year) is not None and m("Q03",year).value is not None}))
            if verdict.allowed:
                emit("HISTORICAL_INVENTORY_TURNOVER_IMPROVED","inventory",StatementType.MATH_OBSERVATION,year,
                     ("E02","DIO_GROSS"),("Q03",),
                     f"DIO thuần và gộp cùng giảm trong các kỳ so sánh; vòng quay ước tính theo bình quân đầu/cuối năm cải thiện. Cần kiểm tra mùa vụ và cơ cấu trước khi suy ra hàng bán thực tế nhanh hơn.",
                     extra_metrics=(dio0,gross0))
            else:
                emit("NET_DIO_DROP_PROVISION_TRAP","inventory",StatementType.ANALYTICAL_INFERENCE,year,
                     ("E02","DIO_GROSS"),("Q03",),
                     "DIO thuần giảm nhưng DIO gộp không xác nhận vòng quay tốt hơn; phải xét trích lập dự phòng trước khi kết luận hiệu quả luân chuyển.",
                     extra_metrics=(dio0,gross0))
        raw_now,raw_prev=book.get("raw_materials",year),book.get("raw_materials",year-1)
        fin_now,fin_prev=book.get("finished_goods",year),book.get("finished_goods",year-1)
        if all(x is not None and x.value_vnd is not None for x in (raw_now,raw_prev,fin_now,fin_prev)):
            raw_delta=raw_now.value_vnd-raw_prev.value_vnd
            fin_delta=fin_now.value_vnd-fin_prev.value_vnd
            if raw_delta>0 and fin_delta<0:
                emit("RAW_MATERIALS_UP_FINISHED_GOODS_DOWN","inventory",StatementType.DIRECT_DISCLOSURE,year,
                     (),(),
                     f"Thuyết minh tồn kho ghi nguyên vật liệu tăng {raw_delta} VND, thành phẩm giảm {abs(fin_delta)} VND. Đây là dịch chuyển giá trị theo nhóm hàng; chưa tách được giá và khối lượng, cũng không chứng minh thành phẩm ế.",
                     ("Nguyên vật liệu có chuyển thành doanh thu và CFO ở kỳ sau?",),
                     (raw_now,raw_prev,fin_now,fin_prev))
        current=m("L01",year);quick=m("L02",year);cash_ratio=m("L03",year);icr=m("D06",year)
        if all(x and x.value is not None for x in (current,quick,cash_ratio)) and current.value>1 and quick.value<1:
            deposit=book.get("term_deposits",year)
            deposit_text=f" Tiền gửi kỳ hạn được công bố {deposit.value_vnd} VND nhưng khả năng dùng tự do chưa được xác nhận." if deposit and deposit.value_vnd is not None else ""
            icr_text=f" ICR theo EBIT là {icr.value:.2f} lần." if icr and icr.value is not None else ""
            emit("LIQUIDITY_COMPONENTS_DIVERGE","liquidity_and_debt",StatementType.ANALYTICAL_INFERENCE,year,
                 ("L01","L02","L03"),("D06","D04"),
                 f"Current ratio {current.value:.2f} lần nhưng quick ratio chặt {quick.value:.2f} lần; cần xem cấu phần và lịch thanh toán.{deposit_text}{icr_text} Các tỷ số này không tự chứng minh mất khả năng thanh toán.",
                 ("Bao nhiêu tiền gửi được rút hoặc dùng trả nợ đúng hạn?",),
                 extra_sources=(deposit,) if deposit else ())
        debt=m("D03",year);debt0=m("D03",year-1)
        if debt and debt0 and debt.value is not None and debt0.value is not None and debt.value>debt0.value:
            emit("INTEREST_DEBT_LEVERAGE_RISE","liquidity_and_debt",StatementType.MATH_OBSERVATION,year,
                 ("D03","D04"),("D06","FCCR_ACCRUAL"),
                 f"Nợ vay chịu lãi/vốn chủ tăng {debt0.value:.2f}% → {debt.value:.2f}%. Lãi, thuê cửa hàng và kỳ đáo hạn phải được đánh giá riêng; ICR/FCCR lịch sử không thay DSCR.",
                 extra_metrics=(debt0,))
        fccr=m("FCCR_ACCRUAL",year)
        if fccr and fccr.value is not None:
            commitment=book.get("rent_commitment_next_12m",year)
            future_text=(f" Cam kết thuê tối thiểu trong 12 tháng tới là {commitment.value_vnd} VND."
                         if commitment and commitment.value_vnd is not None else "")
            emit("RENT_AND_INTEREST_COVERAGE_HISTORICAL","liquidity_and_debt",StatementType.MATH_OBSERVATION,year,
                 ("FCCR_ACCRUAL",),("D06","D09"),
                 f"Bao phủ lãi và thuê cửa hàng đã ghi nhận theo số kế toán là {fccr.value:.2f} lần.{future_text} Cam kết thuê kỳ tới và trả gốc cần lịch tiền riêng; đây không phải DSCR.",
                 extra_sources=(commitment,) if commitment else ())
        fund=next((b for b in bridges if b.bridge_id=="FUNDING" and b.fiscal_year==year),None)
        if fund and fund.target_delta is not None and fund.unexplained_residual==0:
            emit("ASSETS_EQUAL_FUNDING_SOURCES","liquidity_and_debt",StatementType.MATH_OBSERVATION,year,
                 ("L04",),("D03",),
                 f"Tổng tài sản đổi {fund.target_delta} VND, bằng biến động nợ phải trả cộng vốn chủ. Các cấu phần tài sản/nợ chưa tách hết vẫn hiện phần dư {fund.component_residuals}; không gán một khoản vay riêng cho một tài sản riêng.",
                 extra_sources=tuple(d for code in ("assets","liabilities","equity") for z in (year-1,year) if (d:=book.get(code,z))))
        provision=m("Q03",year)
        if provision and provision.value is not None:
            emit("INVENTORY_PROVISION_DISCLOSED","asset_quality",StatementType.MATH_OBSERVATION,year,
                 ("Q03",),("E02","DIO_GROSS"),
                 f"Dự phòng tồn kho bằng {provision.value:.2f}% giá gốc tồn kho cuối năm; so DIO gộp và thuần trước khi nhận xét tốc độ luân chuyển. Tỷ lệ thấp không chứng minh toàn bộ tồn kho được định giá an toàn.")
    return findings
