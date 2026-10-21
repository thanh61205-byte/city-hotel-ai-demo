# Hướng dẫn tải và chạy demo — Streamlit + n8n

Dành cho thành viên nhóm và người chấm tải repo về máy. **Không cần chạy lại notebook hoặc huấn luyện model:** repo đã có kết quả F và policy G1. Dashboard dùng SQLite mặc định; không cần cài PostgreSQL để demo.

Streamlit là giao diện xem booking, lịch hành động và duyệt. n8n nhận yêu cầu từ giao diện, kiểm tra điều kiện rồi trả bản mô phỏng. **Không gửi email, không gọi khách và không thay đổi hệ thống khách sạn.**

## 1. Chuẩn bị máy

| Công cụ | Phiên bản dùng cho hướng dẫn | Cần khi nào? |
|---|---|---|
| [Python](https://www.python.org/downloads/) | 3.13; dashboard hỗ trợ 3.11+ | Luôn cần. Windows: bật **Add python.exe to PATH** khi cài |
| [Node.js](https://nodejs.org/) | 24.x | Chỉ cần khi dùng n8n; bản n8n của project là **2.43.8** |
| Internet | Để tải thư viện | Lần cài đầu; launcher cũng kiểm tra thư viện mỗi lần mở |
| Git | Tùy chọn | Có thể tải ZIP thay vì clone |

Sau khi cài Python/Node, mở lại terminal. Nếu chỉ muốn xem dashboard, có thể bỏ qua Node.js và n8n.

## 2. Tải repo và mở đúng thư mục

Trên GitHub: **Code → Download ZIP**, rồi giải nén hoàn toàn. Không chạy file ngay bên trong ZIP. Hoặc dùng `git clone` với URL repo của nhóm.

Mở thư mục chứa `README.md`, `requirements-g2.txt`, `run_demo.bat`, `dashboard/` và `n8n/`. Đó là **thư mục gốc repo** trong mọi hướng dẫn bên dưới. Trên Windows có thể bấm phải khoảng trống trong thư mục → **Open in Terminal**.

Bản tải về phải có `artifacts/modeling/`, `artifacts/business_case/` và `data/processed/`. Không chỉ tải riêng `dashboard/`. `.venv` và dữ liệu n8n không đi cùng repo; máy bạn sẽ tự tạo chúng.

## 3. Chạy nhanh

### Windows

- **Streamlit + n8n:** bấm đúp `run_demo.bat`.
- **Chỉ Streamlit:** bấm đúp `dashboard/run_windows.bat`, hoặc mở PowerShell tại thư mục gốc và chạy:

```powershell
.\run_demo.bat -NoN8n
```

Script tạo `.venv`, cài `requirements-g2.txt` và mở Streamlit. Khi dùng n8n, script còn cài n8n 2.43.8 trong `.n8n-local/`, import và publish workflow của repo. Lần đầu có thể mất nhiều phút, tùy mạng và máy; n8n có nhiều thư viện phụ thuộc.

Nếu không tìm thấy Node.js phù hợp, script mở dashboard riêng. Nếu cài n8n bị lỗi, dùng lệnh `-NoN8n` ở trên để xem dashboard trước.

### macOS / Linux

Mở terminal tại thư mục gốc:

```bash
bash run_demo.sh              # Streamlit + n8n
# Hoặc chỉ chạy giao diện:
bash run_demo.sh --no-n8n
```

Cần có `python3`, `curl`, và Node.js/npm nếu dùng n8n. Linux có thể cần cài gói `python3-venv` của hệ điều hành. Luồng shell được cung cấp để chạy tương đương; chưa kiểm thử đầu-cuối trên macOS/Linux trong môi trường Windows của nhóm.

### Địa chỉ và cách dừng

| Thành phần | Địa chỉ |
|---|---|
| Streamlit | http://127.0.0.1:8501 |
| n8n | http://127.0.0.1:5690 |
| Webhook đã publish | `http://127.0.0.1:5690/webhook/g2-hotel-approval-demo` |

Nếu trình duyệt không tự mở, nhập địa chỉ Streamlit. Trên Windows, giữ cửa sổ n8n mở; dừng Streamlit bằng `Ctrl+C`, dừng n8n bằng `Ctrl+C` trong cửa sổ của nó. Trên macOS/Linux, launcher dừng tiến trình n8n do chính nó khởi chạy khi thoát. Nếu dùng n8n đã chạy sẵn, dừng ở terminal đã mở nó.

Hai dịch vụ chỉ nghe trên máy cá nhân. Dashboard chưa có đăng nhập. Không cần tạo n8n Cloud, mua API key, cấu hình SMTP hoặc thêm thông tin khách thật cho demo này.

## 4. Demo trong 5 phút

Dashboard luôn mở ở **hôm nay = thứ Hai 01/05/2017** (đổi được bằng biến `G2_TODAY` trước khi khởi động, xem mục 6). Lần đầu mở một nhật ký mới, dashboard dựng sẵn lịch sử làm việc tới hôm qua, có thanh tiến độ (khoảng 10–20 giây): các tuần trước đã được duyệt và thực hiện. Với lô model chấm hôm qua (Chủ nhật 30/04, 441 booking), danh sách gọi điện đã được duyệt và gọi ngay hôm qua (G1 xếp cuộc gọi từ ngày chấm điểm, mỗi ngày tối đa 20), còn email và nhắc lịch để lại chờ duyệt hôm nay. Dải **Today** trên cùng cho biết ngày, lần chấm điểm gần nhất, số cuộc gọi đã ghi trong ngày trên 20, số hành động lên lịch cho hôm nay (75) và số hành động chờ duyệt (1). Menu bên trái theo đúng luồng làm việc: **Daily work** (Overview → Risk Queue → Action Review → Execution & follow-up), **Planning & analysis**, **Data**. Trang Overview mở đầu bằng khung hướng dẫn 4 bước; ẩn đi được và mở lại bằng nút **Show the guide** ở sidebar.

1. **Overview:** khung *Yesterday (Sun 30 Apr)*: 441 booking mới được chấm, 65 rủi ro cao, 63 đề xuất gọi điện đã duyệt, 54 liên hệ đã làm, 9 khách xác nhận; 37 booking tới ngày đến, 8 hủy (22%), model đã xếp 6/8 vào mức cao hoặc trung bình. Chọn *Last 7 days* để xem cả tuần (365 liên hệ, 109 khách xác nhận, 371 booking tới ngày đến, 81 hủy, model xếp 74/81). Khung *Today*: *Awaiting a decision* = 362 (361 đề xuất email/nhắc lịch + 1 đề xuất giữ phòng), *Due today* = 62 (20 cuộc gọi, 42 nhắc lịch). Bấm **View Risk Queue**.
2. **Risk Queue:** hiện mọi booking đang mở (5.129). Lọc *Review status = Awaiting review* (370) và *Risk level = Medium* (196 booking), tích **Select all eligible (196)**, nhập tên ở *Your name (reviewer)*, bấm **Approve 196 recommendations…**, đọc tóm tắt rồi **Confirm approval**: *196 recommendations approved, 367 contacts scheduled.* Bấm lại không tạo trùng vì các booking này đã được duyệt.
3. **Action Review:** mở booking low `73859` và **Reject recommendation…** kèm lý do. Mở `77744`: cuộc gọi ngày 25/04 ghi *Guest asked to change*; mở **Propose another action**, chọn *Change offer* rồi **Propose action**: đề xuất chuyển vào *Pending approvals*.
4. **Execution & follow-up:** *Due now* = 124: 20 cuộc gọi, 93 nhắc lịch, 11 email xác nhận (email và nhắc lịch của lô hôm qua lẽ ra gửi từ 30/04 nên đến hạn luôn hôm nay; automation không giới hạn theo ngày). Tìm `78298`, thử **Record execution (staff)** khi chưa tích ba ô kiểm để thấy bị chặn; tích đủ ba ô, chọn *No answer*. *Calls left* còn 19, lịch sử booking ghi *Call guest recorded: No answer*.
5. Nếu có n8n: tìm `27767` (nhắc lịch đến hạn hôm nay), bấm **Send to automation (n8n)** thay cho **Record execution (staff)**. Kết quả đúng là `accepted: true`, `executed: false` và có `intervention_id`; action chuyển sang **Sent**. Nếu n8n từ chối hoặc không kết nối được, action hiện **Failed** kèm nút **Retry via n8n**. Không bấm cả hai cho cùng action.
6. **Action Review → Pending approvals (2):** *Hold rooms back* cho `2017-05-30 | A` (đề xuất từ hôm qua) và *Change offer* của `77744`. Nhập người duyệt (ví dụ *Revenue manager*) và bấm **Approve selected action** cho từng cái. G3 overbooking luôn bị tắt.
7. **Performance** (vận hành từ đầu năm tới nay; kết quả hủy của booking đã đến, chỉ mang tính mô tả), **Model monitoring** (4.315 booking đã đến trước hôm nay; ROC-AUC 0,740, PR-AUC 0,412, Brier 0,156, top 20%: precision 40,3%, lift 1,92; tự tính lại, không dùng số của notebook vì số đó có dữ liệu sau hôm nay) và **Data scope**.

Gửi lại action đã thực thi sẽ bị từ chối. Mỗi giá trị “hôm nay” có nhật ký riêng; muốn làm lại từ đầu thì dừng Streamlit và xóa file nhật ký của ngày đó (mục 6), lần mở sau sẽ dựng lại đúng lịch sử cũ. Các ô xác nhận khi ghi liên hệ cần thiết vì dữ liệu không có consent hay trạng thái PMS.

## 5. Chạy Streamlit thủ công và nối n8n

Nếu không dùng launcher, mở PowerShell tại thư mục gốc:

```powershell
# Chỉ tạo môi trường khi chưa có.
if (-not (Test-Path '.\.venv\Scripts\python.exe')) {
    python -m venv .venv
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-g2.txt
& '.\.venv\Scripts\python.exe' -m streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501
```

Không cần activate môi trường và không cần cài `requirements.txt` chỉ để mở dashboard.

Khi n8n đã chạy và workflow đã publish, dừng Streamlit rồi mở lại từ **cùng PowerShell đã đặt biến**:

```powershell
$env:G2_N8N_WEBHOOK_URL = 'http://127.0.0.1:5690/webhook/g2-hotel-approval-demo'
& '.\.venv\Scripts\python.exe' -m streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501
```

Cách import bằng giao diện, phân biệt Test URL/Production URL và cài n8n thủ công: [n8n/README_VI.md](n8n/README_VI.md). Với Production URL đã publish không cần bấm **Listen for Test Event**. Với Test URL thì cần bấm Listen trước khi gửi.

## 6. Nhật ký và cấu hình

| Cấu hình | Ý nghĩa |
|---|---|
| Không đặt biến | Hôm nay = 01/05/2017; SQLite tự tạo trong `data/outputs/`, một file theo F run + policy G1 + ngày hôm nay + schema 9, có sẵn lịch sử tới hôm qua |
| `G2_TODAY` | Ngày làm việc dashboard mở, dạng `YYYY-MM-DD` (mặc định `2017-05-01`); phải nằm trong giai đoạn đã chấm điểm (01/01–27/08/2017). Mỗi ngày dùng một file nhật ký riêng |
| `G2_SEED_HISTORY` | `0`: nhật ký mới bắt đầu trống, không dựng lịch sử (dùng cho test) |
| `G2_N8N_WEBHOOK_URL` | URL n8n; thiếu biến thì nút gửi n8n bị mờ |
| `G2_DB_PATH` | Chọn file SQLite khác, ví dụ để bắt đầu lượt demo mới (file mới sẽ được dựng lịch sử tới hôm qua) |
| `G2_DB_URL` | Tùy chọn PostgreSQL có sẵn; ưu tiên hơn `G2_DB_PATH` |

Project đọc biến môi trường từ terminal; **không tự đọc file `.env`**.

Để bắt đầu nhật ký mới mà giữ log cũ: dừng Streamlit, mở PowerShell tại gốc repo rồi chạy:

```powershell
# Đảm bảo dùng SQLite cho lượt demo mới.
Remove-Item Env:G2_DB_URL -ErrorAction SilentlyContinue
$env:G2_DB_PATH = Join-Path $PWD ('data/outputs/demo_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '_v9.sqlite')
.\run_demo.bat
```

Dùng `-NoN8n` nếu chỉ cần Streamlit. Mỗi người mặc định có nhật ký riêng trên máy; clone cùng repo không làm nhật ký tự đồng bộ. Hướng dẫn PostgreSQL tùy chọn nằm ở [dashboard/README.md](dashboard/README.md).

## 7. Lỗi thường gặp

| Lỗi | Cách xử lý |
|---|---|
| `python`/`py` không được nhận diện | Cài Python, bật PATH rồi mở terminal mới; kiểm tra `python --version` |
| `No module named streamlit` | Cài `requirements-g2.txt` bằng đúng `.venv` như mục 5 |
| Lỗi cài n8n / đường dẫn Windows quá dài | Dùng thư mục giải nén ngắn, ví dụ `C:\Projects\CityHotel`; chạy dashboard riêng trước nếu cần |
| `ENOTEMPTY ... rmdir` khi cài n8n | Bản cài thư viện chưa hoàn tất hoặc đang bị tiến trình khác sử dụng. Dừng các cửa sổ đang cài cùng repo bằng `Ctrl+C`; sau khi mọi tiến trình cài đã dừng, xóa **chỉ** `.n8n-local/node_modules` bằng File Explorer rồi chạy lại `run_demo.bat`. Giữ `.n8n-local/data` vì đó là tài khoản/workflow. Không chạy hai lần cài cùng lúc |
| `Another launcher is already running` | Dùng cửa sổ đang chạy hoặc dừng nó trước. File `.demo-run.lock` có thể còn trên đĩa sau khi thoát; không cần xóa vì khóa được hệ điều hành giải phóng khi tiến trình kết thúc |
| Nút gửi n8n bị mờ | Chạy launcher có n8n hoặc đặt URL trong cùng terminal mở Streamlit |
| `Connection refused` | n8n chưa sẵn sàng hoặc đã dừng; kiểm tra địa chỉ/cổng |
| Webhook trả 404 | Kiểm tra workflow đã publish và đúng URL; Test URL cần Listen. Chỉ có trang n8n mở được chưa chứng minh workflow tồn tại |
| `Not due yet` / hết cửa sổ gọi | Action lên lịch sau hôm nay chưa làm được; cuộc gọi chỉ làm trong 7 ngày từ ngày chấm điểm. Muốn ngày khác thì khởi động lại với `G2_TODAY` |
| Trang báo đang dựng lịch sử ở cửa sổ khác | Một phiên khác đang dựng; đợi rồi tải lại. Nếu máy tắt giữa chừng, dừng Streamlit, xóa file nhật ký của ngày đó rồi mở lại |
| Booking không có trong Execution & follow-up | Đề xuất của booking chưa được duyệt: duyệt ở **Risk Queue** hoặc **Action Review** trước |
| Lỗi lạ sau khi cập nhật code (ví dụ `unexpected keyword argument`) | Streamlit vẫn giữ code cũ trong bộ nhớ: dừng bằng `Ctrl+C` rồi chạy lại launcher |
| Action đã thực thi | Chọn action khác hoặc tạo nhật ký demo mới theo mục 6 |
| Schema cũ | Dùng file mới; đổi tên file cũ thành `_v9` không nâng cấp được cấu trúc |
| F/G1 không khớp | Tải đủ artifacts cùng bản repo. Nếu tự chạy lại F thì phải chạy lại G1; không trộn kết quả các phiên bản |
| Cổng 8501/5690/5691 bận | Dừng đúng bản demo đang chạy; không mở thêm nhiều launcher |
| `database is locked` | Phiên khác đang ghi/chờ n8n; đợi rồi thử lại |

Sau khi cập nhật JSON workflow từ repo, launcher có thể vẫn dùng workflow đã import trước đó. Import lại bản mới trong n8n và publish, rồi khởi động lại n8n. Giữ đúng dữ liệu n8n đang dùng (`.n8n-local/data` với launcher).

Launcher Windows kiểm tra mã thoát npm và phiên bản n8n sau cài, đồng thời ngăn hai launcher của cùng repo chạy song song. Lần cài đầu hiển thị tiến độ tải gói; thời gian phụ thuộc mạng, không có mốc vài phút bảo đảm. Launcher đợi n8n báo sẵn sàng sau khi khởi động rồi mới mở dashboard; trong lúc chờ, không mở thêm launcher.

## 8. Kiểm tra kết nối

Khi n8n đang chạy, tại thư mục gốc repo:

```powershell
$env:G2_TEST_N8N_URL = 'http://127.0.0.1:5690/webhook/g2-hotel-approval-demo'
& '.\.venv\Scripts\python.exe' -B -m unittest discover -s tests -p 'test_g2_n8n_live.py' -v
```

Mong đợi **2 test đạt**; kết quả `skipped` không chứng minh đã kết nối n8n. Bộ G2 chạy bằng `-p 'test_g2*.py'`. Toàn bộ suite còn cần thư viện mô hình trong `requirements.txt`.

## 9. Khi đưa lên repo

Giữ launcher, hướng dẫn này, `requirements-g2.txt`, workflow JSON, mã nguồn và dữ liệu/artifacts đi kèm. `.gitignore` đã loại `.venv/`, `.n8n-local/`, `.n8n-manual/`, file SQLite và báo cáo audit nội bộ. Không chia sẻ môi trường cài sẵn, tài khoản n8n hoặc mật khẩu PostgreSQL.

Bản hướng dẫn dùng n8n 2.43.8 đã kiểm tra locally. Tài liệu chính thức có thể mô tả phiên bản khác: [cài bằng npm](https://docs.n8n.io/hosting/installation/npm/), [import/publish qua CLI](https://docs.n8n.io/hosting/cli-commands/).
