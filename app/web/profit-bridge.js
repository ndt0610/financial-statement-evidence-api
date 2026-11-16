/* Profit bridge presentation. Values and residuals remain sourced from the API. */
const bridgeBaseWaterfall = p11Waterfall;
const bridgeBaseRender = renderBridge;
const bridgeAbs = n => n < 0n ? -n : n;
const bridgeCompact = n => (n > 0n ? '+' : n < 0n ? '−' : '') + formatDecimalHalfEven(String(bridgeAbs(n)), 0).replace(/\./g,'');
function bridgeBillion(n, signed=true) {
  const prefix = n < 0n ? '−' : signed && n > 0n ? '+' : '';
  return prefix + formatDecimalHalfEven(String(bridgeAbs(n)) + 'e-9', 2);
}
function bridgeExact(n) { return formatExactMoney(String(n), 'vnd'); }
function bridgeValue(n) { return bridgeBillion(n) + ' tỷ đồng'; }
function bridgeLabelLines(label) {
  const words=label.split(' '), lines=[''];
  for (const word of words) { const i=lines.length-1; if ((lines[i]+' '+word).trim().length>20 && lines.length<3) lines.push(word); else lines[i]=(lines[i]+' '+word).trim(); }
  return lines;
}
function renderProfitVisuals() {
 const year=Number($('bridge-year').value), b=bridge('PROFIT',year), out=$('p11-waterfall');
 if (!b || b.target_delta == null) return bridgeBaseWaterfall();
 const a=metric('RAW_NET_INCOME',year-1), z=metric('RAW_NET_INCOME',year);
 if(!valid(a)||!valid(z)) return bridgeBaseWaterfall();
 try {
  const start=BigInt(a.value), end=BigInt(z.value);
  const steps=b.steps.filter(s=>s.signed_impact!==null && s.signed_impact!==undefined).map(s=>({...s, amount:BigInt(s.signed_impact), name:CONCEPTS[s.code]||s.label}));
  const missing=b.steps.length-steps.length; let running=start;
  const items=[{name:`LNST ${year-1}`,from:0n,to:start,amount:start,total:true}];
  for(const s of steps){const next=running+s.amount;items.push({name:s.name,from:running,to:next,amount:s.amount,code:s.code});running=next;}
  const residual=end-running;
  if(residual!==0n)items.push({name:'Chưa phân rã / phần dư',from:running,to:end,amount:residual,residual:true});
  items.push({name:`LNST ${year}`,from:0n,to:end,amount:end,total:true});
  const vals=items.flatMap(i=>[Number(i.from),Number(i.to)]),low=Math.min(0,...vals),high=Math.max(0,...vals),pad=(high-low||1)*.12,min=low-pad,max=high+pad;
  const W=Math.max(1100,items.length*130), H=390, left=28, right=25, top=35, bottom=245, slot=(W-left-right)/items.length, y=v=>bottom-(Number(v)-min)/(max-min)*(bottom-top), zero=y(0);
  let svg=`<line class="bridge-zero" x1="${left}" x2="${W-right}" y1="${zero}" y2="${zero}"/>`;
  items.forEach((item,i)=>{const x=left+slot*i+slot/2, y1=y(item.from),y2=y(item.to),height=Math.max(Math.abs(y1-y2),2), barTop=Math.min(y1,y2), bw=Math.min(54,slot*.5), cls=item.total?'total':item.residual?'residual':item.amount>=0n?'positive':'negative';
   if(i<items.length-1)svg+=`<line class="bridge-connector" x1="${x+bw/2}" y1="${y2}" x2="${x+slot-bw/2}" y2="${y2}"/>`;
   const exact=bridgeExact(item.amount), compact=bridgeBillion(item.amount,!item.total);
   svg+=`<g tabindex="0" role="img" aria-label="${escapeHtml(item.name+': '+exact)}"><title>${escapeHtml(item.name+': '+exact)}</title><rect class="bridge-bar ${cls}" x="${x-bw/2}" y="${barTop}" width="${bw}" height="${height}" rx="4"/><text class="bridge-value ${cls}" x="${x}" y="${Math.max(15,barTop-7)}" text-anchor="middle">${escapeHtml(compact)}</text><text class="bridge-label" x="${x}" y="${bottom+30}" text-anchor="middle">${bridgeLabelLines(item.name).map((line,k)=>`<tspan x="${x}" dy="${k?14:0}">${escapeHtml(line)}</tspan>`).join('')}</text></g>`;
  });
  const pos=steps.filter(s=>s.amount>0n).sort((x,y)=>x.amount>y.amount?-1:1)[0],neg=steps.filter(s=>s.amount<0n).sort((x,y)=>x.amount<y.amount?-1:1)[0];
  const delta=end-start;
  const summary=`LNST ${year} ${delta>=0n?'tăng':'giảm'} ${bridgeValue(bridgeAbs(delta))} so với ${year-1}. ${pos?'Tác động tăng lớn nhất: '+pos.name+' ('+bridgeValue(pos.amount)+'). ':''}${neg?'Tác động giảm lớn nhất: '+neg.name+' ('+bridgeValue(neg.amount)+').':''}`;
  out.innerHTML=`<div class="chart-header"><div><span class="chart-kicker">PHÂN RÃ BIẾN ĐỘNG · ${year-1} → ${year}</span><h3>Cầu nối lợi nhuận sau thuế</h3></div><span class="bridge-unit">Tỷ đồng · nhãn làm tròn 2 chữ số</span></div><p class="bridge-insight">${escapeHtml(summary)}</p><div class="bridge-key"><span><i class="key-positive"></i>Tác động tăng</span><span><i class="key-negative"></i>Tác động giảm</span><span><i class="key-total"></i>LNST đầu / cuối kỳ</span></div><div class="bridge-chart-scroll"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Cầu nối lợi nhuận sau thuế ${year-1} đến ${year}">${svg}</svg></div><div class="bridge-stages"><span>01 · Quy mô doanh thu, biên gộp & chi phí hoạt động</span><span>02 · Tài chính, khoản khác & thuế</span></div><p class="chart-footnote">Số trên hình được làm tròn; rê chuột hoặc chọn từng cột để xem đến đồng. Tác động doanh thu và biên là phép phân rã theo công thức.${missing?' Còn '+missing+' cấu phần chưa đủ dữ liệu.':''}${residual!==0n?' Phần chưa phân rã / phần dư: '+bridgeExact(residual)+'.':''}</p>`;
  const rows=[...steps].sort((x,y)=>bridgeAbs(x.amount)>bridgeAbs(y.amount)?-1:bridgeAbs(x.amount)<bridgeAbs(y.amount)?1:0), largest=rows.reduce((v,s)=>bridgeAbs(s.amount)>v?bridgeAbs(s.amount):v,0n);
  const old=$('bridge-detail').querySelector('.p10-bars'); if(old)old.remove();
  const body=rows.map(s=>{const pct=largest===0n?0:Number(bridgeAbs(s.amount)*10000n/largest)/100;return `<div class="impact-row"><span class="impact-label">${escapeHtml(s.name)}</span><div class="impact-plot" aria-hidden="true"><span class="impact-zero"></span><i class="impact-bar ${s.amount<0n?'negative':'positive'}" style="${s.amount<0n?'right':'left'}:50%;width:${pct/2}%"></i></div><strong class="impact-amount ${s.amount<0n?'negative':'positive'}" title="${escapeHtml(bridgeExact(s.amount))}">${escapeHtml(bridgeBillion(s.amount))}</strong></div>`}).join('');
  $('bridge-detail').insertAdjacentHTML('beforeend',`<section class="impact-panel"><div class="chart-header"><div><span class="chart-kicker">XẾP HẠNG THEO MỨC TÁC ĐỘNG</span><h3>Tác động của từng yếu tố · ${year}</h3></div><span class="bridge-unit">Tỷ đồng · làm tròn 2 chữ số</span></div><div class="impact-axis"><span>Giảm lợi nhuận</span><span>0</span><span>Tăng lợi nhuận</span></div>${body}<p class="chart-footnote">Thanh càng dài, mức tác động tuyệt đối càng lớn. Di chuột vào số để xem giá trị đến đồng; bảng phía trên giữ công thức và chứng cứ.</p></section>`);
 } catch(error) { bridgeBaseWaterfall(); }
}
p11Waterfall=function(){if(state.bridge==='PROFIT')renderProfitVisuals();else bridgeBaseWaterfall();};
renderBridge=function(){bridgeBaseRender();};
