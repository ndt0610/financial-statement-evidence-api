# P11: hợp đồng phân tích và hiển thị

- Đầu vào lấy từ `AnalysisBook`, giữ kỳ, phạm vi và chứng cứ. Nhận định được tạo ở máy chủ bằng Decimal; giao diện chỉ định dạng và dựng biểu đồ.
- Nhóm tăng trưởng đối chiếu doanh thu, LNST, biên gộp, chi phí bán hàng và quản lý và hai hiệu ứng của cầu nối lợi nhuận khi có nguồn.
- Nhóm dòng tiền đặt CFO cạnh LNST và các dòng B03 có nguồn; không quy toàn bộ thay đổi CFO cho một cấu phần.
- Nhóm DuPont hiển thị biên ròng, vòng quay tài sản và đòn bẩy cùng ROE/ROA theo hai kỳ có dữ liệu.
- Tồn kho hiển thị số dư, tỷ trọng, số ngày và cơ cấu thuyết minh khi có; dòng chưa trích xuất không trở thành giá trị 0.
- Tỷ số PROXY và số mới trích xuất giữ trạng thái nguồn; xem bằng nút mở nguồn và ngăn chi tiết.
- CAGR cần chuỗi liên tục và đầu/cuối dương. Tăng trưởng so với kỳ trước chỉ diễn đạt bằng % khi cơ sở dương và giá trị mới không âm.
- Chênh lệch điểm phần trăm được tính từ Decimal trước khi làm tròn. PNJ FY2025: biên gộp thay đổi 4,324780... điểm %, hiển thị 4,32; SG&A thay đổi 1,272557... điểm %, hiển thị 1,27. Trừ hai giá trị đã làm tròn lần lượt cho 4,33 và 1,28, không phải chênh lệch làm tròn từ số gốc.
- Vĩnh Hoàn FY2024: biên gộp lần lượt 14,879121...% và 15,144825...%; chênh chính xác 0,265704... điểm %, làm tròn thành 0,27 điểm %. Trừ hai số đã làm tròn 15,14 − 14,88 cho 0,26.
- Tác động cộng gộp biến động chi phí bán hàng và quản lý PNJ FY2025 trên cầu nối được tính từ số gốc, làm tròn sau cùng là -143,77 tỷ đồng; không cộng các thành phần đã làm tròn để lấy -143,78 tỷ.
- Waterfall cho lợi nhuận và tiền dùng số đầu kỳ/cuối kỳ; CFO trình bày cấu phần trong năm; cầu nối nguồn tài trợ trình bày biến động. Phần thiếu hoặc sai khác hiển thị riêng.
- PNJ 2024–2025: bảng CSV đối chiếu là bằng chứng so khớp nội bộ, không xác nhận người duyệt đã xem từng ảnh. Xem `P10_PNJ_SOURCE_RECONCILIATION.md` và `/audit`.

Giới hạn: bộ phát hiện chưa bao phủ mọi quy luật và thuyết minh; các chú giải kinh tế cần nguồn riêng. Việc parser PDF còn chọn lọc mã và B09 còn hạn chế không được thay thế bằng suy đoán khi sinh kết luận.
