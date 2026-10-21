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
    'arrival_date': 'Arrival', 'days_to_arrival': 'Days to arrival', 'market_segment': 'Segment',
    'deposit_type': 'Deposit', 'room_type': 'Room', 'reserved_room_type': 'Room',
    'p_basis': 'P basis', 'risk_tier': 'Tier', 'f_risk_tier': "F's tier", 'rule': 'Rule',
    'rule_id': 'Rule', 'schedule_status': 'G1 schedule', 'owner': 'Owner', 'approval': 'Approval',
    'expected_revenue_at_risk': 'Revenue at risk', 'syn_net_value_at_risk': 'Net value at risk (synthetic)',
    'scheduled_actions': 'Planned actions', 'scheduled_at': 'Due', 'due': 'Due',
    'action_id': 'Action', 'action_type': 'Action type', 'schedule_reason': 'Why this date',
    'message_no': 'Message no.', 'stay_date': 'Stay date', 'needs_approval': 'Needs approval',
    'approval_status': 'Approval', 'approved_by': 'Approved by', 'executed_on': 'Executed on',
    'outcome': 'Outcome', 'late_response': 'Guest response', 'closed': 'Closed',
    'target': 'For', 'status': 'Can it run today?', 'status_detail': 'Details', 'bookings': 'Bookings',
    'expected_cancellations': 'Expected cancellations', 'gross_value': 'Gross value',
    'first_decision': 'First known', 'bookings_G2': 'Group bookings', 'bookings_I5': 'I5 bookings',
    'expected_cancellations_G2': 'of which group', 'expected_cancellations_I5': 'of which I5', 'lead_time': 'Lead time (days)',
    'is_canceled': 'Cancelled', 'executions': 'Executions',
    'review_status': 'Review', 'execution_status': 'Execution', 'days_left': 'Days left', 'date': 'Date', 'what': 'What happened', 'who': 'By',
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
    shows the new state. `where`: the page that shows it, the one the user acted on."""
    st.session_state.setdefault(f'g2_flash_{where}', []).append((message, body))
    st.rerun()


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
