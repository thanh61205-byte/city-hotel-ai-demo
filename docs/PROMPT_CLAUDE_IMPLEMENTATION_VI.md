# Prompt giao Claude — chỉ cải tiến AI trong n8n

Hãy đọc `docs/SPEC_AI_N8N_VI.md` và triển khai đúng phạm vi này cho project City Hotel hiện có. Đặc tả rộng `SPEC_STREAMLIT_N8N_AI_WEB_VI.md` đã được thay thế về phạm vi; không triển khai các hạng mục mở rộng trong đó.

Mục tiêu: tạo bản nháp đủ ba loại email — nhắc lịch, xác nhận đặt phòng và trả lời yêu cầu đổi/phản hồi khách — dựa trên booking và chính sách hiện có, rồi nhân viên duyệt trước khi gửi thử.

**Đích triển khai bắt buộc: Streamlit Community Cloud + n8n Cloud.** Không cần n8n local/máy cá nhân bật cho bản demo web. PostgreSQL được host lưu trạng thái; tái sử dụng backend PostgreSQL hiện có và bổ sung bảng AI nhỏ.

1. Trong Streamlit, chọn booking và loại email: `reminder`, `booking_confirmation` hoặc `guest_reply`. Chỉ `guest_reply` yêu cầu nhập phản hồi mẫu.
2. n8n rẽ nhánh theo email_type. Nhắc lịch/xác nhận dùng trực tiếp booking và chính sách, không yêu cầu phản hồi. Riêng trả lời khách phân loại: xác nhận / yêu cầu đổi / yêu cầu hủy / chưa rõ.
3. AI soạn nháp; nhánh trả lời khách trích xuất thêm thông tin từ nội dung gốc.
4. Streamlit hiện dữ liệu nguồn, nháp và kết quả phân tích nếu có để nhân viên sửa, duyệt.
5. Sau khi duyệt, n8n gửi email tới hộp thư thử nghiệm của nhóm và cập nhật nhật ký.

Ví dụ “Có thể tôi sẽ hủy, để tôi hỏi lại gia đình” phải hiện “Chưa xác nhận / cần hỏi lại”, không tự chuyển booking thành đã hủy. Không thêm tính năng AI quản lý.

Đọc README, G1/G2 data contracts, `dashboard/state_store.py`, `dashboard/n8n_client.py`, `dashboard/action_panel.py`, `dashboard/views/execution.py`, workflow/source n8n và các test liên quan trước khi sửa.

Đọc thêm `docs/AUDIT_SPEC_AI_N8N_VI.md` để biết các lỗi thiết kế đã được sửa và các điều kiện chưa kiểm chứng. Đặc tả là nguồn contract chi tiết; không suy ra code mới đã tồn tại từ báo cáo test code cũ.

Yêu cầu:

- Thêm hai workflow riêng: A tạo nháp rẽ nhánh ba email_type; B gửi thử sau duyệt dùng chung. Giữ workflow demo cũ hoạt động.
- Streamlit chỉ thêm một khu vực chọn loại email, nhập phản hồi khi cần, xem nguồn/phân tích, sửa/duyệt nháp và xem trạng thái trong trang hiện có.
- JSON chung dùng chính xác email_type, draft_subject, draft_body, source_refs, missing_information, requires_clarification và requires_human_review. Chỉ guest_reply có intent/certainty/extraction; hai loại còn lại để intent/certainty=null và extraction rỗng. Validate schema theo nhánh, nguồn và dữ kiện; đúng JSON không chứng minh văn bản đúng.
- Facts quan trọng dùng template/resolver từ dữ liệu nguồn, AI không ghi đè ngày/giá/điều khoản; nhân viên vẫn đọc văn bản trước duyệt. Render cả tiền tố/footer DEMO trước tính content hash. Thiếu ngày muốn đổi cho phép soạn email hỏi lại; server phân biệt missing_information và send_blockers, không chặn mọi email có câu hỏi.
- Nhắc lịch lấy ngày và lịch G1 hiện có; không tự tạo lịch. Xác nhận đặt phòng chỉ nhắc lại chi tiết có nguồn, không ghi event khách confirmed. Thiếu dữ kiện thiết yếu/placeholder chưa xử lý phải chặn gửi.
- Phân biệt `cancellation_requested` với `cancelled`; không tự ghi `guest_responses` hoặc đổi trạng thái booking từ nhãn AI.
- Giữ guard I4: email/phản hồi mẫu không tự trở thành yêu cầu đổi sau cuộc gọi. Không dùng nháp support để lách action policy G1.
- G1 không cung cấp đủ mọi điều kiện giá/phí/phòng trống. Thiếu nguồn thì hỏi lại hoặc đề nghị staff xác minh, không bịa.
- Duyệt gắn email_type, booking, context/revision/content hash, recipient, sender reference và delivery mode; thay đổi các yếu tố đó phải duyệt lại. Không giữ nhầm guest message/cache từ loại trước. B chỉ nhận job ID và lấy đúng email đã duyệt từ DB, không gửi body/recipient do payload tự khai. Gửi thử có log riêng, không tính là intervention thật.
- Trước khi đủ cấu hình, mặc định mock. Đích nghiệm thu là email thật tới hộp thư thử của nhóm trong allowlist server, có tiền tố DEMO, credential hợp lệ và chống trùng bền vững. Không lấy recipient từ AI hoặc tự bịa địa chỉ. Chưa có cấu hình thì hoàn thành phần độc lập, bàn giao mock và ghi bước 5 chưa nghiệm thu; yêu cầu nhóm cung cấp địa chỉ/thiết lập credentials qua cơ chế secrets, không yêu cầu dán khóa vào chat.
- Theo giao thức mục 6.1: queued/claimed chưa gửi, dispatching có thể đã gửi; sau đó simulated/provider_accepted/failed/unknown. Claim và kiểm tra approval nguyên tử trong một DB transaction/function, không BEGIN/COMMIT qua nhiều n8n node. Không tự retry Gmail node hoặc rerun riêng node gửi bỏ qua guard. Network call không nằm trong write transaction; dispatching timeout không được reclaim để gửi lại mù quáng.
- Workflow A lưu request/result theo request_id trước khi trả HTTP; mất response không làm mất nháp hoặc gọi model lại. Workflow B lưu provider result trước response; UI đọc lại DB. Send key gắn draft revision/approval/recipient/mode, không gắn browser session hoặc UUID mới mỗi click. Revoke approval và lease/version phải có test cạnh tranh.
- Dùng Gmail Send cho cả ba loại, vì phản hồi mẫu không có Gmail message/thread ID thật. Ưu tiên Managed OAuth2 trên n8n Cloud; sender và recipient thử là hai cấu hình rõ ràng.
- Không có key thì dùng fixture được gắn nhãn; text tự do có thể nhập nhãn thủ công. Không coi fixture là prediction AI hoặc báo workflow chạy khi n8n đang offline.
- Cấu hình provider/model thực tế qua server/n8n credentials; không hard-code tên coding agent thành API model ID.
- Streamlit Python gọi production HTTPS webhook có authentication của n8n Cloud; không gọi localhost, không dùng tunnel/callback vào máy người dùng. Workflow trả JSON trực tiếp; trạng thái gửi lưu DB để đối soát khi timeout. Không phụ thuộc public management API của n8n trial.
- Dùng Cloud Secrets cho webhook/DB, n8n credentials cho Gmail/AI; ưu tiên runtime credit thực sự được tài khoản cấp. Trial 14 ngày và khoản 2 USD không được coi là ngân sách chắc chắn: kiểm tra số dư, hạn, model/node hỗ trợ thực tế. Không nhầm Assistant credits với runtime AI credits; không tự mua gói/nạp tiền.
- Cloud app mặc định chia sẻ riêng cho nhóm. Không xây RBAC nhiều vai trò, nhưng không public quyền gọi AI/gửi email cho người ẩn danh. Nếu private sharing không phù hợp thì gate truy cập tối thiểu hoặc public read-only/mock.
- Dùng PostgreSQL với TLS cho state G2 và draft/approval/send job/audit; n8n đọc approval và claim idempotency nguyên tử, không được cấp quyền tự duyệt. Không dùng SQLite trên Streamlit Cloud hoặc session_state làm nguồn bền vững cho gửi thật.
- PostgreSQL là dịch vụ thứ ba cần nhóm chọn/cấu hình, không kèm sẵn quyền truy cập với hai cloud. Không cam kết miễn phí hoặc tự mua gói. Code pg_backend hiện CREATE SCHEMA và dùng session search_path: tách bootstrap khỏi runtime ít quyền, dùng direct/session connection hoặc sửa/test rõ nếu dùng transaction pooling. AI schema riêng, không thay G2 v9 chỉ để thêm nháp.
- Dùng một workspace demo chung có lịch sử chung cho nhóm private. Browser session chỉ là correlation, không phải danh tính hay ranh giới dữ liệu riêng. Reviewer nhập tay là nhãn demo, private sharing không tự biến nhãn thành danh tính đã xác minh. Không mở rộng sang multi-user RBAC/private drafts.
- Chuẩn bị `dashboard/requirements.txt` cho Community Cloud, giữ nguyên dependency training; config theme ở root, artifacts F/G1 cần cho loader, secrets mẫu và hướng dẫn GitHub/deploy. Không chạy training hoặc launcher cài n8n khi cloud app khởi động.
- Chuẩn bị seed lịch sử trước demo và kiểm tra cold start; Community Cloud có thể hibernate, không hứa luôn phản hồi ngay. Trước làm đầy đủ UI, chứng minh private app → webhook auth → DB → một AI response hoạt động; gửi smoke email chỉ tới địa chỉ nhóm đã chỉ định và duyệt.

Không làm lại dashboard, AI quản lý, Scenario lab, RBAC nhiều vai trò, FastAPI, Docker/VPS, migration toàn hệ thống hoặc retrain model. Chỉ bổ sung module/log/schema và cấu hình cloud cần cho luồng trên; giữ local demo cũ để regression/offline.

Chạy regression phù hợp và tests mới: nhắc lịch/xác nhận không cần guest_message, reply thiếu message bị chặn, đủ ba loại nháp khớp nguồn, thay loại sau duyệt, thiếu facts/placeholder, intent mơ hồ/phủ định, ngày tương đối, hallucinated policy, schema lỗi, prompt injection, stale approval, duplicate send và timeout. Fixture/offline có đủ ba loại. Tạo bộ câu có nhãn tách development/test cho reply và bộ kiểm tra factual correctness riêng cho hai loại còn lại; báo số liệu thật khi có API.

Bàn giao code chạy được, hai workflow JSON, prompts/schema, fixture/tests, README deploy và demo đủ ba loại email. Kiểm chứng trên Streamlit Community Cloud + n8n Cloud: tạo nháp, duyệt, gửi hộp thư thử, quyền truy cập, state qua reboot, không gửi trùng hai tab và không cần máy cá nhân bật. Ghi ngày trial hết hạn thực tế, export workflow và backup state trước hạn.

Báo rõ test pass/fail/skip, live AI/email/cloud đã kiểm chứng hay chưa; chỉ trả URL deploy khi có thật. Thiếu account/GitHub/credentials/địa chỉ nhận thì hoàn thành phần độc lập và ghi đúng cấu hình còn cần, không giả vờ đã deploy. Không mua dịch vụ hoặc liên hệ khách thật.
