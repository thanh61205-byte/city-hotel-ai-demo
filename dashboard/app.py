"""G2 dashboard: F v3.1 weekly queue -> tier / rule / schedule -> approval -> execution.

Run from the repo root: python -m streamlit run dashboard/app.py

The dashboard works on one fixed day, "today" (G2_TODAY, default 2017-05-01). Each "today" has
its own action log; a new log first gets the team's history up to yesterday
(dashboard/seed_history.py). This entry point loads F / G1, opens this session's store, builds
that history once, draws the sidebar and the Today band, then runs the selected page
(dashboard/views/*.py) with the shared PageContext.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import streamlit as st

# Allows launching with `streamlit run dashboard/app.py` from the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dashboard import common, seed_history, state_store as ss, ui  # noqa: E402
from dashboard.data_contract import cancellation_buffer, source_fingerprint  # noqa: E402
from dashboard.ai_email.config import setting  # noqa: E402  (env vars, then Streamlit Cloud Secrets)

# G2_REPO: where the F outputs are read (tests point it at a fixture copy).
DATA_REPO = Path(os.environ.get('G2_REPO', ROOT))
# G2_TODAY: the working day the dashboard opens on (YYYY-MM-DD). G2_SEED_HISTORY=0: start a new
# log empty instead of with the team's history up to yesterday (tests).
DEFAULT_TODAY = date(2017, 5, 1)
SEED_HISTORY = setting('G2_SEED_HISTORY', '1') != '0'

st.set_page_config(page_title='City Hotel | Booking Risk', page_icon=':material/hotel:', layout='wide')
ui.inject_css()

try:
    queue, schedule, manifest = common.load(str(DATA_REPO), source_fingerprint(DATA_REPO))
except Exception as exc:  # show the reason instead of a stack trace to a beginner
    st.error(f'Cannot load the F / G1 outputs: {exc}. Re-run notebook 04 then 05 '
             '(`python src/run_notebooks.py --stage F`, then `--stage G1`).')
    st.stop()
try:
    TODAY = date.fromisoformat(setting('G2_TODAY') or DEFAULT_TODAY.isoformat())
except ValueError:
    st.error('G2_TODAY must be a date written YYYY-MM-DD, for example 2017-05-01.')
    st.stop()
first_day, last_day = min(schedule['decision_at']), max(schedule['scheduled_at'])
if not first_day <= TODAY <= last_day:
    st.error(f'G2_TODAY {TODAY.isoformat()} is outside the scored period '
             f'({first_day.isoformat()} to {last_day.isoformat()}).')
    st.stop()

# One action log per F run + G1 policy, today's date and store schema: another day never
# inherits this day's actions, and old logs are left untouched.
CAPACITY = manifest['call_capacity_per_day']
LOG_KEY = f"{manifest['policy_key']}_{TODAY:%Y%m%d}"
# G2_DB_URL=postgresql://user@host:port/db -> shared PostgreSQL store, one schema per log key
# (same rules as the local SQLite file).
DB_URL = setting('G2_DB_URL')
if DB_URL:
    import hashlib
    _key = hashlib.sha256(LOG_KEY.encode()).hexdigest()[:10]
    DB_TARGET = f"{DB_URL.split('#')[0]}#g2_{_key}_v{ss.SCHEMA_VERSION}"
    DB_LABEL = f"PostgreSQL schema g2_{_key}_v{ss.SCHEMA_VERSION}"
else:
    DB_PATH = Path(os.environ.get('G2_DB_PATH', ROOT / 'data' / 'outputs'
                                  / f'g2_demo_state_{LOG_KEY}_v{ss.SCHEMA_VERSION}.sqlite'))
    DB_TARGET, DB_LABEL = str(DB_PATH), f'SQLite {DB_PATH.name}'
try:
    con = common.state(DB_TARGET, manifest['policy_key'], CAPACITY, manifest['execution_policy'])
except Exception as exc:
    st.error(f'Cannot open the action log: {exc}')
    st.stop()

# A new log starts with the team's history up to yesterday (built once, about a minute).
if SEED_HISTORY and seed_history.needs_history(con):
    try:
        common.build_history(DB_TARGET, manifest, queue, schedule, TODAY, DATA_REPO)
    except Exception as exc:
        st.error(f'Cannot prepare the history up to yesterday: {exc}. Delete {DB_LABEL} and reload.')
        st.stop()
    con.rollback()
if seed_history.status(con) == 'building':
    st.info('The history up to yesterday is being prepared in another window. Reload in a minute.',
            icon=':material/hourglass_top:')
    st.stop()

as_of = TODAY
weeks = sorted(queue['score_time'].unique())

# The daily flow first, in the order the work is done: overview -> approve routine contacts in
# the risk queue -> carry them out -> look at single bookings and sensitive approvals. Support
# pages (planning, results, model, data) sit apart so they never interrupt that flow.
PAGES = {
    'Daily work': [
        st.Page('views/overview.py', title='Overview', icon=':material/dashboard:', default=True),
        st.Page('views/risk_queue.py', title='Risk Queue', icon=':material/format_list_bulleted:'),
        st.Page('views/execution.py', title='Execution & follow-up', icon=':material/send:'),
        st.Page('views/action_review.py', title='Action Review', icon=':material/rate_review:'),
    ],
    'Planning & analysis': [
        st.Page('views/buffer.py', title='Capacity planning', icon=':material/hotel:'),
        st.Page('views/performance.py', title='Performance evaluation', icon=':material/monitoring:'),
        st.Page('views/model_revenue.py', title='Model monitoring', icon=':material/insights:'),
    ],
    'Data': [
        st.Page('views/data_scope.py', title='Data scope', icon=':material/database:'),
    ],
}
page = st.navigation(PAGES)
common.keep_dialog_on(page.url_path)  # a detail popup belongs to the page it was opened on

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.caption(f'Risk model {manifest["selected_model"]} (run {manifest["run_id"][:6]}); at most '
               f'{CAPACITY} calls a day. Details on the Data scope page.')
    if seed_history.status(con) is None:  # never prepared (G2_SEED_HISTORY=0)
        st.caption(':orange[History up to yesterday was not prepared (G2_SEED_HISTORY=0): look-back '
                   'figures and KPIs cover only what was recorded in this log.]')
    if st.session_state.get('g2_guide_hidden') and st.button('Show the guide', icon=':material/help:'):
        st.session_state['g2_guide_hidden'] = False
        st.switch_page('views/overview.py')

st.title('City Hotel cancellation decision support')
last_batch = max((w for w in weeks if w <= as_of), default=None)
ui.today_band(
    as_of, last_batch=last_batch,
    last_batch_size=int(queue['score_time'].eq(last_batch).sum()) if last_batch else 0,
    calls_made=ss.calls_made(con, as_of), capacity=CAPACITY,
    due_today=int(schedule['scheduled_at'].eq(as_of).sum()),
    pending=sum(a['approval_status'] == 'pending' for a in ss.get_actions(con)))

common.set_ctx(common.PageContext(
    data_repo=DATA_REPO, queue=queue, schedule=schedule, manifest=manifest, con=con, as_of=as_of,
    by_id=queue.set_index('booking_id'),
    individual=queue[queue['track'].eq('individual model')], weeks=weeks,
    first_day=first_day, last_day=last_day,
    # Only what F had scored by today, with arrival still ahead (decision input).
    buffer=cancellation_buffer(queue, as_of), db_label=DB_LABEL))
page.run()
