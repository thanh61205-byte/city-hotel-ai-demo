# Audit đề xuất G3 – Overbooking có duyệt từng case

Ngày: 21/10/2026. Đối chiếu đề xuất "G3 – Automatic Overbooking & Case-by-case Approval" của nhóm với dữ liệu, mô hình F, policy G1 và kết quả phân tích overbooking hiện có (`config/assumptions.yaml` mục `overbooking`, `artifacts/business_case/overbooking_grid_2016.csv`, `overbooking_2017.csv`, `key_numbers.json`).

## Kết luận

Giữ được khung quản trị của đề xuất: có giới hạn, duyệt từng case, kiểm tra lại trước khi mở bán, chặn khi vượt hạn mức, lưu lý do từ chối, audit, và "G3 luôn trả về kết quả hoặc trạng thái giải thích được". **Không giữ được phần tính toán và đơn vị duyệt.** Đề xuất có 5 lỗi thiết kế làm số phòng overbook bị ước lượng sai, duyệt sai đối tượng hoặc không thực hiện được. Ngoài ra, bằng chứng duy nhất của dự án cho thấy overbooking **lỗ** trên dữ liệu 2017.

Phiên bản sửa (mục 4): G3 tính và giải thích một **giới hạn tư vấn theo đêm lưu trú × loại phòng**. Revenue Manager duyệt từng đề xuất, General Manager đặt giới hạn. **Không mở bán trong prototype** cho tới khi pilot cho kết quả dương. Code hiện tại vẫn từ chối mọi hành động `overbooking` ở service, n8n và database, đúng với phiên bản này.

## 1. Bằng chứng hiện có

| Phân tích | Kết quả |
|---|---|
| Lưới 2016 (chọn tham số: L = 7/14/30 ngày trước đêm lưu trú, k = 0–2 độ lệch chuẩn, trần 5% số phòng của loại phòng, chỉ khi loại phòng đã kín) | Chỉ k = 0 mở phòng; tốt nhất L = 30: **+EUR 374/năm**, 7 phòng, 0 khách phải chuyển |
| Kiểm tra ngoài mẫu 2017 với policy đã chọn (L = 30, k = 0) | 3 đêm, 11 phòng mở thêm, 8,8 bán được, **4,6 khách phải chuyển**, **−EUR 455 (−EUR 683/năm)** |
| Độ nhạy chi phí chuyển khách | EUR 75: −EUR 165/năm; EUR 300: −EUR 1.719/năm. Lỗ ở mọi mức |

Giả định: bán được 80% phòng mở thêm; EUR 150 mỗi khách-đêm phải chuyển (khoảng 1,4 × ADR 2016; giả định của nhóm, khoảng 75–300). Đây là phân tích thăm dò, nhìn lại quá khứ, không phải policy vận hành.

## 2. Lỗi trong đề xuất

| # | Vấn đề | Vì sao sai | Sửa |
|---|---|---|---|
| 1 | Đơn vị tính là **ngày đến × loại phòng** | Overbooking thiếu phòng xảy ra theo **đêm lưu trú**. Booking 3 đêm chiếm 3 đêm; đêm đông nhất mới là đêm vỡ. Buffer hiện tại (theo ngày đến) ghi rõ không kiểm tồn phòng từng đêm | Tính theo **đêm lưu trú × loại phòng**, như phân tích 5b |
| 2 | **B = Σ p_cancel** lấy thẳng từ điểm chấm cuối tuần đặt | p_cancel là xác suất hủy **trong toàn bộ thời gian từ lúc đặt**. Booking còn sống tới L ngày trước đêm lưu trú có xác suất hủy còn lại thấp hơn, nên B bị **ước lượng quá cao**. F cũng không hiệu chỉnh p (Brier 0,192) | Dùng xác suất **đến ở** của các booking còn hiệu lực tại thời điểm quyết định, ước lượng theo khoảng cách tới đêm lưu trú và hiệu chỉnh trên mùa trước |
| 3 | **O = min(O_max, ⌊B × α⌋)** rồi mới "thỏa ngưỡng" | Dùng kỳ vọng, bỏ qua độ dao động. Booking đoàn hủy **cùng lúc** (không độc lập), nên rủi ro thiếu phòng cao hơn công thức cho thấy. α không có cơ sở chọn | Lấy k lớn nhất ≤ min(O_max, 5% số phòng) sao cho **P(khách đến > số phòng) ≤ ε**, tính bằng mô phỏng hoặc phân phối Poisson-binomial, với mỗi block đoàn là một khối tương quan. Bỏ α |
| 4 | **Duyệt "từng booking bổ sung" trước khi đưa lên market** | Booking bổ sung **chưa tồn tại** lúc quyết định: thứ cần duyệt là **số phòng mở thêm**. Trên kênh OTA, booking được nhận tức thời, không chờ người duyệt từng cái. Danh sách "giá trị booking, rủi ro" lại trộn booking hiện hữu (đang bị dự báo hủy) với booking mới | **Đơn vị duyệt = một đề xuất cho một đêm × loại phòng** (k phòng, hiệu lực tới thời điểm T). Booking mới trong giới hạn được nhận bình thường; vượt giới hạn thì kênh tự đóng |
| 5 | **G3.2 "B > 0"** | Gần như luôn đúng, nên không lọc được gì | Chỉ xét khi **loại phòng đã kín** ở thời điểm quyết định và k* ≥ 1 |
| 6 | **G3.5 "lợi ích > chi phí"** chưa định lượng | Không kiểm tra được | E[net] = tỷ lệ bán được × đóng góp mỗi phòng-đêm − chi phí chuyển khách × E[số khách phải chuyển] > 0, với giả định ghi rõ và độ nhạy |
| 7 | Tính trùng với giữ khách (I1–I3) và nhả phòng đoàn (G1/G2) | Nếu gọi điện giữ khách thành công, số hủy giảm. Phòng đoàn đã nhả bán lại qua G2 không được tính lần nữa | Trừ phần đã bán lại qua G2; chỉ dùng xác suất phản ánh việc đã can thiệp, sau khi pilot đo được uplift |
| 8 | **G3.4** cần tồn phòng thực, phòng hỏng, nâng hạng | Dữ liệu không có PMS live; sức chứa 252 phòng là **ước lượng** từ các đêm có khách | Trong prototype G3 chỉ tính tư vấn; mở bán thật cần dữ liệu PMS |
| 9 | **G3.6** "Revenue Manager duyệt trước tham số và giới hạn" | Bảng quyết định giao **GM đặt giới hạn**; RM duyệt từng đề xuất | GM đặt O_max, ε, chính sách chuyển khách; RM duyệt / từ chối từng đêm × loại phòng |
| 10 | "Đưa booking lên market" | Diễn đạt sai bản chất | "Mở thêm phòng bán trên kênh" (tăng allotment) |
| 11 | Chưa nói khách nào bị chuyển nếu thiếu phòng | Rủi ro công bằng và trải nghiệm khách | Quy trình chuyển khách theo chính sách khách sạn, **không dùng điểm rủi ro cá nhân** để chọn khách bị chuyển; ghi nhận và bồi thường |

Điểm giữ nguyên: G3.7 (duyệt từng case, sửa đơn vị), G3.8, G3.9, G3.10, G3.11, G3.12 (bổ sung trường audit, mục 4).

## 3. Trả lời "G3 luôn chạy"

Hợp lý **nếu hiểu là luôn tính và luôn giải thích**. Mỗi đêm × loại phòng nhận một trong các trạng thái sau:
- *chưa kín phòng*;
- *không đủ bằng chứng* (k* = 0);
- *vượt giới hạn*;
- *đề xuất k phòng – chờ duyệt*;
- *đã duyệt / từ chối*;
- *policy đang tắt*.

"Luôn chạy" **không** có nghĩa là tự mở bán. Với bằng chứng hiện tại (−EUR 0,7k/năm, 4,6 khách phải chuyển), việc mở bán thật phải tắt cho tới khi pilot cho kết quả dương.

## 4. G3 sau khi sửa (dùng trong báo cáo và slide)

**G3 – Controlled overbooking (tư vấn, có duyệt; không thực thi trong prototype)**

1. **Phạm vi:** từng đêm lưu trú × loại phòng, quyết định L ngày trước đêm (L = 30 trong phân tích), chỉ khi loại phòng đã kín.
2. **Đầu vào:** các booking còn hiệu lực tại thời điểm quyết định; xác suất đến ở theo khoảng cách tới đêm, đã hiệu chỉnh; block đoàn là khối tương quan; trừ phòng đã nhả bán lại qua G2.
3. **Giới hạn đề xuất:** k* = số phòng lớn nhất ≤ min(O_max, 5% số phòng loại đó) sao cho P(khách đến > số phòng) ≤ ε **và** E[net] > 0 (bán được 80%, EUR 150 mỗi khách-đêm phải chuyển; giả định của nhóm).
4. **Kết quả luôn có:** k* kèm các con số hoặc trạng thái giải thích (mục 3).
5. **Duyệt:** Revenue Manager duyệt hoặc từ chối **từng đề xuất đêm × loại phòng** (bắt buộc ghi lý do khi từ chối). General Manager đặt O_max, ε, chính sách chuyển khách. Đề xuất đang chờ duyệt không mở bán.
6. **Trước khi mở:** kiểm tra lại tồn phòng, booking hiện hữu, giới hạn còn lại; dữ liệu đã thay đổi thì tính lại. Tổng phòng đã mở không vượt giới hạn hiện hành.
7. **Audit:** người duyệt, thời điểm, phiên bản mô hình và dữ liệu, k*, P(vượt), ε, E[net], giới hạn áp dụng, kết quả thực tế (số bán thêm, số khách phải chuyển).
8. **Trạng thái:** thiết kế. Prototype chưa xây phần tính tư vấn và vẫn từ chối mọi hành động overbooking (service, n8n, database). Chỉ bật mở bán sau pilot có kết quả dương và GM phê duyệt giới hạn.

## 5. Phạm vi sửa tài liệu

- **Final Report (4):** hàng G3 trong bảng quy tắc §5.2.2, thêm đoạn "G3 design (audited)", ý G3 trong Key findings §5.2.3, hàng G3 trong bảng §6.3.
- **Executive Summary (5):** câu G3 trong §3.2.
- **Slide:** slide 12 (dòng G3), slide 32 (hàng G3), thêm một slide phụ lục cho G3 sau slide 32, slide 33 (ô G2/G3), slide 35 (ý G3).
- **Repo:** `docs/decision_table_draft.md` (hàng G3). Code không đổi.
