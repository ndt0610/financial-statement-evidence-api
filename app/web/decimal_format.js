/* String/BigInt decimal formatting: never pass monetary amounts through Number. */
function parseDecimalParts(value){
 const text=String(value).trim(),m=/^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text);
 if(!m)throw new Error('Giá trị thập phân không hợp lệ');
 const exponent=Number(m[4]||0);if(!Number.isSafeInteger(exponent)||Math.abs(exponent)>1000)throw new Error('Số mũ vượt giới hạn');
 let digits=(m[2]+(m[3]||'')).replace(/^0+(?=\d)/,''),scale=(m[3]||'').length-exponent;
 if(scale<0){digits+='0'.repeat(-scale);scale=0;}
 return {negative:m[1]==='-',coefficient:BigInt(digits),scale};
}
function quantizeHalfEven(value,places){
 const p=parseDecimalParts(value);let magnitude=p.coefficient,inexact=false;
 if(p.scale>places){const divisor=10n**BigInt(p.scale-places),remainder=magnitude%divisor;let quotient=magnitude/divisor;
  inexact=remainder!==0n;if(remainder*2n>divisor||(remainder*2n===divisor&&quotient%2n===1n))quotient+=1n;magnitude=quotient;
 }else magnitude*=10n**BigInt(places-p.scale);
 return {scaled:p.negative?-magnitude:magnitude,inexact};
}
function formatScaledDecimal(integer,places){
 const negative=integer<0n,raw=(negative?-integer:integer).toString().padStart(places+1,'0');
 const whole=places?raw.slice(0,-places):raw,frac=places?','+raw.slice(-places):'';
 return (negative?'-':'')+whole.replace(/\B(?=(\d{3})+(?!\d))/g,'.')+frac;
}
function formatExactMoney(value,unit='billion'){
 if(value===null||value===undefined)return '—';
 const q=quantizeHalfEven(value,0),digits=unit==='million'?6:unit==='vnd'?0:9;
 return (q.inexact?'≈ ':'')+formatScaledDecimal(q.scaled,digits)+(unit==='vnd'?' đ':unit==='million'?' triệu đồng':' tỷ đồng');
}
function formatDecimalHalfEven(value,places=2){return formatScaledDecimal(quantizeHalfEven(value,places).scaled,places);}
function formatCompactMoney(value,unit='billion'){
 if(unit==='vnd')return formatExactMoney(value,unit);
 const p=parseDecimalParts(value), places=unit==='million'?6:9;
 const scaled=plainScaledDecimal(p.negative?-p.coefficient:p.coefficient,p.scale+places);
 return formatDecimalHalfEven(scaled,2)+(unit==='million'?' triệu đồng':' tỷ đồng');
}
function metricDisplayUnit(samples){return (samples.find(m=>m&&m.value!==null&&m.value!==undefined)||samples.find(Boolean))?.unit;}
function plainScaledDecimal(integer,places){
 const negative=integer<0n,raw=(negative?-integer:integer).toString().padStart(places+1,'0');
 return (negative?'-':'')+(places?raw.slice(0,-places)+'.'+raw.slice(-places):raw);
}
function subtractDecimal(a,b){
 const x=parseDecimalParts(a),y=parseDecimalParts(b),scale=Math.max(x.scale,y.scale);
 const signed=p=>(p.negative?-p.coefficient:p.coefficient)*10n**BigInt(scale-p.scale);
 return plainScaledDecimal(signed(x)-signed(y),scale);
}
function multiplyDecimalPower10(value,exponent){
 const p=parseDecimalParts(value),scale=Math.max(0,p.scale-exponent);
 const coefficient=p.coefficient*10n**BigInt(Math.max(0,exponent-p.scale));
 return plainScaledDecimal(p.negative?-coefficient:coefficient,scale);
}
