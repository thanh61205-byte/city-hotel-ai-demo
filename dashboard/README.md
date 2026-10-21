# G2 dashboard — cancellation decision support (prototype)

The dashboard works on one fixed day, **today = Monday 1 May 2017** (`G2_TODAY`). The weeks before it have already been handled by the team: that history is prepared once, when a new action log is opened, through the same service and guards as a click (`seed_history.py`, below). Of yesterday's batch (Sunday 30 April, 441 bookings), the calls were approved and made yesterday; its emails and reminders wait for today's review.

Streamlit website on the latest stage-F run (`artifacts/modeling/`, v3.1, XGBoost) **and the G1 policy built on it** (`artifacts/business_case/`, notebook 05). The daily flow is: see the situation (Overview) → filter and pick bookings (Risk Queue) → review recommendations, approve them in a batch or change one (Action Review) → carry out what is due and follow up on automation (Execution & follow-up) → judge the results (Performance). It reads both stages' files directly, so re-running notebook 04 and then 05 updates it. Design: `docs/G2_architecture.md`; fields: `docs/G2_data_contract.md`.

## Run (from the repo root)
- Dashboard + local n8n in one step: `run_demo.bat` (Windows) or `bash run_demo.sh` (macOS / Linux); see [RUN_DEMO.md](../RUN_DEMO.md).
- Windows: double-click `dashboard/run_windows.bat`, or
- `python -m pip install -r requirements-g2.txt` then `python -m streamlit run dashboard/app.py`, and open <http://localhost:8501>.

The dashboard only needs `requirements-g2.txt` (Streamlit, pandas, Altair, NumPy, requests), not the modelling stack in `requirements.txt`. The theme (colours, Public Sans font, no Deploy button) is in `.streamlit/config.toml`, read when Streamlit is launched from the repo root as the launchers do; elsewhere the app works with Streamlit's default look. Tests: `python -m unittest discover -s tests -v`.

**Order of stages.** After re-running notebook 04, re-run notebook 05 too (`python src/run_notebooks.py --stage G1`): the dashboard refuses a G1 policy built on another F run.

**Action log** is saved in `data/outputs/g2_demo_state_<F run>+<G1 policy id>_<today YYYYMMDD>_v<schema>.sqlite`: one file per F run, G1 policy, today's date and schema, so a new run, policy or day never inherits old actions, and old files are left untouched. A new file first gets the history up to yesterday (below); delete the file to start that day again. The G1 daily call capacity is fixed per file. If `G2_DB_PATH` is set explicitly, point it to a new file when the run, policy or schema changes. When `artifacts/modeling/` or `artifacts/business_case/` change, the next interaction reloads them (or click **Reload F / G1 outputs** on the Data scope page).

**AI email drafts (DEMO).** The Execution & follow-up page ends with an *AI email drafts* section: pick a booking and Reminder / Booking confirmation / Reply to guest, read the source data, generate a draft (n8n Cloud, or a labelled offline preview), edit, approve, send a DEMO test (mock or the team's test inbox) and see the shared log. It never writes the action log (no execution, response or KPI). Code: `dashboard/ai_email/`; deployment: `docs/README_DEPLOY_CLOUD.md`.

## Layout
`app.py` is the entry point: it loads F / G1, reads today (`G2_TODAY`, default 2017-05-01; there is no date picker), opens this session's store, builds its history once if the log is new, draws the **Today** band (today, latest risk scores, calls logged out of the daily limit, actions planned for the day, actions waiting for approval), then runs the selected page. Pages live in `dashboard/views/` (not `pages/`, which Streamlit would scan on its own). Each page says what it is for; Overview opens with a short guide (hide it, and bring it back from the sidebar).

| Group | Page | What it is for | Approves? |
|---|---|---|---|
| Daily work | **Overview** (default) | Looking back (*Yesterday* / *Last 7 days*): bookings scored, high risk, approved, contacts made, guests confirmed, arrivals, cancellations and how many of them the model had flagged. Then today: decisions waiting, due today, high-risk arrivals, arrivals in 3 days; recent activity; where to go next | No, only navigates |
| | Risk Queue | Open bookings today: search, filter (check-in window, risk level, review and execution status, dates), sort, pick rows or "Select all eligible", **bulk approval** with a summary and a confirmation | Routine recommendations (I1–I3), in a batch |
| | Action Review | One booking: summary, risk ranking, recommendation, its actions; **Approve & schedule**, **Reject** (with a reason), **Change this action** (replace it), propose another action. **Pending approvals:** sensitive actions one by one | Yes, case by case |
| | Execution & follow-up | Due, failed, scheduled, sent, completed or closed actions with what each needs; record results and guest answers; automation attempts and **Retry**; one booking's history; workload | No (records results) |
| Planning & analysis | Capacity planning | Expected cancellations per stay date × room type (group blocks, I5), dates to watch; propose room / price actions | Proposes only; approved in Action Review |
| | Performance | Operations from the action log; actual cancellations of bookings already arrived (descriptive only) | No |
| | Model monitoring | ROC-AUC, PR-AUC, Brier, precision / recall / lift at the top 20% and deciles, recomputed on bookings arrived before today with the score of their weekly batch (notebook 04's figures include later arrivals, so they are not used). No model-vs-rule table: the data has no rule scores | No |
| Data | Data scope | Sources and when they were written, coverage, fields present and missing, load checks, reload; no upload or re-scoring | No |

**Two statuses, never merged.** *Review status* says whether staff decided on the recommendation (Awaiting review → Approved / Rejected; or Decide manually / No contact planned / Group block). *Execution status* says how far an action got (Scheduled, Due, Sent (by automation), Completed (by staff), Failed, Closed, Awaiting approval, Rejected). Nothing shows as sent or completed without a logged execution.

Shared code: `common.py` (loaders, page context, history build with progress bar, tables with readable headers, reviewer name), `workflow.py` (review and execution status, batch eligibility, look-back counts, activity), `action_panel.py` (approve, readiness, execute, response, close, automation attempts for one action), `vocab.py` (plain names for codes and plain-language refusals), `ui.py` (colour tokens, Today band), `model_view.py` (monitoring metrics on arrived bookings), `seed_history.py` (history up to yesterday). Every write still goes through `state_store.py`.

## History up to yesterday (`seed_history.py`)
Built once, when the log is new and empty (about 10–20 s on a laptop, with a progress bar; a session opening meanwhile is asked to wait). Every step goes through `state_store`, so every guard runs.
- **Review:** every routine recommendation (I1–I3) of the batches up to 23 April is approved by *Reservations team* on its scoring day. Of the batch scored yesterday (30 April), only the calls (I3) were approved: G1 places calls from the scoring day on, at most 20 a day, so a call list left for the next day would put 40 calls on one day. Its emails and reminders (I1, I2) wait for today.
- **Execution:** every approved contact due before today is done on its due date: emails and reminders through automation (status *Sent*), calls by staff (*Completed*), at most 20 a day. A follow-up no longer needed (the guest answered or cancelled) is closed without contact on the day of the answer. With today = 1 May: 9,039 recommendations approved, 1,332 calls, 8,876 emails and reminders, 1,027 follow-ups closed, 1,690 email replies.
- **Guest answers:** the data has none, so they are derived from each booking's real outcome and never contradict it. A guest who later cancelled never confirms; 35% of them cancel during the call. A guest who kept the booking confirms on 50% of calls and asks to change on 10%, otherwise does not answer; 25% confirm one or two days after an email or reminder. Answers are fixed per booking reference, so a rebuild gives the same history.
- **Rooms and prices:** each scoring day the *Revenue manager* holds back rooms on the stay date × room type with the most expected cancellations (proposed, approved, carried out). One more proposal from yesterday (`2017-05-30 | A`) waits for approval.

The real outcome (`is_canceled`) is read only to keep these answers consistent, and to show cancellations of bookings that arrived before today (Overview, Performance, Model monitoring). It never reaches the queue, the schedule or a recommendation.

## What is real and what is prepared
The pages do not repeat this on every screen, so it is stated here:
- **Real historical data:** City Hotel booking attributes and outcomes (public research dataset, 2017 arrivals); the model's scores, tiers and rules, and the G1 schedule.
- **Prepared, not observed:** today's date (a fixed day in 2017), the history up to yesterday with every guest answer in it (derived from the real outcome as above), and the reviewer names (*Reservations team*, *Revenue manager*).
- **Synthetic:** net contribution (`syn_net_*`): costs and commissions.
- **Not in the data:** consent / opt-out and other open actions, confirmed by staff when they record a contact.
- **Nothing leaves the computer:** the action log, approvals, executions and the n8n webhook are local; no guest is contacted and no PMS, price or inventory is changed. *Sent* means accepted by the local n8n workflow, which returns a preview only.
- **No effect is claimed:** contacts went to the riskiest bookings and there is no control group, so no revenue protected or cancellation prevented is shown; that needs the pilot (`docs/Pilot_evaluation_plan.md`).
- **Approval time (KPI P3)** is not shown: the log records single-action approvals on the computer's clock (2026), not on the working day, so the figure means nothing while today is in 2017. It stays in `docs/kpi_register.csv` and `state_store.approval_kpis`.

## What G1 changes in G2
- **Tiers:** call tier = top 15% of each weekly batch by p_cancel × gross value, reminder tier = next by p_cancel up to 60% (F's 5% / 20% shown as `f_risk_tier`).
- **Calls:** placed by G1 within 7 days of the decision, at most 20 a day and at least 8 days before arrival, so many are dated after the decision day. A call with no free slot is **manual review** (*Decide manually*): no automatic action; staff may propose one from Action Review.
- **Review:** routine plans (I1 confirmation and reminder, I2 reminders, I3 call) enter the action log only when a reviewer approves them, alone or in a batch (`plan_reviews`, one final decision per booking). A rejected plan cannot be imported afterwards. Sensitive actions (I4, I6, date × room-type actions) keep their own pending → approved / rejected decision, one at a time.
- **Execution:** at most 20 calls can be logged per business day. Calls must still be within offsets 0–6 from the original F score date and at least 8 days before arrival when executed, including manual calls. Expired calls are blocked for manual review before any n8n request.
- **G3:** overbooking is unavailable in the form and blocked by both the service and n8n, even with approval.
- **Checks at load:** G1 must name the same F run; G2 re-derives every G1 tier from F's scores and the frozen policy, and checks every dated action (I1 / I2 milestones, I3 window and capacity). Anything else stops the app with the reason.

## A 5-minute demo script
Start with a new action log (the default file for the day, or a new `G2_DB_PATH`) and today = 01/05/2017 (the default). Figures below were checked on the current F / G1 outputs.
1. **Overview:** read the guide. *Yesterday (Sun 30 Apr)*: 441 new bookings scored, 65 high risk, 63 call recommendations approved, 54 contacts made, 9 guests confirmed, 37 arrival dates reached, 8 cancelled (22%), 6 of 8 flagged by the model. *Last 7 days*: 365 contacts, 109 guests confirmed, 371 arrivals, 81 cancelled, 74 flagged. *Today*: *Awaiting a decision* 362 (361 email / reminder recommendations + 1 room proposal), *Due today* 62 (20 calls, 42 reminders). Click **View Risk Queue**.
2. **Risk Queue:** every open booking (5,129). Filter *Review status = Awaiting review* (370) and *Risk level = Medium* (196), tick **Select all eligible (196)**, enter your name, click **Approve 196 recommendations…**, read the summary and **Confirm approval**: *196 recommendations approved, 367 contacts scheduled.* A second click approves nothing: they are no longer eligible.
3. **Action Review:** open the low-risk booking `73859` and **Reject recommendation…** with a reason. Open `77744`: its call on 25/04 recorded *Guest asked to change*; under **Propose another action** choose *Change offer* and **Propose action**: it waits under Pending approvals.
4. **Execution & follow-up:** *Due now* 124: 20 calls, 93 reminders and 11 confirmation emails (the emails and reminders of yesterday's batch were planned from 30/04, so they are due at once; automation has no daily limit). Search `78298`, try **Record execution (staff)** without the three checks (*Tick the three checks before saving…*), tick them and record *No answer*: *Calls left* 19, and the booking history shows *Call guest recorded: No answer*. With n8n running, open the reminder of booking `27767` (due today) and **Send to automation (n8n)**: it becomes **Sent**; a refused or failed hand-over shows as **Failed** with **Retry via n8n**.
5. **Action Review → Pending approvals (2):** *Hold rooms back* for `2017-05-30 | A` (proposed yesterday) and the change offer of `77744`; enter a reviewer (e.g. *Revenue manager*) and **Approve selected action** for each.
6. **Performance** (operations so far this year; cancellations of arrived bookings, descriptive only), **Model monitoring** (4,315 bookings arrived before today: ROC-AUC 0.740, PR-AUC 0.412, Brier 0.156, top 20% precision 40.3%, lift 1.92) and **Data scope**.

To work on another day, set `G2_TODAY` before launching (e.g. `set G2_TODAY=2017-06-12` in Windows CMD): that day opens its own log with its own history. Within a log, time only moves forward per booking: nothing earlier than its latest entry can be decided or logged.

## Optional n8n connection
Import `n8n/g2_approval_demo_workflow.json` in a local n8n, click **Listen for Test Event** on the `Webhook` node, then launch the dashboard with `set G2_N8N_WEBHOOK_URL=<test URL>` (Windows CMD). **Send to automation (n8n)** returns what n8n *would* do (email preview / staff task / approval notice); only a reply with `accepted: true` (not just HTTP 200) is logged, as executed by n8n. Node code: `n8n/src/*.js`; regenerate the JSON with `python n8n/build_workflow.py`. Never add email credentials, and keep any published (production) webhook on `127.0.0.1` as in `n8n/README_VI.md` §6.

**Current state schema: 9** (9 adds two append-only logs: `plan_reviews`, one final staff decision per booking on its routine G1 plan (I1–I3; approving imports it, rejecting needs a reason and the database then refuses that plan), and `execution_attempts`, every hand-over to n8n with its result (accepted / refused / error) so failures stay visible and can be retried; 8 added database guards for concurrent writers: daily call limit, closed vs executed, G3 ban; 7 added `action_closures`: an open auto / approved action can be **closed without contact**, e.g. a G1 call whose window has passed, with who / why / business date; it then never runs, no longer blocks reminders and is not a call attempt). Restart the dashboard and re-import the updated workflow if you use n8n. The store keeps the frozen G1 execution policy, original F score date and, for I4 offers, `basis_event_id`: the exact change request the offer answers. A newer request invalidates the old offer and needs a new offer and approval. The default filename ends in `_v9.sqlite` and preserves older files (schema 5–8); an explicit `G2_DB_PATH` must point to a new file when upgrading. Older logs lack the request binding (schema 6), the closure log (schema 7) or the concurrency guards (schema 8) or the review and attempt logs (schema 9), so they are not migrated automatically.

**Two sessions at once (schema 8 and later):** each browser session has its own database connection, and every write (create, import, approve, execute, respond, close) is one transaction that first takes the store's write lock, then runs its checks and writes, then commits. So the last call slot goes to one session only, an action is either closed or executed, and the n8n webhook is called inside that transaction (the second request for the same action is never sent). Database triggers enforce the daily call limit, closed vs executed and the G3 ban for direct SQL writers too; on PostgreSQL they lock the row they depend on. Tests: `tests/test_g2_concurrency.py`.

**PostgreSQL instead of SQLite:** `pip install "psycopg[binary]>=3.2,<4"`, then set `G2_DB_URL=postgresql://user@host:port/db` before launching. The store lives in schema `g2_<hash of policy and today>_v9` of that database with the same CHECKs, triggers and messages (`dashboard/pg_backend.py`, DDL in `sql/g2_state_store_postgres.sql`); the sidebar shows which store is in use.

**Hướng dẫn n8n từng bước trên Windows:** [n8n/README_VI.md](../n8n/README_VI.md).

## Hướng dẫn nhanh (tiếng Việt)

**F, G1, G2 là gì?** F dự đoán booking nào có thể hủy. G1 lập kế hoạch: booking nào gọi điện, nhắc lịch hay không liên hệ, và gọi ngày nào trong giới hạn 20 cuộc/ngày. G2 là màn hình và sổ nhiệm vụ cho nhân viên, chạy đúng theo kế hoạch G1.

**Luồng dùng hằng ngày:** Overview (xem tình hình) → Risk Queue (lọc, chọn booking, duyệt hàng loạt) → Action Review (xem kỹ một booking, từ chối hoặc thay action; duyệt từng action nhạy cảm) → Execution & follow-up (ghi kết quả, theo dõi automation, Retry) → Performance (đánh giá).

**Kiểm tra nhanh (3 phút), hôm nay = 01/05/2017 (mặc định):**
1. **Overview:** *Yesterday*: 441 booking mới, 65 rủi ro cao, 63 đề xuất gọi điện đã duyệt, 37 booking tới ngày đến, 8 hủy, model xếp 6/8 vào mức cao hoặc trung bình. *Today*: *Awaiting a decision* = 362, *Due today* = 62 (20 cuộc gọi, 42 nhắc lịch).
2. **Risk Queue:** lọc *Review status = Awaiting review* và *Risk level = Medium* (196 booking) → **Select all eligible** → nhập tên → **Approve 196 recommendations…** → **Confirm approval**: 196 đề xuất được duyệt, 367 liên hệ được lên lịch. Bấm lại không tạo trùng.
3. **Action Review:** booking low `73859` có thể **Reject** kèm lý do. Booking `77744` (khách xin đổi ở cuộc gọi 25/04) có thể đề xuất *Change offer*, rồi chờ duyệt.
4. **Execution & follow-up:** 124 việc đến hạn (20 cuộc gọi, 93 nhắc lịch, 11 email). Ghi nhận cuộc gọi của `78298` (thiếu ba ô kiểm thì bị chặn, đủ thì ghi được).
5. **Action Review → Pending approvals:** duyệt *Hold rooms back* `2017-05-30 | A` (đề xuất từ hôm qua).
6. **Model monitoring:** 4.315 booking đã đến trước hôm nay, ROC-AUC 0,740, tự tính lại trên dữ liệu tới hôm qua.

**Lưu ý:**
- “Hôm nay” là một ngày cố định của năm 2017 (`G2_TODAY`); lịch sử tới hôm qua và câu trả lời của khách trong đó được dựng sẵn, suy ra từ kết quả thật của booking (xem *History up to yesterday* và *What is real and what is prepared* ở trên). Website không gửi email, không đổi giá, phòng, tồn kho hay database thật.
- Gọi, email, nhắc lịch (I1–I3) chỉ vào sổ nhiệm vụ khi được duyệt (theo lô hoặc từng booking); action nhạy cảm (I4, I6, phòng / giá) duyệt từng cái.
- Tier high / medium / low là **tỷ lệ của mỗi lô tuần** theo policy G1 (15% gọi, tổng 60% có liên hệ), không phải ngưỡng xác suất cố định. Số liệu ROI của G1 là mô phỏng, cần pilot để kiểm chứng.
- Consent và "action đang mở" không có trong dữ liệu: nhân viên phải xác nhận lúc thực thi.
- Website **không có đăng nhập**: chỉ chạy trên máy cá nhân, không đưa lên server công khai.
