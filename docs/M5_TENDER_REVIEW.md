# M5: rà soát tiêu chí tài chính nhà thầu

`POST /v1/tender-reviews` nhận `document_hashes` và `config`. Mỗi tiêu chí có `id`, `metric`, `years`, `minimum`, `operator` (`>=` hoặc `>`); `AVERAGE_REVENUE` yêu cầu `revenue_code` là `01` hoặc `10`. Cấu hình còn phải nêu `company_id`, `report_scope` và `criteria_source` của đúng hồ sơ mời thầu. Giá trị tiền là VND, tỷ số là lần; không có ngưỡng pháp lý mặc định.

Hỗ trợ `NET_ASSETS` (B01:270 − 300), `WORKING_CAPITAL` (B01:100 − 310), `CURRENT_RATIO` (100 / 310), `AVERAGE_REVENUE`, `NET_INCOME` (B02:60), `CFO` (B03:20). Một năm cần fact thuộc chính tài liệu năm đó; không tự trộn số so sánh từ một báo cáo khác làm báo cáo năm độc lập. Các fact phải cùng công ty, phạm vi, kỳ, tiền tệ VND và ở trạng thái VERIFIED; thiếu hoặc trùng fact, cảnh báo đối chiếu, mẫu số không dương đều cho `NEEDS_REVIEW` và không có giá trị kết luận.

Kết quả gồm công thức, ngưỡng, trạng thái và bằng chứng từng nguồn; là đối chiếu từng tiêu chí, không phải quyết định lựa chọn nhà thầu. Đối với PDF, fact phải được con người xác nhận trước. API là prototype local và chưa có xác thực/lưu vết chống sửa.

Ví dụ:

```json
{"document_hashes":["<sha256>"],"config":{"company_id":"TEST","report_scope":"SEPARATE","criteria_source":"HSMT mục 2.1","criteria":[{"id":"working-capital","metric":"WORKING_CAPITAL","years":[2025],"minimum":"0","operator":">="}]}}
```
