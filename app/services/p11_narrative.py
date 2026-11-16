"""Financial observations built from sourced Decimal values, never from display text."""
from decimal import Decimal, ROUND_HALF_EVEN

from app.services.analysis_book import union_evidence, weakest


def _number(value, scale=Decimal(1)):
    amount=(value/scale).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)
    return f'{amount:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def build_p11_findings(book, metrics, bridges, years):
    if not years: return []
    year=years[-1]; prior=year-1 if year-1 in years else None
    by_metric={(m.metric_id,m.fiscal_year):m for m in metrics}
    bridge={(b.bridge_id,b.fiscal_year):b for b in bridges}
    findings=[]
    def datum(name, y=year):
        return book.get(name,y)
    def val(name,y=year):
        item=datum(name,y)
        note_concepts={'raw_materials','finished_goods','merchandise','work_in_progress',
                       'inventory_in_transit','real_estate_inventory','inventory_tools','consigned_inventory',
                       'cash_on_hand','demand_deposits','cash_equivalents','third_party_trade_receivables',
                       'related_party_trade_receivables','term_deposits','restricted_term_deposits',
                       'eligible_term_deposits','restricted_cash','unrestricted_cash'}
        if item and name in note_concepts and item.verification_status not in {'HUMAN_VERIFIED','HUMAN_CORRECTED','VERIFIED'}:
            return None
        return item.value_vnd if item else None
    def metric(name,y=year):
        item=by_metric.get((name,y))
        return item.value if item else None
    def money(value):
        rounded=value.quantize(Decimal('1'),rounding=ROUND_HALF_EVEN)
        exact=f'{rounded:,.0f}'.replace(',','.')
        abbreviated=(value/Decimal(10)**9).quantize(Decimal('.01'),rounding=ROUND_HALF_EVEN)*Decimal(10)**9
        return ('≈ ' if abbreviated!=value else '')+_number(value,Decimal(10)**9)+' tỷ đồng ('+('xấp xỉ ' if rounded!=value else '')+exact+' đồng)'
    def percent(value): return _number(value)+'%'
    def add(key,title,text,inputs,roles,level='MATH_OBSERVATION'):
        sources=[datum(name,y) for name,y in inputs if datum(name,y) is not None]
        primary={'investor':{'growth','dupont','profitability'},'credit':{'liquidity','leverage'},
                 'tender':{'liquidity'},'cfo':{'cashflow','inventory','cash_cycle'}}
        bridge_id={'growth':'PROFIT','profitability':'PROFIT','cashflow':'CFO',
                   'inventory':'FUNDING','liquidity':'FUNDING','leverage':'FUNDING'}.get(key)
        matched_bridge=bridge.get((bridge_id,year)) if bridge_id else None
        bridge_drivers=[{'code':step.code,'label':step.label,'impact_vnd':str(step.signed_impact),
                         'verification_status':step.verification_status}
                        for step in (matched_bridge.steps if matched_bridge else []) if step.signed_impact is not None]
        observations=[{'concept':item.concept,'year':item.year,'value_vnd':str(item.value_vnd),
                       'source_line':item.source_line,'verification_status':item.verification_status}
                      for item in sources if item.value_vnd is not None]
        forms=sorted({s.source_line.split(':')[0] for s in sources if s.source_line and ':' in s.source_line})
        bridge_evidence=[step.evidences for step in (matched_bridge.steps if matched_bridge else [])
                         if step.signed_impact is not None]
        input_status=weakest(*(s.verification_status for s in sources),
                             *(step.verification_status for step in (matched_bridge.steps if matched_bridge else [])
                               if step.signed_impact is not None))
        findings.append({'id':key,'title':title,'narrative':text,'roles':roles,'level':level,'year':year,
                         'priority_by_role':{role:(3 if key in primary.get(role,set()) else 2) for role in roles},
                         'inputs':[f'{name}@{y}' for name,y in inputs],
                         'verification_status':input_status,
                         'evidences':[x.model_dump(mode='json') for x in union_evidence(*(s.evidences for s in sources),*bridge_evidence)],
                         'analysis_sections':{'headline':title,'comparison':observations,
                            'drivers':bridge_drivers,
                            'cross_statement':{'forms':forms,'status':'AVAILABLE' if len(forms)>1 else 'INSUFFICIENT_LINKED_INPUT'},
                            'evidence_status':input_status,
                            'caveat':'Cấu phần là phân rã số học; nguyên nhân kinh doanh cần đối chiếu thuyết minh và tài liệu liên quan.',
                            'next_check':'Xem các nguồn và thuyết minh có liên quan trước khi xác định nguyên nhân.'}})
    def insight(key,headline,meaning,follow_up):
        finding=next((item for item in reversed(findings) if item['id']==key),None)
        if finding:
            finding.update({'headline':headline,'meaning':meaning,'follow_up':follow_up})
            finding['analysis_sections']['headline']=headline
            finding['analysis_sections']['next_check']=follow_up
    revenue,ni=val('revenue'),val('net_income')
    if prior and all(v is not None for v in (revenue,ni,val('revenue',prior),val('net_income',prior))):
        r0,n0=val('revenue',prior),val('net_income',prior)
        direction=lambda x:'tăng' if x>0 else 'giảm' if x<0 else 'không đổi'
        def change(a,b):
            return f'{direction(a-b)} {percent(abs((a-b)/b*100))}' if b>0 and a>=0 else f'chuyển từ {money(b)} sang {money(a)}'
        def phrase(label,a,b):
            if b<=0 or a<0:return f'{label} {change(a,b)}'
            return f'{label} {change(a,b)} từ {money(b)} '+('lên ' if a>b else 'xuống ' if a<b else 'đến ')+money(a)
        text=phrase('Doanh thu',revenue,r0)+'; '+phrase('lợi nhuận sau thuế',ni,n0)+'.'
        inputs=[('revenue',prior),('revenue',year),('net_income',prior),('net_income',year)]
        cagr=by_metric.get(('G03',year))
        if cagr and cagr.value is not None and len(years)>=3:
            text+=f' Tăng trưởng doanh thu bình quân năm giai đoạn {years[0]}–{year}: {percent(cagr.value)}.'
            for entry in cagr.inputs:
                concept,_,period=entry.rpartition('@')
                if period.isdigit():inputs.append((concept,int(period)))
        p0,p1=metric('P01',prior),metric('P01')
        if p0 is not None and p1 is not None:
            connector='lên' if p1>p0 else 'xuống' if p1<p0 else 'đến'
            text+=f' Biên gộp thay đổi từ {percent(p0)} {connector} {percent(p1)} ({_number(p1-p0)} điểm %).'
            inputs.extend([('gross_profit',prior),('gross_profit',year)])
        nmargin0,nmargin1=metric('P04',prior),metric('P04')
        if nmargin0 is not None and nmargin1 is not None:
            text+=f' Biên ròng từ {percent(nmargin0)} đến {percent(nmargin1)}, thay đổi {_number(nmargin1-nmargin0)} điểm %.'
        b=bridge.get(('PROFIT',year)); steps={s.code:s for s in b.steps} if b else {}
        if all(steps.get(k) and steps[k].signed_impact is not None for k in ('revenue_effect','margin_effect')):
            text+=f' Theo cầu nối, tác động doanh thu là {money(steps["revenue_effect"].signed_impact)}, tác động biên là {money(steps["margin_effect"].signed_impact)}.'
        if all(steps.get(k) and steps[k].signed_impact is not None for k in ('selling','admin')):
            impact=steps['selling'].signed_impact+steps['admin'].signed_impact
            text+=f' Biến động chi phí bán hàng và quản lý tác động {money(impact)} đến lợi nhuận hoạt động lõi.'
            inputs.extend([('selling',prior),('selling',year),('admin',prior),('admin',year)])
        sg0,sg1=metric('X02_SGA_TO_REV',prior),metric('X02_SGA_TO_REV')
        if sg0 is not None and sg1 is not None:
            text+=f' Chi phí bán hàng và quản lý / doanh thu từ {percent(sg0)} đến {percent(sg1)}, thay đổi {_number(sg1-sg0)} điểm %.'
            inputs.extend([('selling',prior),('selling',year),('admin',prior),('admin',year)])
        add('growth','Tăng trưởng và sinh lời',text,inputs,['investor','cfo'],'FORMULA_DECOMPOSITION')
        if revenue<r0 and ni>n0:
            insight('growth','Doanh thu giảm nhưng lợi nhuận vẫn tăng',
                    'Biến động lợi nhuận đi ngược doanh thu; đối chiếu biên gộp, chi phí và các cấu phần còn lại trong cầu nối.',
                    'Kiểm tra cơ cấu doanh thu và thuyết minh trước khi xác định nguyên nhân kinh doanh.')
        elif revenue>r0 and ni>n0:
            insight('growth','Doanh thu và lợi nhuận cùng tăng',
                    'Đối chiếu tốc độ tăng và thay đổi biên để xác định phần cải thiện của lợi nhuận.',
                    'Kiểm tra cầu nối lợi nhuận và các khoản tài chính, thuế.')
        elif revenue<r0 and ni<n0:
            insight('growth','Doanh thu và lợi nhuận cùng giảm',
                    'Quy mô và lợi nhuận suy giảm đồng thời; cầu nối cho thấy đóng góp của biên và chi phí khi đủ nguồn.',
                    'Kiểm tra cơ cấu doanh thu, biên gộp và các yếu tố tài chính trước khi quy nguyên nhân.')
        elif revenue>r0 and ni<n0:
            insight('growth','Doanh thu tăng nhưng lợi nhuận giảm',
                    'Tăng quy mô chưa chuyển thành tăng lợi nhuận; đối chiếu biên gộp, chi phí và thuế.',
                    'Kiểm tra các bước trong cầu nối lợi nhuận và thuyết minh liên quan.')
        else:
            insight('growth','Doanh thu và lợi nhuận biến động khác nhau',
                    'Các thay đổi cần được đọc cùng dấu và quy mô của từng kỳ.',
                    'Kiểm tra dữ liệu từng kỳ và cầu nối lợi nhuận.')
    cfo=val('cfo');ratio=metric('C01')
    if cfo is not None and ni is not None:
        text=f'Dòng tiền kinh doanh đạt {money(cfo)} so với lợi nhuận sau thuế {money(ni)}'
        if ratio is not None:text+=f', tương đương {percent(ratio)}'
        text+='.'
        keys=[('cfo',year),('net_income',year)]
        if prior and metric('C01',prior) is not None:
            text+=f' Năm {prior}, CFO / lợi nhuận sau thuế là {percent(metric("C01",prior))}.'
            keys.extend([('cfo',prior),('net_income',prior)])
        if year-2 in years and metric('C01',year-2) is not None:
            text+=f' Năm {year-2}, tỷ lệ này là {percent(metric("C01",year-2))}.'
            keys.extend([('cfo',year-2),('net_income',year-2)])
        if prior and val('cfo',prior) is not None:
            text+=f' CFO từ {money(val("cfo",prior))} đến {money(cfo)}, chênh lệch với lợi nhuận sau thuế {money(cfo-ni)}.'
        if len(years)>1 and years==list(range(years[0],year+1)) and all(val(k,y) is not None for y in years for k in ('cfo','net_income')):
            total_cfo=sum((val('cfo',y) for y in years),Decimal(0));total_ni=sum((val('net_income',y) for y in years),Decimal(0))
            if total_ni>0:
                text+=f' Lũy kế {years[0]}–{year}, CFO {money(total_cfo)} bằng {percent(total_cfo/total_ni*100)} lợi nhuận sau thuế {money(total_ni)}.'
                keys.extend((k,y) for y in years for k in ('cfo','net_income'))
        for concept,label in (('pre_wc','trước thay đổi vốn lưu động'),('cf_inventory','biến động tồn kho'),('cf_receivables','biến động phải thu'),('cf_payables','biến động phải trả'),('tax_paid','thuế đã nộp'),('interest_paid','lãi vay đã trả')):
            value=val(concept)
            if value is not None:
                text+=f' {label.capitalize()}: {money(value)}.';keys.append((concept,year))
                if concept=='pre_wc' and prior and val(concept,prior) is not None:
                    text+=f' So với kỳ trước thay đổi {money(value-val(concept,prior))}.';keys.append((concept,prior))
        cfi=val('cfi');deposit_out=val('deposits_loans_out');deposit_in=val('deposits_loans_in')
        if cfi is not None:
            text+=f' Dòng tiền đầu tư là {money(cfi)}.';keys.append(('cfi',year))
        if deposit_out is not None and deposit_in is not None:
            text+=f' Dòng tiền gửi và cho vay ra/thu về ròng là {money(deposit_out+deposit_in)}.'
            keys.extend([('deposits_loans_out',year),('deposits_loans_in',year)])
        capex=val('capex')
        if capex is not None:text+=f' CFO sau chi mua sắm TSCĐ là {money(cfo-abs(capex))}.';keys.append(('capex',year))
        add('cashflow','Dòng tiền và lợi nhuận',text,keys,['investor','credit','cfo'],'FORMULA_DECOMPOSITION')
        if ni>0 and cfo<ni:
            insight('cashflow','Dòng tiền kinh doanh thấp hơn lợi nhuận',
                    'Chênh lệch cần đọc cùng biến động vốn lưu động và các khoản điều chỉnh trong B03.',
                    'Đối chiếu tồn kho, phải thu, phải trả và thuyết minh trước khi quy nguyên nhân.')
        elif ni>0 and cfo>=ni:
            insight('cashflow','Dòng tiền kinh doanh vượt lợi nhuận',
                    'CFO cao hơn lợi nhuận cùng kỳ; các điều chỉnh và biến động vốn lưu động góp phần tạo chênh lệch.',
                    'Xem các cấu phần B03 và mức duy trì dòng tiền qua các năm.')
        else:
            insight('cashflow','Dòng tiền kinh doanh và kết quả lợi nhuận',
                    'Khi lợi nhuận không dương, không dùng tỷ lệ CFO / lợi nhuận để kết luận chất lượng chuyển đổi.',
                    'Đọc riêng dấu, giá trị và các cấu phần B03 của từng năm.')
    if prior:
        ids=('P06','P05','P04','E06','DUPONT_EQUITY_MULTIPLIER')
        if all(metric(mid,y) is not None for mid in ids for y in (prior,year)):
            roe0,roe1=metric('P06',prior),metric('P06')
            roa0,roa1=metric('P05',prior),metric('P05')
            p0,p1=metric('P04',prior),metric('P04')
            t0,t1=metric('E06',prior),metric('E06')
            e0,e1=metric('DUPONT_EQUITY_MULTIPLIER',prior),metric('DUPONT_EQUITY_MULTIPLIER')
            text=(f'ROE từ {percent(roe0)} đến {percent(roe1)} ({_number(roe1-roe0)} điểm %); '
                  f'ROA từ {percent(roa0)} đến {percent(roa1)}. Biên ròng từ {percent(p0)} đến {percent(p1)}, '
                  f'vòng quay tài sản từ {_number(t0)} đến {_number(t1)} lần, '
                  f'hệ số nhân vốn chủ từ {_number(e0)} đến {_number(e1)} lần.')
            # Exact sequential decomposition, explicitly identifying order.
            margin0,margin1=p0/100,p1/100
            effects=[('biên ròng',(margin1-margin0)*t0*e0*100),
                     ('vòng quay tài sản',margin1*(t1-t0)*e0*100),
                     ('hệ số nhân vốn chủ',margin1*t1*(e1-e0)*100)]
            text+=' Phân rã tuần tự (biên → vòng quay → đòn bẩy): '+ '; '.join(f'{label} { _number(effect)} điểm %' for label,effect in effects)+'.'
            sources=[]
            for y in (prior,year):
                for concept in ('revenue','net_income','assets','equity'):
                    sources.append((concept,y))
                sources.extend([('assets',y-1),('equity',y-1)])
            add('dupont','Hiệu suất vốn và DuPont',text,sources,['investor'],'FORMULA_DECOMPOSITION')
            largest=max(effects,key=lambda entry:abs(entry[1]))
            insight('dupont',f'ROE {"tăng" if roe1>roe0 else "giảm" if roe1<roe0 else "không đổi"} so với kỳ trước',
                    f'Theo phân rã tuần tự đã chọn, {largest[0]} có tác động tuyệt đối lớn nhất ({_number(largest[1])} điểm %); các nhân tố còn lại có thể bù trừ.',
                    'Đối chiếu biên ròng, vòng quay và vốn chủ bình quân; đóng góp phụ thuộc thứ tự phân rã.')
    def metrics_observation(key,title,items,roles):
        parts=[];inputs=[]
        for mid,label in items:
            m=by_metric.get((mid,year))
            if not m or m.value is None:continue
            unit='%' if m.unit=='%' else ' '+m.unit
            formatted=lambda value: money(value) if m.unit=='VND' else _number(value)+unit
            value=formatted(m.value)
            old=by_metric.get((mid,prior)) if prior else None
            if old and old.value is not None:
                value=formatted(old.value)+' → '+value
                for item in old.inputs:
                    concept,_,period=item.rpartition('@')
                    if period.isdigit():inputs.append((concept,int(period)))
            parts.append(label+': '+value+(' (chỉ báo thay thế)' if m.status.value=='PROXY' else ''))
            for item in m.inputs:
                concept,_,period=item.rpartition('@')
                if period.isdigit():inputs.append((concept,int(period)))
        if parts:add(key,title,'; '.join(parts)+'.',inputs,roles)
    metrics_observation('profitability','Biên lợi nhuận và chi phí',
        [('P01','Biên gộp'),('P02','Biên hoạt động lõi'),('P04','Biên ròng'),('X02_SGA_TO_REV','Chi phí bán hàng và quản lý / doanh thu')],['investor','cfo'])
    metrics_observation('leverage','Cấu trúc nợ và bao phủ lãi',
        [('D01','Nợ phải trả / tài sản'),('D03','Nợ vay / vốn chủ'),('D04','Nợ vay ròng'),('D06','Bao phủ lãi vay')],['credit'])
    metrics_observation('cash_cycle','Chu kỳ vốn lưu động',
        [('E02','Số ngày tồn kho'),('E03','Số ngày phải thu'),('E04','Số ngày phải trả'),('E05','Chu kỳ chuyển đổi tiền')],['cfo'])
    if metric('E02') is not None and prior and metric('E02',prior) is not None:
        delta=metric('E02')-metric('E02',prior)
        insight('cash_cycle',f'Số ngày tồn kho {"tăng" if delta>0 else "giảm" if delta<0 else "không đổi"} so với kỳ trước',
                f'Chỉ tiêu thay đổi {_number(delta)} ngày; đọc cùng số ngày phải thu, phải trả và cấu phần B03.',
                'Kiểm tra cơ cấu tồn kho, mùa vụ và các giả định dùng doanh thu hoặc giá vốn thay cho giao dịch tín dụng.')
    elif any(metric(mid) is not None for mid in ('E02','E03','E04','E05')):
        available=[(mid,label) for mid,label in [('E02','tồn kho'),('E03','phải thu'),('E04','phải trả'),('E05','chuyển đổi tiền')] if metric(mid) is not None]
        insight('cash_cycle','Theo dõi tốc độ luân chuyển vốn',
                'Đã tính được '+', '.join(f'{label} {_number(metric(mid))} ngày' for mid,label in available)+'.',
                'So sánh cùng định nghĩa qua nhiều kỳ; xem thuyết minh về mùa vụ và thời hạn thanh toán.')
    if metric('P01') is not None and prior and metric('P01',prior) is not None:
        delta=metric('P01')-metric('P01',prior)
        insight('profitability',f'Biên gộp {"mở rộng" if delta>0 else "thu hẹp" if delta<0 else "không đổi"}',
                f'Biên gộp thay đổi {_number(delta)} điểm %, cần đặt cạnh biên hoạt động lõi và chi phí bán hàng, quản lý.',
                'Xem cầu nối lợi nhuận và cơ cấu doanh thu trước khi quy nguyên nhân.')
    elif any(metric(mid) is not None for mid in ('P01','P02','P04','X02_SGA_TO_REV')):
        insight('profitability','Cơ cấu biên lợi nhuận và chi phí',
                'Đọc biên gộp, biên hoạt động và biên ròng cùng tỷ lệ chi phí để xác định các tầng tạo lợi nhuận.',
                'Đối chiếu cùng kỳ trước và cơ cấu doanh thu khi có dữ liệu so sánh.')
    if metric('D03') is not None:
        m=by_metric[('D03',year)]
        insight('leverage','Đòn bẩy và khả năng bao phủ cần đọc cùng nhau',
                f'Nợ vay / vốn chủ là {percent(m.value)}'+(' theo chỉ báo thay thế.' if m.status.value=='PROXY' else '.'),
                'Đối chiếu kỳ hạn nợ, CFO và các điều kiện vay trước khi đánh giá khả năng trả nợ.')
    elif metric('D01') is not None:
        insight('leverage','Nợ phải trả trong cơ cấu nguồn vốn',
                f'Nợ phải trả tương đương {percent(metric("D01"))} tài sản; chỉ tiêu này gồm cả khoản không chịu lãi.',
                'Tách nợ vay, nợ thương mại và kỳ hạn trả nợ trước khi đánh giá áp lực tài chính.')
    inv,assets=val('inventory'),val('assets')
    if inv is not None and assets is not None and assets>0:
        text=f'Tồn kho cuối {year} là {money(inv)}, chiếm {percent(inv/assets*100)} tổng tài sản.';keys=[('inventory',year),('assets',year)]
        if prior and val('inventory',prior) is not None:
            old=val('inventory',prior);text+=f' So với {prior}, tồn kho thay đổi {money(inv-old)}.';keys.append(('inventory',prior))
            if old>0 and inv>=0:text+=f' Tốc độ thay đổi là {percent((inv-old)/old*100)}.'
            old_assets=val('assets',prior)
            if old_assets is not None and old_assets>0:
                text+=f' Tỷ trọng tồn kho / tài sản từ {percent(old/old_assets*100)} đến {percent(inv/assets*100)}.';keys.append(('assets',prior))
            cf_inventory=val('cf_inventory')
            if cf_inventory is not None:
                text+=f' B03:10 ghi nhận {money(cf_inventory)}; chênh giữa Δ tồn kho và số đảo dấu B03:10 là {money(inv-old+cf_inventory)}.'
                keys.append(('cf_inventory',year))
        d0,d1=(metric('E02',prior),metric('E02')) if prior else (None,None)
        if d0 is not None and d1 is not None:
            text+=f' Số ngày tồn kho từ {_number(d0)} đến {_number(d1)} ngày, chênh {_number(d1-d0)} ngày.'
            for midyear in (prior,year):
                for entry in by_metric[('E02',midyear)].inputs:
                    concept,_,period=entry.rpartition('@')
                    if period.isdigit():keys.append((concept,int(period)))
        elif d1 is not None:
            text+=f' Số ngày tồn kho năm {year} là {_number(d1)} ngày.'
            for entry in by_metric[('E02',year)].inputs:
                concept,_,period=entry.rpartition('@')
                if period.isdigit():keys.append((concept,int(period)))
        for key,label in (('raw_materials','nguyên vật liệu'),('finished_goods','thành phẩm'),('merchandise','hàng hóa'),
                          ('work_in_progress','chi phí sản xuất kinh doanh dở dang'),('inventory_in_transit','hàng đang đi trên đường'),
                          ('real_estate_inventory','hàng hóa bất động sản'),('inventory_tools','công cụ dụng cụ'),
                          ('consigned_inventory','hàng gửi đi bán')):
            v=val(key)
            if v is not None:
                before=val(key,prior) if prior else None
                text+=f' {label.capitalize()}: '+(f'{money(before)} → ' if before is not None else '')+f'{money(v)}.'
                if before is not None:text+=f' Thay đổi {money(v-before)}.'
                keys.append((key,year))
                if before is not None:keys.append((key,prior))
        add('inventory','Vốn lưu động và tồn kho',text,keys,['investor','credit','cfo'])
        if prior and val('inventory',prior) is not None:
            old=val('inventory',prior)
            if inv>old:
                insight('inventory','Quy mô tồn kho tăng',
                        'Vốn gắn với tồn kho lớn hơn cuối kỳ trước; đối chiếu tốc độ bán và cơ cấu hàng.',
                        'Xem vòng quay, dự phòng và thuyết minh cơ cấu tồn kho.')
            elif inv<old:
                insight('inventory','Quy mô tồn kho giảm',
                        'Số dư tồn kho giảm; dòng tiền liên quan trên B03 có thể khác mức thay đổi số dư.',
                        'Đối chiếu B03:10, dự phòng và các biến động phi tiền mặt.')
        if not next((f for f in reversed(findings) if f['id']=='inventory'),{}).get('headline'):
            insight('inventory','Tồn kho trong cơ cấu tài sản',
                    f'Tồn kho cuối kỳ chiếm {percent(inv/assets*100)} tổng tài sản; xem thêm thành phần và vòng quay.',
                    'Đối chiếu năm trước, dự phòng và thuyết minh theo từng loại hàng.')
    ca,cl=val('current_assets'),val('current_liabilities')
    if ca is not None and cl is not None:
        text=f'Vốn lưu động ròng là {money(ca-cl)}.'; keys=[('current_assets',year),('current_liabilities',year)]
        for mid,label in (('L01','Thanh toán hiện hành'),('L02','thanh toán nhanh'),('L05','vốn lưu động hoạt động ròng'),('X13_NOWC_TO_REV','vốn lưu động hoạt động / doanh thu'),('X09_NWC_TO_INVENTORY','vốn lưu động ròng / tồn kho'),('D03','nợ vay / vốn chủ'),('D04','nợ vay ròng'),('D06','bao phủ lãi vay')):
            m=by_metric.get((mid,year))
            if m and m.value is not None:
                formatted=money(m.value) if m.unit=='VND' else f'{_number(m.value)} {m.unit}'
                old=by_metric.get((mid,prior)) if prior else None
                if old and old.value is not None:
                    formatted=(money(old.value) if m.unit=='VND' else f'{_number(old.value)} {m.unit}')+' → '+formatted
                    keys.extend((x.rsplit('@',1)[0],int(x.rsplit('@',1)[1])) for x in old.inputs if '@' in x)
                text+=f' {label.capitalize()}: {formatted}'+(' (chỉ báo thay thế)' if m.status.value=='PROXY' else '')+'.';keys.extend((x.rsplit('@',1)[0],int(x.rsplit('@',1)[1])) for x in m.inputs if '@' in x)
        if metric('X09_NWC_TO_INVENTORY') is not None:text+=' Tỷ lệ này so sánh quy mô vốn lưu động ròng với tồn kho, không xác định trực tiếp nguồn tài trợ riêng cho kho.'
        for concept,label in (('cash','tiền và tương đương tiền'),('borrowings','vay ngắn hạn'),('term_deposits','tiền gửi có kỳ hạn'),('short_held_to_maturity','đầu tư nắm giữ đến ngày đáo hạn ngắn hạn'),('restricted_term_deposits','tiền gửi có kỳ hạn bị hạn chế')):
            current=val(concept)
            if current is not None:
                previous=val(concept,prior) if prior else None
                text+=f' {label.capitalize()}: '+(f'{money(previous)} → ' if previous is not None else '')+f'{money(current)}.'
                keys.append((concept,year))
                if previous is not None:keys.append((concept,prior))
        for concept,label in (('cash_on_hand','tiền mặt'),('demand_deposits','tiền gửi không kỳ hạn'),
                              ('cash_equivalents','các khoản tương đương tiền'),
                              ('third_party_trade_receivables','phải thu khách hàng bên thứ ba'),
                              ('related_party_trade_receivables','phải thu khách hàng bên liên quan')):
            current=val(concept)
            if current is not None:
                previous=val(concept,prior) if prior else None
                text+=f' {label.capitalize()}: '+(f'{money(previous)} → ' if previous is not None else '')+f'{money(current)}.'
                keys.append((concept,year))
                if previous is not None:keys.append((concept,prior))
        interest=val('interest_expense')
        if interest is not None:
            previous=val('interest_expense',prior) if prior else None
            text+=' Chi phí lãi vay: '+(f'{money(abs(previous))} → ' if previous is not None else '')+f'{money(abs(interest))}.'
            keys.append(('interest_expense',year))
            if previous is not None:keys.append(('interest_expense',prior))
        if metric('D04') is not None and by_metric[('D04',year)].status.value=='PROXY':
            text+=' Nợ vay ròng là chỉ báo thay thế; cần xác nhận phạm vi tiền đủ điều kiện khấu trừ và nợ vay dài hạn.'
        add('liquidity','Thanh khoản và nợ vay',text,keys,['credit','tender','cfo'])
        if cl>0 and prior and metric('L01',prior) is not None and metric('L02',prior) is not None and metric('L01') is not None and metric('L02') is not None:
            if metric('L01')>metric('L02'):
                insight('liquidity','Thanh khoản phụ thuộc vào thành phần tài sản ngắn hạn',
                        'Hệ số hiện hành cao hơn hệ số nhanh; chênh lệch phụ thuộc định nghĩa từng chỉ số và cơ cấu tài sản.',
                        'Xem kỳ hạn tiền gửi, hạn chế sử dụng và khả năng chuyển đổi tồn kho thành tiền.')
        if not next((f for f in reversed(findings) if f['id']=='liquidity'),{}).get('headline'):
            insight('liquidity','Cơ cấu tài sản và nghĩa vụ ngắn hạn',
                    f'Vốn lưu động ròng là {money(ca-cl)}; cần xem thêm chất lượng tài sản và thời điểm các khoản nợ đến hạn.',
                    'Đối chiếu tiền khả dụng, phải thu, tồn kho và lịch thanh toán nợ.')
    return findings
