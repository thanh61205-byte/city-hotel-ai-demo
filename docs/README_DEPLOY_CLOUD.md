# Triển khai AI email drafts: Streamlit Community Cloud + n8n Cloud + PostgreSQL

Phạm vi: `docs/SPEC_AI_N8N_VI.md` (ba loại email: nhắc lịch, xác nhận đặt phòng, trả lời khách; nhân viên duyệt trước khi gửi thử). Trạng thái kiểm chứng thực tế: `docs/AI_EMAIL_VERIFICATION_VI.md`. Tài liệu này không chứa secret; mọi giá trị thật nhập ở Streamlit Cloud Secrets, n8n Credentials hoặc biến môi trường trên máy chạy bootstrap — **không dán khóa vào chat hay Git**.

```text
Thành viên nhóm (private app)
  → Streamlit Community Cloud (dashboard/app.py, trang Execution & follow-up → "AI email drafts (DEMO)")
      ├─ HTTPS + header X-G2-Webhook-Token → n8n Cloud workflow A (draft)  ─┐
      └─ HTTPS + header X-G2-Webhook-Token → n8n Cloud workflow B (send)   ─┤→ Gmail Send → hộp thư thử của nhóm
  Streamlit (role g2_ai_app) ──TLS──► PostgreSQL được host ◄──TLS── n8n (role g2_ai_n8n, chỉ EXECUTE hàm)
```

Không cần máy cá nhân bật, không tunnel, không gọi localhost. Local demo cũ (`run_demo.*`, SQLite, n8n local) giữ nguyên để regression/offline; khi không cấu hình gì, khu vực AI chạy **offline preview** (fixture có nhãn, nhãn tay, mock cục bộ).

## 0. Thông tin tài khoản đã biết (21/10/2026)

| Mục | Giá trị nhìn thấy | Cần làm |
|---|---|---|
| n8n Cloud | phiên bản 2.44.5, trial "14 days left", 0/1000 executions | Ghi **ngày hết hạn chính xác** trong Admin Panel vào mục 8; export workflow + backup DB trước ngày đó |
| AI runtime | **Gateway credits $2.00** (khác "AI usage"/Assistant credits) | Chọn "Use Gateway credits" ở node model (mục 3.4). Trial không nạp thêm được; hết số dư thì node lỗi → nháp "failed", app đề xuất fixture |
| Custom roles của n8n | Enterprise, không dùng | Quyền tối thiểu làm ở PostgreSQL (role `g2_ai_n8n`) |
| PostgreSQL được host | **chưa chọn** | Mục 1 |
| GitHub repo / Streamlit Cloud / Gmail sender / hộp thư thử | **chưa có** | Mục 2–5 |

Theo [tài liệu Gateway credits](https://docs.n8n.io/build/understand-workflows/use-gateway-credits): trial có credits, không nạp thêm được cho tới khi nâng gói; chỉ model/node trong mục "Included in n8n" dùng được. Không mua gói, không nạp tiền trong khuôn khổ demo.

## 1. PostgreSQL được host (dịch vụ thứ ba)

Chọn một nhà cung cấp mà **cả Streamlit Cloud và n8n Cloud kết nối được qua Internet với TLS** (ví dụ Neon, Supabase, Aiven… — kiểm tra hạn mức/sleep/backup của gói tại thời điểm chọn; tài liệu này không cam kết miễn phí).

- Dùng **direct connection hoặc session pooler**. Store G2 hiện có đặt `search_path` theo session nên **không dùng transaction pooler** cho `G2_DB_URL`. Schema AI `g2_ai_v1` dùng tên đầy đủ và không prepared statement nên chạy được cả hai, nhưng dùng cùng kiểu kết nối cho đơn giản.
- Luôn thêm `sslmode=require`.

Trên **máy của một thành viên** (Python 3.11+, `pip install -r requirements-g2.txt`), đặt biến môi trường trong shell (không ghi vào file trong repo):

```powershell
$env:G2_AI_OWNER_DB_URL = "postgresql://OWNER:...@HOST:5432/DB?sslmode=require"
$env:G2_AI_APP_DB_PASSWORD = "<tự đặt>"
$env:G2_AI_N8N_DB_PASSWORD = "<tự đặt>"
$env:G2_AI_TEST_RECIPIENTS = "team=hop-thu-thu-cua-nhom@example.com"
python -m dashboard.ai_email.bootstrap --apply-schema --create-roles --recipients
$env:G2_AI_DB_URL = "postgresql://g2_ai_app:...@HOST:5432/DB?sslmode=require"
python -m dashboard.ai_email.bootstrap --check          # phải in "TLS on; AI schema 1"
```

- `--apply-schema`: chạy `sql/g2_ai_email_postgres.sql` (idempotent, có advisory lock, không bao giờ DROP).
- `--create-roles`: tạo `g2_ai_app` (Streamlit) và `g2_ai_n8n` (n8n: **không có quyền bảng nào**, chỉ EXECUTE 6 hàm workflow). Nếu nhà cung cấp không cho CREATE ROLE: tạo 2 role ở console rồi chạy phần sau dòng `-- @grants` của `sql/g2_ai_email_roles_postgres.sql`. Sau mỗi lần cập nhật schema, chạy lại `--create-roles` để áp lại grants.
- `--recipients`: allowlist hộp thư thử **phía server**. Địa chỉ nhận không bao giờ lấy từ AI, payload hay nội dung khách.
- Store G2 cũ (`G2_DB_URL`) cũng cần role có quyền tạo schema lần đầu; lần đầu chạy app hoặc `--seed-g2-history` bằng role đó, sau đó runtime chỉ mở schema đã có (`pg_backend` chỉ CREATE SCHEMA khi thiếu).
- Seed lịch sử G2 **một lần trước buổi demo** (qua mạng mất vài phút): đặt `G2_DB_URL`, `G2_TODAY=2017-05-01`, chạy `python -m dashboard.ai_email.bootstrap --seed-g2-history`. App đặt `G2_SEED_HISTORY="0"` để cold start không tự seed; nếu chưa seed, sidebar ghi rõ "History up to yesterday was not prepared".

## 2. GitHub repository

Bản làm việc hiện là thư mục ZIP, **chưa có Git/remote**. Một thành viên có quyền quản lý:

```powershell
git init
git add .
git status          # kiểm tra KHÔNG có .streamlit/secrets.toml, .env, .n8n-local/, data/outputs/*.sqlite
git commit -m "AI email drafts via n8n Cloud"
git branch -M main
git remote add origin https://github.com/<to-chuc-hoac-user>/<repo>.git
git push -u origin main
```

Mọi file mà app cần đều nhỏ hơn 100 MB (lớn nhất `data/processed/bookings_prepared.csv` ~20 MB). Repo **private** được khuyến nghị; repo public mặc định tạo app public → phải tự chuyển app sang private (mục 5).

## 3. n8n Cloud

### 3.1 Credentials (Settings → Credentials, hoặc ngay trong node)
1. **Header Auth** — tên `G2 AI webhook token`, Name `X-G2-Webhook-Token`, Value = chuỗi ngẫu nhiên dài (cùng giá trị với secret `G2_AI_WEBHOOK_TOKEN`).
2. **Postgres** — user `g2_ai_n8n`, host/DB/port của mục 1, SSL **require**.
3. **Gmail OAuth2** — ưu tiên managed "Sign in with Google" ([hướng dẫn](https://docs.n8n.io/integrations/builtin/credentials/google/oauth-single-service)); đăng nhập bằng **tài khoản gửi thử** của nhóm. Người gửi và người nhận là hai cấu hình riêng.
4. Model: không cần credential nếu dùng **Gateway credits** (3.4).

### 3.2 Import
Workflows → Import from File: `n8n/g2_ai_email_draft_workflow.json` (A) và `n8n/g2_ai_email_send_workflow.json` (B). Workflow demo cũ `n8n/g2_approval_demo_workflow.json` không đổi và không cần cho bản cloud.

### 3.3 Gắn credential
- Node **Webhook** (A và B): Authentication = Header Auth → chọn credential 3.1.1.
- Mọi node **Postgres** (A: 2, B: 3): chọn credential 3.1.2. Mỗi node chỉ gọi **một** hàm (`claim_generation`, `complete_generation`, `claim_send_job`, `begin_dispatch`, `finish_dispatch`); không có BEGIN/COMMIT trải qua nhiều node.
- Node **Send test email (Gmail Send)**: chọn credential Gmail; **giữ Retry On Fail = tắt**, không chạy lại riêng node này (bỏ qua guard). Gửi bằng **Send**, không dùng Reply (phản hồi mẫu không có Gmail thread).
- `SENDER_REF` trong node "Validate send request" (mặc định `gmail:g2-team-test-sender`) phải bằng secret `G2_AI_SENDER_REF`. Đổi tài khoản gửi → đổi cả hai → mọi approval cũ cần duyệt lại.

### 3.4 Model (workflow A)
Node **OpenAI Chat Model** (sub-node của "Draft with AI (one call)"): ở ô credential chọn **Use Gateway credits** nếu model nằm trong "Included in n8n"; nếu không, chọn model khác trong danh sách đó hoặc "Use my own credential". Mặc định **`gpt-6-luna`** (OpenAI GPT-6 Luna), không đặt temperature và **không đặt Maximum Number of Tokens** (gpt-6-luna từ chối `max_tokens`, đã thấy trên n8n Cloud 21/10), JSON output, timeout 30 s, **maxRetries 0**: một lần gọi model cho một nháp, không vòng sửa lỗi. Nếu đổi model/nhà cung cấp, cập nhật `MODEL_ID` trong `n8n/ai_email/build_workflows.py` (nhãn ghi vào log) và chạy lại `python n8n/ai_email/build_workflows.py`. Không dùng tên coding agent làm model ID.

### 3.5 Publish
Bật Active/Publish từng workflow, mở node Webhook → tab **Production URL**, sao chép vào secrets `G2_AI_DRAFT_WEBHOOK_URL` / `G2_AI_SEND_WEBHOOK_URL`. Test URL (`/webhook-test/`) bị app từ chối. Không cần public API của n8n.

## 4. Streamlit Community Cloud

1. share.streamlit.io → Create app → repo/branch `main`, **Main file path `dashboard/app.py`**, Advanced settings → Python **3.13** (test local chạy trên 3.14; repo yêu cầu 3.11+; kiểm tra build log trên Cloud).
2. Cloud đọc `dashboard/requirements.txt` (cạnh entrypoint) — chỉ gói dashboard, không cài stack training; kiểm tra build log thấy file này. Theme ở `.streamlit/config.toml` (root).
3. Settings → **Secrets**: dán nội dung `.streamlit/secrets.toml.example` rồi thay giá trị thật. Bắt đầu với `G2_AI_REAL_EMAIL_ENABLED = "0"`.
4. App không chạy training, không cài n8n khi khởi động. App có thể **hibernate sau thời gian không có truy cập**; mở app và chạy smoke test trước buổi trình bày (cold start + đọc lại trạng thái từ DB, không tự gọi AI hay gửi).

## 5. Quyền truy cập
Settings → Sharing → **Only specific people** và mời email thành viên nhóm ([sharing](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app)); kiểm tra bằng cửa sổ ẩn danh rằng người ngoài không mở được. Tài khoản Community Cloud có giới hạn số private app — kiểm tra thực tế. Nếu không thể private: đặt `G2_AI_ACCESS_CODE` (cổng tối thiểu, không phải danh tính) hoặc `G2_AI_READ_ONLY = "1"` (public chỉ xem). Một workspace demo chung: mọi người được mời xem chung lịch sử; "Reviewer label" là nhãn demo, không phải danh tính đã xác minh; không có RBAC.

## 6. Gate triển khai sớm (smoke test) — trước khi dùng thật
1. App private mở được, mục "Connection status" ghi *shared PostgreSQL*, *n8n Cloud workflow A (live AI)*, *n8n workflow B*.
2. Tạo một nháp **Reminder** cho booking 27767 bằng "AI via n8n" → n8n Executions có 1 execution thành công; DB có request `succeeded`, `analysis_mode=live_ai`.
3. Duyệt **mock** → "Simulate send (mock)" → trạng thái *Simulated*; n8n Executions của B thành công.
4. Đặt `G2_AI_REAL_EMAIL_ENABLED = "1"`, reboot app; duyệt lại cho *Team's test inbox* → gửi **một** email; nhóm kiểm tra hộp thư thấy tiêu đề `[DEMO] …` → bấm "Mark email received".
5. Ghi vào mục 8: model/node/version, nhà cung cấp DB + kiểu kết nối, kết quả private sharing, ngày hết trial.

Nếu một bước chưa chạy được, **không** dùng fixture để báo "cloud đạt": ghi rõ bước nào chưa qua.

## 7. Kịch bản demo (5 phút)
1. Execution & follow-up → "AI email drafts (DEMO)", booking **27767** (G1 có reminder đến hạn 01/05/2017).
2. **Reminder** → Generate → xem nguồn (lịch G1, guard G1), nháp, khối dữ kiện, hash.
3. Đổi sang **Booking confirmation** → nháp cũ không còn hiện (stale), Generate không cần phản hồi khách.
4. **Reply to guest** → chọn mẫu "Có thể tôi sẽ hủy, để tôi hỏi lại gia đình." → *Unclear — Chưa xác nhận / cần hỏi lại*, không đổi booking; thử "Tôi muốn lùi ngày nhận phòng sang thứ Sáu." → ngày đề xuất để trống, hiện 2 cách hiểu, email hỏi lại.
5. Sửa câu chữ → revision 2 (approval cũ tự thu hồi) → duyệt → gửi tới hộp thư thử → "Shared history" có email type, revision, reviewer, trạng thái, provider ID.

## 8. Checklist cấu hình thực tế (điền khi làm)

| Mục | Giá trị / kết quả | Ngày, người kiểm |
|---|---|---|
| Ngày hết trial n8n (từ Admin Panel) | | |
| Gateway credits còn lại / model dùng | | |
| Nhà cung cấp PostgreSQL, kiểu kết nối, TLS on | | |
| URL Streamlit app (chỉ ghi khi đã deploy thật) | | |
| Private sharing đã kiểm bằng cửa sổ ẩn danh | | |
| Smoke test 6.2–6.4 | | |
| Reboot app → trạng thái còn nguyên | | |
| Hai tab bấm gửi cùng approval → 1 email | | |
| Export workflow JSON + `bootstrap --backup` trước hạn trial | | |

## 9. Xử lý sự cố

| Hiện tượng | Kiểm tra |
|---|---|
| "AI email store not available: AI schema g2_ai_v1 is missing" | Chạy `--apply-schema` (mục 1) |
| "n8n refused the webhook authentication" | Token secret khác credential Header Auth |
| "Webhook not found" | Workflow chưa publish hoặc dùng Test URL |
| Nháp "failed: AI call failed" | Hết Gateway credits / model không hỗ trợ / timeout; dùng "Demo fixture" (có nhãn) |
| Gửi ở trạng thái *Unknown* | Gmail có thể đã gửi: kiểm tra hộp thư rồi "Mark email received"; **không** gửi lại tự động; muốn gửi lần nữa phải tạo revision mới và duyệt lại |
| "Real test email requested but … staying on mock" | Thiếu workflow B URL / token / G2_AI_DB_URL |
| "AI workspace … is bound to run …" | Đổi `G2_AI_WORKSPACE_ID` khi đổi `G2_TODAY` hoặc F/G1 run |

## 10. Bản đồ file
- `dashboard/ai_email/` — contract, render, validate, dates, fixtures, store, client, config, service, panel, evaluate, bootstrap.
- `sql/g2_ai_email_postgres.sql`, `sql/g2_ai_email_roles_postgres.sql` — schema, trigger, hàm workflow, quyền.
- `n8n/ai_email/` — prompts (`prompts/*_v1.md`), fixtures, code node (`src/`), `build_workflows.py`; output `n8n/g2_ai_email_*_workflow.json`.
- `tests/test_ai_email_*.py`, `tests/fixtures/ai_email/` — test, bộ câu có nhãn (dev/test), case dữ kiện, vector validator.
