# Trạng thái kiểm chứng — AI email drafts (n8n + Streamlit)

Ngày: 21/10/2026. Phạm vi: `docs/SPEC_AI_N8N_VI.md`. Hướng dẫn triển khai: `docs/README_DEPLOY_CLOUD.md`.

## Kết luận ngắn

- **Đã chạy và kiểm thử cục bộ:** code Streamlit + store + giao thức gửi, hai workflow JSON (A soạn nháp rẽ nhánh 3 loại, B gửi chung), prompts/schema/fixture, bộ câu có nhãn, bộ case dữ kiện. Toàn bộ test suite đạt trên SQLite và trên PostgreSQL 16 thật.
- **Đã chạy thật một phần (xem bảng bên dưới):** Neon + n8n Cloud workflow A + gpt-6-luna tạo được nháp Reminder, gọi từ Streamlit chạy trên máy.
- **Chưa kiểm chứng:** Streamlit Community Cloud, workflow B / Gmail, gửi email thật. **Không có URL deploy.** Bước 5 của đặc tả (gửi email thật tới hộp thư nhóm) **chưa nghiệm thu**.
- **Chưa có số liệu AI:** macro-F1 / precision / recall chưa đo vì chưa có lần chạy live AI. Không báo số nào thay thế.

## Kiểm chứng thực tế (cập nhật 21/10/2026 tối)

| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| PostgreSQL được host (Neon, Frankfurt, direct connection, TLS) | **Đạt** | Bootstrap tạo schema `g2_ai_v1`, role `g2_ai_app` / `g2_ai_n8n`, allowlist 1 hộp thư thử; `--check` kết nối bằng role app |
| n8n Cloud 2.44.5: import workflow A, credential Postgres (role n8n) + Header Auth, publish | **Đạt** | Lỗi thao tác ban đầu (import đè vào workflow có sẵn) đã làm lại sạch |
| Live AI: Streamlit **chạy trên máy** → production webhook A → Neon → gpt-6-luna (Gateway credits) → lưu DB → hiển thị | **Đạt 1 nháp Reminder** (booking 27767, request `0f988aed`) | Lần đầu lỗi 400: gpt-6-luna không nhận `max_tokens`; đã bỏ tùy chọn này (repo + n8n). Lỗi được lưu đúng là `failed`, không treo |
| Workflow B, Gmail, gửi email thật | Chưa | |
| Streamlit Community Cloud, private sharing, reboot | Chưa | App mới chạy local; chưa có GitHub repo |
| Số liệu phân loại intent (macro-F1) | Chưa đo | |

## Kết quả test (21/10/2026, Windows 11, Python 3.14.4, Streamlit 1.66.0, Node.js 24.19.0)

| Lần chạy | Kết quả | Skip và lý do |
|---|---|---|
| Baseline trước khi sửa (SQLite) | 263 test, **OK**, 5 skip | — |
| Toàn bộ sau khi sửa, SQLite | **367 test, OK, 0 fail, 15 skip** | 7 test chỉ PostgreSQL (AI store), 3 chỉ PostgreSQL (G2 concurrency), 2 cần Node + PostgreSQL (mô phỏng Gmail), 1 cần PostgreSQL (service gọi n8n), 2 cần n8n thật (`G2_TEST_N8N_URL`) |
| Toàn bộ sau khi sửa, PostgreSQL 16.2 (`G2_TEST_PG_DSN`, instance tạm cục bộ) | **367 test, OK, 0 fail, 6 skip** | 2 test chỉ SQLite (AI store), 2 về file SQLite (G2), 2 cần n8n thật |

104 test mới (`tests/test_ai_email_*.py`): contract/render/fact-check (30), store và giao thức (34), workflow n8n (29), giao diện Streamlit (6), đóng gói Cloud (5). Regression G2 cũ (I4, G3, capacity, leakage, workflow demo cũ) vẫn đạt.

## Ca bắt buộc đã có test

| Yêu cầu | Bằng chứng (test) |
|---|---|
| Nhắc lịch / xác nhận chạy khi guest_message = null, không intent giả | `TestContext.test_reminder_and_confirmation_need_no_guest_message`, `test_three_types_through_workflow_a` |
| Trả lời khách thiếu nội dung bị chặn, không gọi model | `test_reply_without_message_is_refused`, `test_reply_without_message_never_reaches_model`, UI `test_reply_requires_message_and_shows_unclear` |
| Đủ ba loại nháp khớp nguồn; fixture offline đủ ba loại | `test_fixture_drafts_have_no_content_blockers`, `test_factual_case_file` (7 nhắc lịch + 7 xác nhận) |
| Đổi loại email sau duyệt không giữ nhầm nháp/approval | UI `test_confirmation_needs_no_message_and_switching_type_drops_the_draft`, `test_type_change_needs_its_own_approval` |
| Thiếu dữ kiện / placeholder chưa resolve chặn gửi; thiếu ngày muốn đổi không chặn | `test_essential_facts_and_eligibility`, `test_unresolved_placeholder_blocks`, `test_missing_new_date_is_information_not_a_blocker` |
| "Có thể tôi sẽ hủy…" → Chưa xác nhận / cần hỏi lại; phủ định; điều kiện; "Hãy hủy…" ≠ cancelled | `TestGuestReplyCases`, kiểm thêm trên trình duyệt (preview cục bộ) |
| Ngày tương đối từ mốc 2017, không tự chọn ngày | `test_weekday_is_not_resolved_to_a_date`, `reply_weekday_resolved_to_date` (vector) |
| Chính sách / giá / hoàn tiền bịa | `test_hallucinated_policy_price_and_claims_block`, `test_schema_error_hallucination_and_ai_failure` |
| Schema lỗi, JSON hỏng, hết credit/AI lỗi | `test_schema_error_hallucination_and_ai_failure`, `validator_cases.json` (24 vector, Python và JS cho cùng kết quả) |
| Prompt injection không đổi recipient/công cụ | `test_prompt_injection_changes_nothing`, `test_prompt_nodes_fence_the_guest_message`, `test_send_request_ignores_payload_content` |
| Mất response A không mất nháp, không gọi model lại | `test_lost_response_recovers_without_new_model_call`, `test_service_generate_and_lost_response`, `test_duplicate_call_reads_database_not_model` |
| Double-click / hai tab không gửi trùng | `test_two_tabs_create_one_job`, `test_concurrent_claims_only_one_wins`, `test_mock_send_through_workflow_b_and_double_click`, `test_gmail_gets_the_approved_email_from_the_database` |
| Approval cũ/bị thu hồi/hết hạn bị chặn; revoke vs dispatch cạnh tranh | `test_revoke_between_claim_and_dispatch_blocks_sending`, `test_revoke_and_dispatch_race_never_both`, `test_expired_approval_cannot_send`, `test_edit_after_approval_revokes_it` |
| Payload giả body/recipient không thay được nội dung DB | `test_send_request_ignores_payload_content`, `test_gmail_gets_the_approved_email_from_the_database` |
| Đổi recipient / mode / sender phải duyệt lại | `test_recipient_mode_and_sender_are_bound`, `test_deactivated_recipient_blocks_at_claim` |
| Gmail timeout / accepted nhưng ghi DB lỗi → unknown, không gửi lại | `test_dispatching_timeout_becomes_unknown_and_is_never_resent`, `test_gmail_timeout_is_unknown_and_not_resent` |
| Worker cũ không ghi đè worker mới (lease/version) | `test_old_worker_cannot_overwrite_new_one`, `test_illegal_transitions_are_refused_by_the_database` |
| Không ghi `guest_responses` / interventions từ nhãn AI hay email thử | UI `test_reminder_draft_approve_and_mock_send` (đếm bảng G2 trước/sau) |
| Quyền tối thiểu: role n8n không truy cập bảng, không tự duyệt | `TestPgRoles` (chạy trên PostgreSQL) |

Cách mô phỏng n8n trong test: Code node lấy **từ chính file JSON export** và chạy bằng Node.js; node Postgres được thay bằng đúng hàm SQL mà node đó gọi; model và Gmail là giả lập. Điều này kiểm logic, **không** chứng minh n8n Cloud 2.44.5 import đúng mọi tham số node.

## Rủi ro / giả định chưa kiểm được

1. **Tham số node khi import vào n8n 2.44.5:** Switch (mode expression, v3), Postgres (`queryReplacement`, v2.5), Basic LLM Chain v1.5 + OpenAI Chat Model v1.2 (`responseFormat`, `maxRetries`), Gmail v2.1 (`appendAttribution`). Viết theo cấu trúc đã biết, chưa import thử trên tài khoản. Nếu node báo thiếu tham số sau import, sửa trong UI rồi export lại.
2. **Gateway credits:** tài khoản hiện $2.00. Chưa xác nhận `gpt-6-luna` có trong danh sách "Included in n8n"; đổi model nếu cần (README_DEPLOY_CLOUD §3.4).
3. **Nhãn bộ 40 câu** (`tests/fixtures/ai_email/guest_reply_labelled.jsonl`) do trợ lý soạn theo đặc tả; **nhóm cần đọc và xác nhận nhãn trước khi đo**. Một số câu trùng câu mẫu bắt buộc; chấm chỉ dùng live AI, không dùng fixture.
4. **Private app / hibernation / cold start** của Community Cloud: chưa thử.
5. Các mốc thời gian (lease 2–3 phút, dispatching 5 phút → unknown, timeout client 45/30 s, model 30 s) là giá trị khởi đầu, cần đo lại trên cloud.

## Cấu hình còn cần nhóm cung cấp (qua secrets, không qua chat)

PostgreSQL được host + chạy bootstrap; GitHub repo; Streamlit Cloud app private; trong n8n: credential Header Auth, Postgres (`g2_ai_n8n`), Gmail OAuth2 của tài khoản gửi thử, chọn Gateway credits cho model; địa chỉ hộp thư thử của nhóm (`G2_AI_TEST_RECIPIENTS`). Ngày hết trial chính xác lấy từ Admin Panel (màn hình 21/10 ghi "14 days left").

## Lệnh tái lập

```powershell
python -m unittest discover -s tests                         # SQLite
$env:G2_TEST_PG_DSN = "postgresql://user@host:5432/db"       # PostgreSQL thử nghiệm (schema tạm, tự xóa)
python -m unittest discover -s tests
python -m dashboard.ai_email.evaluate --factual-only        # case dữ kiện, không gọi AI
python -m dashboard.ai_email.evaluate --split test          # chỉ khi đã cấu hình live AI (tốn credit)
```
