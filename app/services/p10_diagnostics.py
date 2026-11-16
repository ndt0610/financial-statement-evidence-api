"""Structured, evidence-bearing observations for the four P10 roles."""
from app.models.full_analysis import MetricStatus
from app.services.analysis_book import union_evidence, weakest

ROLES = {
 'investor': [('Tăng trưởng & lợi nhuận',['RAW_REVENUE','G02','RAW_GROSS_PROFIT','RAW_NET_INCOME'], 'So sánh tốc độ doanh thu với lợi nhuận để tìm thay đổi về biên.'),
              ('Biên lợi nhuận & thuế',['P01','P02','P04','X04_EFFECTIVE_TAX_RATE'], 'Biên phản ánh phần kết quả còn lại trên doanh thu, cần đối chiếu cầu nối.'),
              ('Hiệu suất vốn & DuPont',['P06','P05','E06','X07_DUPONT_EBIT_MARGIN'], 'Tỷ suất sinh lời phụ thuộc biên, hiệu suất tài sản và cơ cấu vốn.'),
              ('Chất lượng lợi nhuận & cổ tức',['C01','C07','C03','C04'], 'Dòng tiền và lợi nhuận khác nhau về thời điểm ghi nhận và vốn lưu động.')],
 'credit': [('Cấu trúc nợ',['D03','D01','D04','X10_BORROWING_INFLOW_TO_ST_DEBT'], 'Cần phân biệt số dư nợ với dòng vay và dòng trả trong kỳ.'),
            ('Bao phủ lãi và trả nợ',['D06','D08','D09'], 'Lợi nhuận bao phủ lãi và dòng tiền trả gốc trả lời các câu hỏi khác nhau.'),
            ('Các lớp thanh khoản',['L03','L02','L01','L06'], 'Các tỷ số có phạm vi tài sản thanh khoản khác nhau.'),
            ('Cân đối vốn và chất lượng tài sản',['L04','X09_NWC_TO_INVENTORY','Q01','Q05'], 'Vốn lưu động ròng không xác định nguồn tiền tài trợ riêng cho từng tài sản.')],
 'tender': [('Giá trị tài sản và kết quả',['X12_NET_WORTH','RAW_NET_INCOME','P03'], 'Đối chiếu chỉ tiêu với đúng ngưỡng của hồ sơ đang xét.'),
            ('Doanh thu qua các kỳ',['RAW_REVENUE','G02','G03'], 'Doanh thu bình quân cần đúng số năm và phạm vi hồ sơ quy định.'),
            ('Nguồn lực tài chính',['L04','L01','RAW_CFO'], 'Nguồn lực được chấp nhận phụ thuộc điều kiện của hồ sơ.'),
            ('Đối chiếu dữ liệu',['D01','D02','C01'], 'Đối chiếu kỳ, phạm vi và phiên bản trước khi đánh giá.')],
 'cfo': [('Chu kỳ tiền và vốn lưu động',['E02','E03','E04','E05','L05'], 'Thời gian luân chuyển là chỉ báo cần so với cơ cấu hoạt động.'),
         ('Tồn kho và phải thu',['RAW_INVENTORY','G04','Q03','RAW_CF_INVENTORY'], 'Biến động số dư và dòng tiền có thể khác nhau.'),
         ('Kiểm soát chi phí',['X02_SGA_TO_REV','G06','X01_ADMIN_TO_REV','X04_EFFECTIVE_TAX_RATE'], 'So sánh chi phí với doanh thu để tìm thay đổi cơ cấu.'),
         ('Phân bổ vốn và hiệu suất tài sản',['E06','E07','X14_CAPEX_TO_REV','C05'], 'Cần đối chiếu dòng tiền và đầu tư với năng lực vận hành.')],
}


def diagnostics(metrics, years):
    if not years:
        return {}
    lookup={(m.metric_id,m.fiscal_year):m for m in metrics}
    year=years[-1]
    result={}
    for role, tiers in ROLES.items():
        result[role]=[]
        for title, ids, meaning in tiers:
            current=[lookup.get((mid,year)) for mid in ids]
            available=[m for m in current if m and m.value is not None and m.status in {MetricStatus.COMPUTED,MetricStatus.PROXY}]
            focus=available[0] if available else None
            prior=lookup.get((focus.metric_id,years[-2])) if focus and len(years)>1 else None
            if focus and prior and prior.value is not None and prior.unit==focus.unit:
                relation='tăng' if focus.value>prior.value else 'giảm' if focus.value<prior.value else 'không đổi'
                observation=f'{focus.metric_id} {relation} từ {prior.value} đến {focus.value} {focus.unit} ({prior.fiscal_year}–{year}).'
                sources=[prior,focus]
            elif focus:
                observation=f'{focus.metric_id} năm {year}: {focus.value} {focus.unit}.'
                sources=[focus]
            else:
                observation=f'Chưa đủ dữ liệu để đánh giá {title.lower()} năm {year}.'
                sources=[]
            missing=[mid for mid in ids if not lookup.get((mid,year)) or lookup[(mid,year)].value is None]
            result[role].append({'title':title,'observation':observation,
                'evidence_statement':f'{len(available)}/{len(ids)} chỉ tiêu có giá trị; mở từng chỉ tiêu để xem công thức và nguồn.',
                'meaning':meaning,'follow_up':('Kiểm tra dữ liệu thiếu: '+', '.join(missing)) if missing else 'Đối chiếu trạng thái nguồn và yếu tố đối chứng.',
                'metric_ids':ids,'verification_status':weakest(*(m.verification_status for m in sources)),
                'evidences':[e.model_dump(mode='json') for e in union_evidence(*(m.evidences for m in sources))]})
    return result
