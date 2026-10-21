# Chạy thử n8n cùng Streamlit trên Windows

> **Cách nhanh nhất:** bấm đúp `run_demo.bat` ở thư mục gốc repo (macOS: `bash run_demo.sh`). Script tự cài n8n vào `.n8n-local/`, import + publish workflow, mở n8n và dashboard. Hướng dẫn cho người chấm: [`RUN_DEMO.md`](../RUN_DEMO.md). Các mục dưới đây là cách làm tay.

> **AI email drafts trên n8n Cloud** (workflow A soạn nháp, B gửi thử sau duyệt: `g2_ai_email_draft_workflow.json`, `g2_ai_email_send_workflow.json`, nguồn trong `ai_email/`): xem [`docs/README_DEPLOY_CLOUD.md`](../docs/README_DEPLOY_CLOUD.md). Workflow demo dưới đây giữ nguyên.

Workflow hiện tại là bản demo: Streamlit gửi một hành động → n8n kiểm tra → tạo bản xem trước email/nhiệm vụ/thông báo → trả kết quả. Chưa gửi email, gọi điện, đổi giá hoặc sửa tồn kho thật; chưa tự quét booking theo giờ.

## 1. Mở n8n

Cài Node.js 24.x trước. Mở PowerShell tại thư mục gốc repo (nơi có `README.md`) và chạy:

```powershell
$env:N8N_USER_FOLDER = Join-Path $PWD '.n8n-manual'
$env:N8N_LISTEN_ADDRESS = '127.0.0.1'
$env:N8N_PORT = '5678'
$env:N8N_RUNNERS_BROKER_PORT = '5679'
npx.cmd --yes n8n@2.43.8
```

Nếu được hỏi tải gói, nhập `y`. Đợi thông báo sẵn sàng rồi mở http://localhost:5678. Tạo tài khoản quản trị cục bộ nếu lần đầu được yêu cầu. Giữ cửa sổ PowerShell này mở.

Hướng dẫn ghim n8n **2.43.8**, phiên bản đã kiểm tra với project. Dữ liệu n8n riêng nằm trong `.n8n-manual/`; không đưa thư mục này lên Git.

## 2. Import workflow

Tạo workflow mới → menu **⋯** → **Import from File** → chọn:

```text
n8n\g2_approval_demo_workflow.json
```

Lưu workflow. Phải có 4 node nối nhau:

1. Webhook.
2. Approval & execution-time guard.
3. Mock dispatch (nothing sent).
4. Respond to Webhook.

Nếu đã import bản cũ, dùng JSON mới nhất để có kiểm tra I4 theo yêu cầu đổi cụ thể và các chốt chặn G1/G3.

## 3. Mở Streamlit có kết nối n8n

Dừng Streamlit cũ bằng `Ctrl+C` trong cửa sổ đang chạy nó. Mở **PowerShell thứ hai**, chạy:

```powershell
# Mở PowerShell tại thư mục gốc repo của bạn.
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
  python -m venv .venv
}
& ".\.venv\Scripts\python.exe" -m pip install -r requirements-g2.txt
$env:G2_N8N_WEBHOOK_URL="http://localhost:5678/webhook-test/g2-hotel-approval-demo"
& ".\.venv\Scripts\python.exe" -m streamlit run dashboard/app.py --server.address 127.0.0.1
```

Máy mới tải repo **chưa có `.venv`**. Cài Python 3.13 và bật Add to PATH; mở lại PowerShell nếu lệnh `python` chưa được nhận diện. Khối lệnh trên tạo môi trường riêng và cài thư viện. Những lần sau chỉ cần đặt URL và chạy lệnh Streamlit cuối cùng từ cùng cửa sổ. Giữ hai cửa sổ n8n và Streamlit mở. `.venv` không đưa lên Git.

Mặc định dashboard dùng nhật ký schema 9, tên kết thúc `_v9.sqlite`. Nếu bạn đã tự đặt `G2_DB_PATH` tới file schema cũ, đổi biến đó sang một tên file mới trước khi mở. Không cần chạy lại F/G1.

## 4. Chuẩn bị hành động

Trên Streamlit:

1. Dashboard mở ở hôm nay = **01/05/2017** (mặc định, không cần chọn ngày).
2. Vào **Execution & follow-up**, tìm `27767`: nhắc lịch của booking này đến hạn hôm nay và đã được duyệt sẵn trong lịch sử.
3. Chọn nhắc lịch đó; trạng thái phải là *Ready*.
4. (Muốn thử với cuộc gọi: tìm `78298`, cuộc gọi đến hạn hôm nay.)
5. Tích đủ ba ô kiểm tra, chọn outcome `no_response`.

Chọn hành động chưa thực thi. Để thử đường n8n, dùng nút **Send to automation (n8n)** ở bước tiếp theo; ghi nhận bằng **Record execution (staff)** trước sẽ làm hành động không được gửi lần nữa.

## 5. Nhận và gửi thử

Trong n8n, mở node **Webhook**, chọn **Test URL**. URL phải trùng cấu hình ở bước 3; nếu khác, sao chép URL thực tế và khởi động lại Streamlit với giá trị đó.

Bấm **Listen for Test Event**, rồi quay ngay lại Streamlit bấm **Send to automation (n8n)**. Nếu hết thời gian chờ, bấm nghe lại trước lần gửi tiếp theo.

Kết quả chấp nhận của demo:

```json
{
  "accepted": true,
  "status": "DEMO_ACCEPTED_NO_ACTION",
  "executed": false
}
```

`executed: false` là đúng: workflow chỉ mô phỏng. Streamlit thêm `intervention_id` và ghi nhật ký mô phỏng sau khi nhận `accepted: true`. Cuộc gọi tạo mô tả nhiệm vụ cho nhân viên, không tự gọi khách.

## 6. Chạy không cần giao diện (CLI, đã kiểm chứng 20/10)

Cách này cài n8n vào một thư mục riêng, import và publish workflow bằng lệnh, nghe trên `127.0.0.1` (không mở ra mạng). Cổng 5690 cho n8n, 5691 cho task broker (task broker mặc định dùng 5679, trùng nếu đặt n8n ở 5679).

```powershell
# Mở PowerShell tại thư mục gốc repo. Dừng n8n trước khi import/publish.
$R = Join-Path $PWD '.n8n-local'
New-Item -ItemType Directory -Force $R | Out-Null
npm.cmd install --prefix $R n8n@2.43.8
$env:N8N_USER_FOLDER = "$R\data"; $env:N8N_PORT = "5690"; $env:N8N_RUNNERS_BROKER_PORT = "5691"
$env:N8N_LISTEN_ADDRESS = "127.0.0.1"; $env:N8N_DIAGNOSTICS_ENABLED = "false"
& "$R\node_modules\.bin\n8n.cmd" import:workflow --input="n8n\g2_approval_demo_workflow.json"
& "$R\node_modules\.bin\n8n.cmd" list:workflow
$workflowId = Read-Host "Nhap ID workflow vua in o tren (phan truoc dau |)"
& "$R\node_modules\.bin\n8n.cmd" publish:workflow --id=$workflowId
& "$R\node_modules\.bin\n8n.cmd" start
```

Production URL khi đó là `http://127.0.0.1:5690/webhook/g2-hotel-approval-demo` (không cần bấm Listen). Ở cửa sổ thứ hai, đặt `G2_N8N_WEBHOOK_URL` bằng URL này rồi mở Streamlit như mục 3. Kiểm tra tự động:

```powershell
$env:G2_TEST_N8N_URL = "http://127.0.0.1:5690/webhook/g2-hotel-approval-demo"
& ".\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_g2_n8n_live.py" -v
```

Muốn lưu nhật ký vào PostgreSQL thay vì SQLite: đặt thêm `G2_DB_URL=postgresql://user@host:port/db` trước khi mở Streamlit (cần `psycopg[binary]`, đã ghi trong `requirements-g2.txt`).

## Khi gặp lỗi

| Hiện tượng | Cách kiểm tra |
|---|---|
| Nút gửi n8n bị mờ | Mở lại Streamlit từ đúng PowerShell đã đặt `G2_N8N_WEBHOOK_URL`. |
| Webhook không tìm thấy / 404 | Chọn Test URL và bấm **Listen for Test Event** ngay trước khi gửi. |
| Không kết nối được | Kiểm tra cửa sổ n8n còn chạy và mở được http://localhost:5678 (hoặc 5690 nếu chạy theo mục 6). |
| Production URL trả 404 sau khi khởi động lại | Workflow chưa ở trạng thái published: dừng n8n, chạy lại `publish:workflow --id=<id>` (mục 6), rồi `start`. |
| n8n báo cổng 5679 đang dùng | Task broker mặc định dùng 5679; đặt `N8N_RUNNERS_BROKER_PORT` khác cổng n8n như mục 6. |
| `Not due yet` | Chọn action đến hạn hôm nay (ví dụ nhắc lịch của `27767`); ngày hôm nay đặt bằng `G2_TODAY` trước khi khởi động. |
| Hết cửa sổ gọi | Dùng ngày hợp lệ và hành động chưa thực thi; không sửa ngày đánh giá gốc để né chính sách. |
| Đã thực thi | Chọn một hành động mới; nếu muốn phát lại từ đầu, dùng file nhật ký demo mới qua `G2_DB_PATH`, giữ lại file cũ. |
| Schema cũ | Dùng nhật ký schema 9 mới; không đổi tên file cũ thành `_v9` vì cấu trúc bên trong vẫn cũ. |
| Báo `database is locked` | Một phiên khác đang ghi (ví dụ đang chờ n8n trả lời); đợi vài giây rồi bấm lại. Khoá chờ tối đa 15 giây. |
| Cuộc gọi quá cửa sổ G1 | Đóng cuộc gọi bằng **Close without contact** ở trang Execution & follow-up hoặc Action Review (ghi người đóng, lý do); booking được mở cho reminder thay thế. |

## Trạng thái và nguồn

Code node và luồng gửi/ghi nhật ký đã được kiểm thử cục bộ, và đã chạy hết luồng trên n8n 2.43.8 thật (20/10, cách ở mục 6): cuộc gọi I3 của booking 22631 ngày 03/01/2017 được chấp nhận và ghi `executed_by = n8n`; action `price_adjustment` đang chờ duyệt và overbooking đều bị guard của n8n từ chối. Không cần Production URL cho bài demo Test URL ở trên.

- [n8n: chạy bằng npm/npx](https://docs.n8n.io/hosting/installation/npm/)
- [n8n: import workflow](https://docs.n8n.io/workflows/export-import/)
- [n8n: webhook khi thử workflow](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/workflow-development/)
