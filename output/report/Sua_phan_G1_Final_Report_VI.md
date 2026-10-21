# Sửa phần G1 trong Final Report (20/10/2026)

**File gốc:** `Downloads/Advanced BA - Group 2 - Final Report (1).docx`. File gốc không bị sửa.

Lưu ý về file `Final Report.docx` (không có "(1)"): nó có cùng nội dung, nhưng cuối file bị dán thêm toàn bộ "Kế hoạch triển khai", nên không dùng bản đó.

**Bản đã sửa:**
- `Advanced BA - Group 2 - Final Report - G1 revised.docx`
- `Advanced BA - Group 2 - Final Report - G1 revised.pdf` (Word xuất ra 25 trang; mục lục đã cập nhật)

Mọi số liệu đều được đối chiếu với artifact hiện tại: F run `f-v3.1-2026-10-19_20261020-101915`, G1 policy `+g1-543bdc31`, `artifacts/business_case/`, `artifacts/scenarios_capacity/`.

## Những gì đã sửa

| Chỗ | Trước | Sau |
|---|---|---|
| Expected Business Value, dòng 2 (Evaluation) | "Precision@20% and Lift@20%" | Đo theo tier của G1: tỷ lệ huỷ và phần giá trị bị huỷ trong tier gọi và tier liên hệ, so với cả hàng đợi tuần (2017: tier gọi 38.3% so với 24.0%) |
| Expected Business Value, dòng 4 | "about EUR 30k … about 170% … about 7 months"; ô Evaluation dán nhầm 2 tài liệu tham khảo (Falk & Vieru, Friedman) | Số chính xác: EUR 30.4k/năm, ROI 173%, payback 6.9 tháng, one-off EUR 15.6k, chi phí chạy EUR 3.4k/năm, ghi rõ "dựa trên giả định cho đến khi có pilot". Đã xoá 2 tài liệu dán nhầm |
| Expected Business Value, dòng 3–4 | Một số ô dùng font Gungsuh (trông như font máy chữ) | Đổi về Times New Roman như các ô khác |
| Bảng rule 5.2.2, I1 | "Low tier, or 7 days or fewer to arrival" | "Low tier, or reminder tier with 7 days or fewer". Tier gọi khi còn ≤ 7 ngày là I5, không phải I1 |
| Bảng rule, I3 | Thiếu điều kiện cách ngày đến | Thêm "at least 8 days before arrival" |
| Bảng rule, I4 | "Revenue Manager if price changes" | Thêm "(always, in the prototype)", vì G2 luôn bắt duyệt offer |
| Đoạn sau Hình 5.2 | "n8n executes actions on the scheduled date…" (như đã chạy thật) | Ghi rõ: đây là thiết kế production; trong prototype, các kiểm tra chạy ở dashboard và lặp lại ở workflow n8n demo, không gửi gì |
| Hình 5.2 | Ô cuối: "n8n executes on the scheduled date" | Vẽ lại bằng đúng code của notebook 05; ô cuối thành "Execution on the scheduled date (prototype: dashboard + demo n8n workflow, nothing is sent)", thêm kiểm tra cửa sổ gọi và giới hạn cuộc gọi/ngày; holdout ghi là "pilot design" |
| Backfire | "99.96% … are free to cancel" | "… are No Deposit and free to cancel" (đúng nguồn của con số) |
| Tiêu đề 5.2.3, 5.2.4 | Heading 2 (sai cấp) | Heading 3, cùng cấp với 5.2.1–5.2.2. Mục lục đã cập nhật |
| "Other assumptions…" | Thiếu reminder uplift và call backfire; "one-off cost of EUR 30k" | Thêm reminder uplift 0% / 8% → EUR 25.5k / 38.5k và call backfire 1% → EUR 26.5k; sửa thành "implementation cost of EUR 30k instead of 15k" (tổng one-off khi đó ≈ EUR 30.6k) |
| Sau Hình 5.4 (mới) | Report nói tới pricing, allocation, resale nhưng không có số về công suất | Thêm bảng Occupancy / ADR / RevPAR cho S0, S4, S5, S6 (từ `src/scenario_capacity.py`), kèm chú thích: S6 dựa trên độ co giãn giá −0.8 là giả định nhóm; ở −1.2 hoặc −1.5 RevPAR giảm, nên S6 là câu hỏi cho pilot, không phải khuyến nghị |
| 5.2.4, dòng Targeting quality | Chỉ có "Top 20%: precision 41.1%, lift 1.71" (cách xếp hạng của F) | Thêm kết quả của chính G1: tier gọi 38.3% huỷ (lift 1.60), chiếm 44.8% giá trị bị huỷ; tier gọi + reminder chiếm 87.4%; vẫn giữ số top 20% của model (rule 35.3%) |
| 5.2.4, dòng Business value | Thiếu payback | Thêm payback 6.9 tháng |
| Limitation 2 | "historical model fitted on 2015 data" | "selected on 2015 data and refitted before each 2016 decision" |
| Output to the system | "n8n executes automatic actions" | Thêm rule và cờ manual review (17 task); ghi rõ n8n trong prototype là demo, không liên hệ khách |

## Đã kiểm tra lại và đúng, giữ nguyên

- 12,102 booking, 35 lô tuần, tỷ lệ huỷ 24.0%.
- Grid 42 chính sách, chọn trên dữ liệu 2016.
- Tier 15% / 60%, tối đa 20 cuộc gọi/ngày.
- Chi phí: EUR 1.41 / cuộc gọi, EUR 0.05 / SMS, one-off EUR 15,564, chi phí chạy EUR 3,360/năm.
- Uplift 10% / 3%, backfire 0% / 0.5%.
- Bảng S0–S5 (số cuộc gọi, số booking nhận reminder, số huỷ ngăn được, net/năm).
- Năm 1 EUR 11.5k; 3 năm EUR 65.5k; ROI 173%; payback 6.9 tháng.
- Model so với rule: EUR 30.4k so với 28.8k.
- Break-even 1.1% / 2.0%.
- Độ nhạy: −1.9k / 78.8k / 40.3k / 20.5k / 22.4k / ROI −82% / 95% / 13.6 tháng.
- Công suất: trung bình 16.5 cuộc/ngày, tối đa 20, 17 task duyệt tay.
- G3: −EUR 0.7k/năm.
- Pilot: MDE khoảng 5 điểm %, hiệu ứng giả định 3.5 điểm %.
- 4,600 booking chưa có kết quả.

## Chưa sửa (không thuộc G1)

- **Method 1 (5.1):** vẫn là số và mô hình của bản F cũ (Logistic Regression, PR-AUC 0.457…). Bảng kết quả 5.1.3 vẫn trống.
- **PDMT map:** ảnh vẫn là bản vẽ lại có lỗi.
- **Mục tiêu "Recall ≥ 70%"** ở dòng 1 của Expected Business Value.
- **Danh mục tài liệu tham khảo:** thiếu 7 nghiên cứu, 1 nguồn bị trùng.

Chi tiết các lỗi này: `output/review/Kiem_tra_Final_Report_2026-10-20_VI.md`.

**Font:** phần Method 2 dùng Cambria, trong khi các phần khác dùng Times New Roman. Lỗi này có sẵn từ bản gốc; nên thống nhất font khi ghép bản cuối.
