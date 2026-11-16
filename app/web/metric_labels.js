// Financial labels are shared by the analysis and audit views.
const METRIC_GROUPS = {
 G:['Thay đổi doanh thu thuần','Tăng trưởng doanh thu thuần','Tăng trưởng doanh thu bình quân năm','Tồn kho / Tổng tài sản','Nợ phải trả / Tổng nguồn vốn','Chi phí bán hàng / Doanh thu','Biến động tồn kho / Biến động tài sản'],
 L:['Thanh toán hiện hành','Thanh toán nhanh theo tiền và phải thu','Thanh toán bằng tiền','Vốn lưu động ròng','Vốn lưu động hoạt động ròng','CFO / Nợ ngắn hạn bình quân','Số ngày phòng thủ'],
 D:['Nợ phải trả / Tổng tài sản','Nợ phải trả / Vốn chủ sở hữu','Nợ vay / Vốn chủ sở hữu','Nợ vay ròng','Nợ vay ngắn hạn / Tổng nợ vay','Khả năng trả lãi theo EBIT','Nợ vay ròng / EBITDA','CFO / Nợ vay bình quân','Khả năng trả nợ theo hợp đồng'],
 E:['Vòng quay hàng tồn kho','Số ngày tồn kho','Số ngày phải thu','Số ngày phải trả','Chu kỳ chuyển đổi tiền','Vòng quay tổng tài sản','Vòng quay tài sản cố định'],
 P:['Biên lợi nhuận gộp','Biên lợi nhuận hoạt động cốt lõi','Biên lợi nhuận trước thuế','Biên lợi nhuận ròng','ROA','ROE hợp nhất','ROE cổ đông công ty mẹ','ROIC','ROCE'],
 C:['CFO / Lợi nhuận sau thuế','CFO / Doanh thu','Chênh lệch CFO và lợi nhuận','Dồn tích / Tài sản bình quân','CFO sau chi đầu tư tài sản','CFO / Chi đầu tư tài sản','CFO / Lợi nhuận lũy kế','Dòng tiền sau đầu tư / Cổ tức tiền'],
 Q:['Dự phòng phải thu / Phải thu gộp','Phải thu quá hạn / Phải thu gộp','Dự phòng tồn kho / Tồn kho gộp','Phải thu bên liên quan / Phải thu','Tài sản bảo đảm / Tổng tài sản']
};
const METRIC_NAMES=Object.fromEntries(Object.entries(METRIC_GROUPS).flatMap(([group,names])=>names.map((name,i)=>[group+String(i+1).padStart(2,'0'),name])));
Object.assign(METRIC_NAMES,{DUPONT_NET_MARGIN:'Biên lợi nhuận ròng DuPont',DUPONT_ASSET_TURNOVER:'Vòng quay tài sản DuPont',DUPONT_EQUITY_MULTIPLIER:'Hệ số nhân vốn chủ DuPont',DUPONT_ROE:'ROE đối chiếu DuPont',DIO_GROSS:'Số ngày tồn kho gộp',ETR:'Thuế suất hiệu dụng',ID_A:'Nợ vay / tổng tài sản',NET_DEBT_ALL_TERM_SCENARIO:'Kịch bản nợ vay ròng gồm tiền gửi',FCCR_ACCRUAL:'Khả năng trang trải định phí'});
const METRIC_STATUS={COMPUTED:'Đã tính',PROXY:'Chỉ báo thay thế',MISSING_INPUT:'Thiếu dữ liệu',NOT_APPLICABLE:'Không áp dụng',HUMAN_VERIFIED:'Đã xác nhận nguồn',HUMAN_CORRECTED:'Đã đính chính',EXTRACTED:'Mới trích xuất (chờ xác nhận)',VERIFIED:'Đã xác minh'};
function formatMetricValue(m){if(m.value===null||m.value===undefined)return 'Thiếu dữ liệu';try{if(m.unit==='VND'||m.unit==='đồng')return formatCompactMoney(m.value,'billion');return formatDecimalHalfEven(m.value,2)+(m.unit==='%'?'%':m.unit==='lần'?' lần':m.unit==='ngày'?' ngày':'')}catch{return 'Thiếu dữ liệu'}}
