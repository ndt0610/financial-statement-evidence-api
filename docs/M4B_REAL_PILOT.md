# M4b: thử nghiệm BCTC thật và PDF

Đây là thử nghiệm kỹ thuật trên hai báo cáo riêng FY2025, không phải đánh giá độc lập hay đại diện thị trường. Các ô được trợ lý chép và kiểm tra bằng hình ảnh PDF; chưa có kế toán viên độc lập xác nhận. Các trang PDF dùng để phát triển parser cũng chính là các trang dùng để đo kết quả.

| Doanh nghiệp | Tài liệu công bố | SHA-256 | Đặc điểm |
|---|---|---|---|
| NBB | [BCTC riêng](https://www.nbb.com.vn/wp-content/uploads/2026/02/BAO-CAO-TAI-CHINH-RIENG-NAM-2025.pdf) | `ec11735f832b4dcb6f22a13705e188faf5f5893b5ce74db2ae6b6c0542a8492e` | PDF chữ, CFO âm |
| PV Drilling | [BCTC riêng](https://www.pvdrilling.com.vn/Data/Sites/1/media/qhcd/bao-cao-tai-chinh/2025/20260330%20-%20PVD%20-%20BCTC%20kiem%20toan%20-%20Cong%20ty%20me%202025_Signed.pdf) | `fb1e4ff256501d44697ee3e15f323245891915b0dad1e907201bfc97d057ded2` | PDF scan, CFO dương; phần chọn trình bày VND quy đổi từ đồng tiền chức năng USD |

Các bản PDF công khai được tải vào `.cache/m4b/`, kiểm tra hash và không đưa vào repo vì chưa xác định quyền phân phối. Nhãn, trang, kỳ và mô tả nguồn nằm trong `benchmarks/m4b_real/labels.json`. Nhãn B09 hiện chỉ xác định mục và trang, chưa có bộ nhãn nội dung thuyết minh để đo độ đúng.

## Kết quả đã đo

| Phép thử | Kết quả | Phạm vi |
|---|---|---|
| Tính M4a so với phép tính Decimal độc lập | 22/22 | 11 chỉ báo × 2 báo cáo trên số đã chép; làm tròn 6 chữ số thập phân khi so sánh |
| Docling trích xuất đúng giá trị, đơn vị và loại kỳ | 56/60 | 28/30 mỗi báo cáo; cùng các trang dùng trong quá trình phát triển |
| Trang nguồn khớp nhãn | 56/60 | Bounding box hiện diện cho 56 fact nhưng chưa kiểm tra với nhãn vùng độc lập |
| Trạng thái sau trích xuất tự động | 56/56 `EXTRACTED` | Không tự xác minh; chỉ báo không trả giá trị khi đầu vào chưa xác minh |
| QA từ chối đầu vào chưa xác minh | 40/40 | Cùng một câu doanh thu qua FastAPI TestClient, lặp 20 lần mỗi báo cáo; không phải tập câu hỏi từ chối đa dạng |

Điều kiện tách hàng nghiêm ngặt hiện bỏ hai kỳ mã 270 của NBB do thiếu nhãn dòng tách biệt, và hai kỳ mã 400 của PV Drilling do ghép số hai cột. Các trường hợp mơ hồ bị bỏ qua thay vì suy diễn. Với fact đã chép để thử riêng bộ tính, NBB có lợi nhuận dương và CFO âm nên xuất tín hiệu cần rà soát; PV Drilling không có tín hiệu đó. Tín hiệu không chứng minh gian lận hay khả năng trả nợ.

Chuyển đổi Docling trên CPU 2 luồng mất 72,088 giây cho 6 trang NBB và 101,359 giây cho 4 trang PV Drilling (gồm khởi tạo, không gồm tải mô hình lần đầu). Thời gian p50/p95 của truy vấn TestClient cùng câu hỏi được ghi trong JSON kết quả của từng lần chạy; phép đo không gồm ingest, mạng hay tải đồng thời và không đại diện production. Đã thử chuyển riêng trang B09 NBB thành công, nhưng chưa đánh giá hoặc nối nội dung note vào QA.

## Chạy lại

```powershell
python -m pip install -r requirements-m2.txt
python scripts/fetch_m4b_sources.py
python -m venv .venv-m4b
.venv-m4b\Scripts\python -m pip install -r requirements-m4b-docling.txt
.venv-m4b\Scripts\python -X utf8 scripts/run_docling_extract.py .cache/m4b/nbb.pdf .cache/m4b/docling_nbb.json --start 7 --end 12 --timeout 360
.venv-m4b\Scripts\python -X utf8 scripts/run_docling_extract.py .cache/m4b/pvd.pdf .cache/m4b/docling_pvd.json --start 7 --end 10 --ocr --timeout 360
python scripts/run_m4b_manual.py
python scripts/evaluate_m4b_pdf.py
python -m pytest -q
```

Lần đầu Docling có thể tải model OCR và layout. Các trang được chọn thủ công; bộ chuyển đổi không tự tìm bảng trên toàn PDF. JSON ghi thời gian của lần chạy cụ thể.

## Nạp PDF qua API thử nghiệm

Sau khi cài hai bộ dependencies ở trên, cấu hình môi trường chạy Docling riêng rồi khởi động API:

```powershell
$env:M4B_DOCLING_PYTHON = (Resolve-Path .venv-m4b\Scripts\python.exe).Path
uvicorn app.api.main:app
```

Gửi multipart tới `POST /v1/documents/ingest-pdf` với các trường `file`, `company_name`, `company_id`, `report_scope` (`SEPARATE` hoặc `CONSOLIDATED`), `fiscal_start`, `fiscal_end`, `page_start`, `page_end` và `ocr` (`true` cho scan). Chọn tối đa 30 trang mỗi lần, file tối đa 20 MiB. Các khoảng trang tiếp theo của cùng file và metadata được gộp vào index; nạp lại một khoảng sẽ thay dữ liệu của khoảng đó. Metadata công ty/kỳ do người dùng cung cấp và cần được kiểm tra thủ công. API không tự tìm các trang cần đọc.

Index và file được lưu tại `data/uploads` theo SHA-256 và đọc lại sau khi khởi động lại. Fact PDF ban đầu ở trạng thái `EXTRACTED`; QA và health review từ chối tính toán dựa trên fact chưa xác nhận.

### Người dùng rà soát fact và thuyết minh

1. Gọi `GET /v1/documents/{hash}/pdf-facts` để xem fact, giá trị, kỳ, nguồn PDF và `fact_id`. Mở chính file PDF đối chiếu số, đơn vị, kỳ, loại báo cáo và doanh nghiệp.
2. Gọi `POST /v1/documents/{hash}/signoff` (hoặc đường dẫn tương đương `/pdf-fact-reviews`) với JSON gồm `fact_id`, `reviewer`, `decision` (`CONFIRM`/`REJECT`), `reviewed_source_and_context: true`, `confirmed_normalized_value`, `confirmed_currency` và toàn bộ object `confirmed_period` lấy từ fact. `comment` là tùy chọn. Hệ thống so sánh ID, giá trị, tiền tệ và kỳ với bản trích xuất; nếu không khớp thì từ chối. `CONFIRM` chuyển fact thành `VERIFIED`, còn `REJECT` giữ `EXTRACTED`. API chưa hỗ trợ sửa số; cần xử lý lại nguồn thay vì xác nhận một số sai.
3. Để trả lời câu hỏi lai, người rà soát tiếp tục đối chiếu mã thuyết minh trên fact với phần B09 trong PDF, sau đó gọi `POST /v1/documents/{hash}/pdf-note-reviews` với `fact_id` mới, `reviewer`, `note_ref`, `page_number`, `excerpt` và `reviewed_source_and_link: true`. API yêu cầu fact đã xác nhận, mã khớp và trang tồn tại. Trích đoạn do người dùng nhập; hệ thống **không tự kiểm chứng nội dung trích đoạn hoặc vị trí vùng trên trang**. Tham chiếu thuyết minh phải được parser đọc ra từ fact; chưa hỗ trợ tự tìm hay nhập thay thế mã bị OCR bỏ sót.

Mỗi hành động ghi người được khai báo, thời gian UTC, quyết định và ID trước/sau trong `review_history` của cùng index; xem lại qua endpoint danh sách. ID thay đổi khi fact thay đổi để chặn xác nhận bản cũ. Không nạp lại trang có fact đã xác nhận; trang khác vẫn được bổ sung. QA có thể trả lời khi các fact cần thiết đã xác nhận; câu hỏi lai còn cần liên kết thuyết minh đã xác nhận.

Đây là prototype local: trường `reviewer` chỉ là tên tự khai, không có xác thực hay chữ ký, lịch sử lưu file JSON có thể sửa, và khóa đồng thời chỉ hoạt động trong một tiến trình API. Không triển khai công khai hoặc dùng thay hệ thống kiểm toán/quy trình phê duyệt. Xác nhận của người dùng không biến bộ nhãn thử nghiệm hiện tại thành nhãn chuyên gia độc lập.

Đã thử endpoint thật với Docling trên trang 10 PDF NBB: ingest 4 fact `EXTRACTED`, 21,24 giây trong một lần chạy cục bộ; câu hỏi doanh thu trả `UNVERIFIED` kèm chứng cứ trang 10 và không trả giá trị. Đây không phải benchmark độ trễ production.

## Giới hạn và việc còn lại

- Chưa có giao diện rà soát, xác thực người duyệt, lưu vết chống sửa, sửa fact sai hoặc trích xuất/thẩm định tự động thuyết minh PDF. Không dùng nhãn đánh giá làm xác nhận sản phẩm.
- Cần người am hiểu kế toán rà soát nhãn, bổ sung tập báo cáo mới và gán nhãn tách biệt trước khi dùng kết quả trong CV. Cần đo vùng bằng nhãn tọa độ, tỷ lệ từ chối trên nhiều câu độc lập và API/ingest trong điều kiện triển khai.
- Các biểu mẫu, năm, đơn vị và bố cục ngoài hai mẫu chưa được xác thực. PDF scan, bảng nhiều trang và bảng gộp còn có thể lỗi; không thay thế kiểm tra của chuyên viên.
