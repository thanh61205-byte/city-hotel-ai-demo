# Rà soát đặc tả AI email + n8n Cloud

Ngày: 21/10/2026. Phạm vi: rà soát thiết kế và đối chiếu code hiện có, không triển khai tính năng hoặc gửi email.

## Kết luận

Kiến trúc phù hợp cho demo nhóm: Streamlit Community Cloud làm giao diện, n8n Cloud tạo nháp/gửi email sau duyệt, PostgreSQL được host lưu trạng thái. Chỉ có ba loại email đã chốt. Không cần FastAPI, agent tự trị, Docker/VPS hoặc viết lại mô hình.

Bản trước có điểm thiếu rõ ràng; đã sửa trực tiếp trong `SPEC_AI_N8N_VI.md` và đồng bộ `PROMPT_CLAUDE_IMPLEMENTATION_VI.md`. Thiết kế sau rà soát có thể giao triển khai, nhưng chưa thể xác nhận chạy end-to-end trước khi tích hợp các tài khoản thật.

## Phát hiện và cách sửa

| Vấn đề | Bằng chứng / ảnh hưởng | Sửa trong đặc tả |
|---|---|---|
| Thiếu một dependency triển khai rõ ràng | App cần DB bền vững; hai dịch vụ cloud không tự cung cấp DB app cho nhóm | Nêu PostgreSQL là dịch vụ thứ ba, cần chọn provider/kết nối/chi phí; không khẳng định miễn phí |
| Nhập nhằng session với danh tính và khôi phục dữ liệu | Session mất sau reconnect; private sharing không tự xác minh reviewer nhập tay | Chốt workspace demo chung, lịch sử chung; session chỉ correlation; không hứa private drafts/RBAC |
| Gửi nội dung payload có thể khác bản duyệt | Hash approval chưa đủ nếu dispatch lấy body/recipient tùy ý | n8n đọc bản cuối cùng từ DB; approval gắn cả recipient, sender, mode và content |
| Double-click/retry chưa mô tả đầy đủ | UUID mới mỗi click không chống trùng; timeout có thể đã gửi | Unique logical send key, claim nguyên tử, lease/version, dispatching → unknown khi chưa rõ |
| Transaction qua nhiều node n8n không chắc cùng connection | BEGIN/COMMIT ở hai node riêng không đủ bảo đảm tính nguyên tử | Một SQL transaction/function cho claim/recheck; không giữ khóa khi gọi mạng |
| Mất HTTP response A làm mất nháp/tốn thêm AI credit | Bản cũ chỉ mô tả HTTP trả kết quả trực tiếp | Lưu request/result ở DB, đọc lại theo request ID; không tự gọi model lại |
| Code PostgreSQL cần quyền bootstrap/session state | `pg_backend.PgConnection.__init__`: CREATE SCHEMA, SET search_path; app chọn schema theo F/G1/ngày | Tách bootstrap/runtime, AI schema riêng; kiểm tra pooling/TLS từ cả hai cloud |
| Email Reply cần message/thread thật | Input là văn bản mẫu trong Streamlit | Gmail Send cho cả ba loại; không giả Gmail thread ID |
| Missing information có thể làm luồng hỏi lại bị kẹt | Thiếu ngày đổi là lý do gửi câu hỏi, không nhất thiết blocker | Phân biệt missing_information và send_blockers do server tính |
| Dataset không có đủ dữ kiện cho “xác nhận live” | Loader có booking_id/arrival/room code, consent/status live còn unknown | Email lịch sử có nhãn DEMO, không đòi contact thật, không biến unknown thành consent=True |
| JSON hợp lệ không chứng minh email đúng | LLM vẫn có thể bịa chính sách trong text | Facts quan trọng dùng template nguồn; review người dùng; không hứa validator hiểu đúng mọi câu |
| Ngày hệ thống có thể bị dùng để hiểu “thứ Sáu” | Replay là 2017, server là thời gian thật | Tách message_business_at và received_at_utc; yêu cầu mơ hồ phải hỏi lại |
| Cloud không tương đương luôn sẵn sàng | Community Cloud có hibernation và giới hạn private app | Thêm pre-demo wake/smoke check, kiểm tra private sharing thực tế |
| Credit/thời gian trial không đủ chứng minh ngân sách | Chưa truy cập tài khoản n8n của nhóm | Không đảm bảo có 2 USD; ghi đúng credit, model/node và ngày hết hạn lúc setup |

## Kiểm tra đã thực hiện

Đã đọc/đối chiếu app entrypoint, loaders F/G1, n8n client, state store, PostgreSQL backend, dependency files và hướng dẫn cloud chính thức.

Đã chạy 40 tests hiện có, kết quả **40 đạt, 0 fail, 0 error, 0 skip**, trong các file:

- `tests/test_g2_data.py`
- `tests/test_g2_g1.py`
- `tests/test_g2_n8n_client.py`
- `tests/test_g2_offer_basis.py`

Log cục bộ: `output/review/spec_audit/regression.log`. Test dùng cơ chế local/fixture của repo; không có service Cloud/AI/Gmail/PostgreSQL hosted mới được cấu hình trong lần rà soát này. Các test này xác nhận một phần nền code cũ; không chứng minh workflow mới hoặc deployment đã hoạt động. Không chạy lại training/model selection hoặc toàn bộ test suite vì lần này chỉ sửa tài liệu.

Đã rà lại UTF-8, cấu trúc Markdown/JSON mẫu, đường dẫn file nguồn và sự thống nhất giữa prompt với đặc tả sau sửa.

## Điều kiện cần kiểm chứng khi triển khai

1. GitHub repo thực tế, private app trên Streamlit Community Cloud và quyền xem của nhóm.
2. n8n Cloud workspace còn hoạt động, hai workflow đã publish, webhook auth đúng.
3. PostgreSQL provider/endpoint đã chọn; cả hai cloud kết nối TLS và đủ quyền migration/runtime; thử restart/khôi phục.
4. AI runtime credit hoặc API credential thật, model/node structured output tương thích, quota thực tế.
5. Gmail sender được kết nối OAuth, recipient thử nhóm chỉ định; test Send và kiểm tra thư nhận.
6. Test cạnh tranh/timeout thực tế: không gửi trùng, không dùng approval cũ, không mất nháp/kết quả sau reconnect.

Chưa có các điều kiện này thì có thể hoàn thành code/fixture và tài liệu, nhưng chưa được báo “cloud demo đã nghiệm thu”.

## Nguồn nền tảng đã đối chiếu

- Private/public app và giới hạn private app: [Streamlit sharing](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app).
- Hibernation và giới hạn tài nguyên: [Streamlit app management](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app).
- Dependency manifest: [Streamlit dependencies](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies).
- Gmail Managed OAuth2: [n8n Google credentials](https://docs.n8n.io/integrations/builtin/credentials/google/oauth-single-service).
- Gmail Send/Reply: [n8n Gmail operations](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.gmail/message-operations).
- Webhook auth, production URL và response: [n8n Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook).
- Trial/credit: [n8n trial](https://docs.n8n.io/deploy/use-n8n-cloud/start-your-free-trial.md), [n8n pricing](https://n8n.io/pricing/).

Nguồn sản phẩm có thể thay đổi. Số dư/điều kiện của tài khoản thực tế khi triển khai là cơ sở quyết định sử dụng, không phải giả định trong đặc tả.
