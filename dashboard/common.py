"""What every dashboard page shares: cached loaders, this session's store connection, the page
context built by app.py on each run, and small display helpers.

app.py (the entry point) runs before the selected page on every rerun: it loads F / G1, opens
the store (building its history once), draws the sidebar and the Today band, then stores a
PageContext in this session's state. Pages (dashboard/views/*.py) read it with ctx(); they never load or connect themselves.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard import model_view as mv, seed_history, state_store as ss, ui, vocab
from dashboard.data_contract import load_outputs

TIER_ORDER = {'high': 0, 'medium': 1, 'low': 2}
# Refusals shown to the user: service rules, and database guards / a busy store (sqlite3.Error,
# also raised by the PostgreSQL backend).
BLOCKED = (ValueError, PermissionError, sqlite3.Error)
EUR = '€{:,.2f}'
P_CANCEL = st.column_config.ProgressColumn('P(cancel)', format='percent', min_value=0.0, max_value=1.0,
                                            help='Cancellation probability scored by F (group rows: '
                                                 'the group rule rate)')
# Display names of data columns, used by every table; the data and CSV downloads keep the
# field names of the data contract (docs/G2_data_contract.md).
LABELS = {
    'booking_id': 'Booking', 'track': 'Track', 'score_time': 'Scored on', 'decision_at': 'Decided on',
    'arrival_date': 'Check-in', 'days_to_arrival': 'Days to check-in', 'market_segment': 'Market segment',
    'deposit_type': 'Deposit', 'room_type': 'Room', 'reserved_room_type': 'Room',
    'p_basis': 'P basis', 'risk_tier': 'Risk level', 'f_risk_tier': "F's risk level", 'rule': 'Recommendation',
    'rule_id': 'Rule', 'schedule_status': 'G1 schedule', 'owner': 'Owner', 'approval': 'Approval',
    'expected_revenue_at_risk': 'Revenue at risk', 'syn_net_value_at_risk': 'Net value at risk (synthetic)',
    'scheduled_actions': 'Planned actions', 'scheduled_at': 'Due', 'due': 'Due',
    'action_id': 'Action', 'action_type': 'Action type', 'schedule_reason': 'Why this date',
    'message_no': 'Message no.', 'stay_date': 'Stay date', 'needs_approval': 'Needs approval',
    'approval_status': 'Approval', 'approved_by': 'Approved by', 'executed_on': 'Executed on',
    'outcome': 'Outcome', 'late_response': 'Guest response', 'closed': 'Closed',
    'target': 'For', 'status': 'Can it run today?', 'status_detail': 'Details', 'bookings': 'Bookings',
    'expected_cancellations': 'Expected cancellations', 'gross_value': 'Booking value',
    'first_decision': 'First known', 'bookings_G2': 'Group bookings', 'bookings_I5': 'I5 bookings',
    'expected_cancellations_G2': 'of which group', 'expected_cancellations_I5': 'of which I5', 'lead_time': 'Lead time (days)',
    'is_canceled': 'Cancelled', 'executions': 'Executions',
    'review_status': 'Review status', 'execution_status': 'Execution status',
    'days_left': 'Days to check-in', 'date': 'Date', 'what': 'What happened', 'who': 'By',
    'recommended': 'Recommendation',
}
RISK_COLORS = ui.RISK_COLORS
T = ui.TOKENS


@dataclass(frozen=True)
class PageContext:
    data_repo: Path
    queue: pd.DataFrame
    schedule: pd.DataFrame
    manifest: dict
    con: sqlite3.Connection
    as_of: date
    by_id: pd.DataFrame
    individual: pd.DataFrame
    weeks: list
    first_day: date
    last_day: date
    buffer: pd.DataFrame
    db_label: str

    @property
    def capacity(self) -> int:
        return self.manifest['call_capacity_per_day']


_CTX = 'g2_page_context'


def set_ctx(context: PageContext) -> None:
    st.session_state[_CTX] = context


def ctx() -> PageContext:
    return st.session_state[_CTX]


def table(df: pd.DataFrame, fmt: dict | None = None, height: int | None = None,
          progress_p: bool = True, alt_text: str | None = None) -> None:
    """Dataframe with readable headers (LABELS), formatted numbers and "—" for a missing value;
    p_cancel shown as a progress bar when present."""
    fmt = {k: v for k, v in (fmt or {}).items() if not (progress_p and k == 'p_cancel')}
    data = df.style.format(na_rep='—').format(fmt, na_rep='—')
    config = {c: st.column_config.Column(LABELS[c]) for c in df.columns if c in LABELS}
    if progress_p and 'p_cancel' in df.columns:
        config['p_cancel'] = P_CANCEL
    kwargs = {'hide_index': True, 'width': 'stretch', 'column_config': config}
    if height:
        kwargs['height'] = height
    if alt_text:
        kwargs['alt'] = alt_text
    st.dataframe(data, **kwargs)


def reviewer_input(key: str = 'g2_reviewer') -> str:
    """'Your name' for approvals, rejections and closures. The name typed on one page is kept
    for the others (a widget's own state is dropped when its page is left)."""
    if key not in st.session_state:
        st.session_state[key] = st.session_state.get('g2_reviewer_saved', '')
    name = st.text_input('Your name (reviewer)', key=key, placeholder='Reservations lead',
                         help='There is no login: the name is stored with each decision.')
    st.session_state['g2_reviewer_saved'] = name
    return name.strip()


def show_blocked(exc, prefix: str = '') -> None:
    """A refusal of the store, in plain words with the next step (dashboard/vocab.py)."""
    st.error(prefix + vocab.friendly_error(exc))


def done_and_rerun(message: str, body: dict | None = None, where: str = 'actions') -> None:
    """Keep the confirmation across a rerun, then rerun so every table (and the Today band)
    shows the new state. `where`: the page that shows it, the one the user acted on. An open
    detail popup closes, so the confirmation is seen on the page."""
    st.session_state.setdefault(f'g2_flash_{where}', []).append((message, body))
    close_dialog()
    st.rerun()


# ---------------------------------------------------------------- detail popups
# One popup at a time, kept in the session as (page, item) so it stays open while the user works
# in it and closes after an action (done_and_rerun), when dismissed, or when the page changes.
_DIALOG = 'g2_dialog'


def open_dialog(page: str, item) -> None:
    st.session_state[_DIALOG] = (page, item)


def dialog_item(page: str):
    """The item whose popup is open on `page`, else None."""
    open_ = st.session_state.get(_DIALOG)
    return open_[1] if open_ and open_[0] == page else None


def close_dialog() -> None:
    st.session_state.pop(_DIALOG, None)


def keep_dialog_on(page: str) -> None:
    """app.py: a popup opened on another page is closed when the user moves on."""
    open_ = st.session_state.get(_DIALOG)
    if open_ and open_[0] != page:
        close_dialog()


# ---------------------------------------------------------------- paged lists
PAGE_SIZE = 20


def page_slice(df: pd.DataFrame, key: str, size: int = PAGE_SIZE) -> pd.DataFrame:
    """The rows of the page chosen with pager(key) (drawn under the list). A page number beyond
    the last page (the filter result shrank) falls back to the last page."""
    pages = max(1, -(-len(df) // size))
    k = f'{key}_page'
    if st.session_state.get(k, 1) > pages:
        st.session_state[k] = pages
    n = int(st.session_state.get(k, 1))
    return df.iloc[(n - 1) * size: n * size]


def pager(total: int, key: str, size: int = PAGE_SIZE) -> None:
    """Page picker under a list: [page] / pages, and which rows are shown."""
    pages = max(1, -(-total // size))
    k = f'{key}_page'
    with st.container(horizontal=True, vertical_alignment='center', gap='small'):
        st.number_input('Page', min_value=1, max_value=pages, step=1, key=k, width=120,
                        label_visibility='collapsed')
        n = int(st.session_state.get(k, 1))
        first, last = (n - 1) * size + 1, min(n * size, total)
        st.markdown(f'/ {pages:,} pages · rows {first:,}–{last:,} of {total:,}' if total else
                    '/ 1 page · no rows', width='content')


def show_flash(where: str = 'actions') -> None:
    for message, body in st.session_state.pop(f'g2_flash_{where}', []):
        st.success(message)
        if body is not None:
            st.json(body)


@st.cache_data(show_spinner=False, max_entries=2)
def load_outcomes(repo: str, fingerprint: tuple) -> pd.Series:
    # Cancelled or not, per booking: known only after arrival. Pages show it for bookings that
    # arrived before today; it never feeds the queue, schedule or actions.
    return mv.outcomes(repo)


@st.cache_data(show_spinner=False, max_entries=2)
def load(repo: str, fingerprint: tuple):
    # `fingerprint` (mtime + size of every F and G1 input) is the cache key: when notebook 04
    # or 05 rewrites its outputs, the next rerun reloads and re-validates them.
    return load_outputs(repo)


@st.cache_resource(show_spinner=False)
def prepare_store(db_path: str, run_key: str, capacity: int, execution_policy: dict) -> str:
    # Create / verify the store once per server process (schema, run, capacity, policy).
    ss.connect(db_path, run_id=run_key, call_capacity=capacity,
               execution_policy=execution_policy).close()
    return db_path


def build_history(db_path: str, manifest: dict, queue: pd.DataFrame, schedule: pd.DataFrame,
                  today: date, repo: Path) -> dict | None:
    """Write the team's history up to yesterday into a new, empty log (seed_history.py), with a
    progress bar. Its own connection; on SQLite without a sync per commit, which makes the
    thousands of small writes fast (if the computer stops meanwhile, delete the file)."""
    con = ss.connect(db_path, run_id=manifest['policy_key'], call_capacity=manifest['call_capacity_per_day'],
                     execution_policy=manifest['execution_policy'], check_same_thread=False)
    bar = st.progress(0.0, text='Preparing the history up to yesterday…')
    try:
        if isinstance(con, sqlite3.Connection):
            con.execute('PRAGMA synchronous = OFF')
            con.execute('PRAGMA journal_mode = MEMORY')
        first = min(queue['score_time'])
        outcome = mv.outcomes(repo) if today > first else pd.Series(dtype=int)
        return seed_history.build(con, queue, schedule, manifest['policy_key'], today, outcome,
                                  progress=lambda share, text: bar.progress(min(share, 1.0), text=text))
    finally:
        bar.empty()
        con.close()


def state(db_path: str, run_key: str, capacity: int, execution_policy: dict):
    """This browser session's own connection. Never shared between sessions: a commit of one
    session must not end another session's transaction. Writes are serialised by the store's
    write lock (state_store._unit), so checks always see the other sessions' commits."""
    prepare_store(db_path, run_key, capacity, execution_policy)
    key = f'g2_store::{db_path}'
    con = st.session_state.get(key)
    if con is None:
        # A session's reruns are sequential but may run on different threads.
        con = ss.connect(db_path, run_id=run_key, call_capacity=capacity,
                         execution_policy=execution_policy, check_same_thread=False)
        st.session_state[key] = con
    con.rollback()  # nothing half-done is carried over from an interrupted rerun
    return con


def item_list(rows: list[dict], spec: list[tuple], *, key: str, id_field: str,
              button: str = 'Details', icon: str = ':material/open_in_new:'):
    """A list of records, one line each, with a details button at the end of each line.
    spec: (header, relative width, row -> markdown) per column. Returns the id of the record
    whose button was clicked in this run, else None."""
    widths = [w for _, w, _ in spec] + [1.0]
    head = st.columns(widths, vertical_alignment='bottom')
    for col, (header, _, _) in zip(head, spec):
        col.caption(f'**{header}**')
    clicked = None
    for r in rows:
        with st.container(border=True, gap='small'):
            cols = st.columns(widths, vertical_alignment='center')
            for col, (_, _, show) in zip(cols, spec):
                col.markdown(show(r))
            if cols[-1].button(button, key=f'{key}_{r[id_field]}', icon=icon, width='stretch'):
                clicked = r[id_field]
    return clicked
