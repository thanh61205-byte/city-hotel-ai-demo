"""Where each booking and action stands today, for the Overview, Risk Queue and
Execution pages. Read only: every decision is still taken and checked by state_store.

Review status (has staff decided on G1's recommendation?) and execution status (how far an
action got) are kept apart: an approved plan has not been carried out yet.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from dashboard import state_store as ss, vocab

# Review status of a booking's recommendation today.
AWAITING, APPROVED, REJECTED = 'Awaiting review', 'Approved', 'Rejected'
MANUAL, NO_CONTACT, GROUP = 'Decide manually', 'No contact planned', 'Group block'


def open_bookings(queue: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Bookings F had scored by `as_of` (today) whose arrival is still ahead."""
    return queue[(queue['score_time'] <= as_of) & (queue['arrival_date'] > as_of)]


def review_status(con, bookings: pd.DataFrame, schedule: pd.DataFrame) -> pd.Series:
    """Review status per booking (index: booking_id).

    Approved / Rejected: a plan_reviews decision, or (approved) a G1 plan already imported into
    the action log by the older import buttons. Awaiting review: a routine G1 plan (I1-I3) with
    no decision yet. Bookings without a plan: I3 calls that found no slot (decide manually), I5
    (no contact, capacity planning) and group bookings (capacity planning)."""
    decided = {int(r['booking_id']): r['decision'] for r in ss.plan_reviews(con)}
    imported = {int(r[0]) for r in con.execute(
        'SELECT DISTINCT booking_id FROM action_plan WHERE scheduled_at IS NOT NULL').fetchall()}
    planned = set(schedule['booking_id'])
    out = {}
    for bid, track, status in zip(bookings['booking_id'], bookings['track'],
                                  bookings.get('schedule_status', pd.Series(index=bookings.index))):
        bid = int(bid)
        if track != 'individual model':
            out[bid] = GROUP
        elif bid in decided:
            out[bid] = APPROVED if decided[bid] == 'approved' else REJECTED
        elif bid in imported:
            out[bid] = APPROVED
        elif bid in planned:
            out[bid] = AWAITING
        elif status == 'manual_capacity_review':
            out[bid] = MANUAL
        else:
            out[bid] = NO_CONTACT
    return pd.Series(out, name='review_status')


def pending_approvals(con) -> list[dict]:
    """Sensitive actions (I4, I6, date x room type) waiting for a case-by-case decision."""
    return [a for a in ss.get_actions(con) if a['approval_status'] == 'pending']


def done_between(con, start: date, end: date) -> int:
    """Executions (contacts and date actions) with a business date in [start, end]."""
    return con.execute('SELECT COUNT(*) FROM interventions WHERE as_of >= ? AND as_of <= ?',
                       (start.isoformat(), end.isoformat())).fetchone()[0]


def approved_between(con, start: date, end: date) -> int:
    """Recommendations (routine G1 plans) approved with a business date in [start, end]."""
    return con.execute("""SELECT COUNT(*) FROM plan_reviews WHERE decision = 'approved'
                          AND as_of >= ? AND as_of <= ?""", (start.isoformat(), end.isoformat())).fetchone()[0]


def contacts_between(con, start: date, end: date) -> int:
    """Guest contacts (calls, emails, reminders, offers, deposit requests) done in [start, end]."""
    return con.execute(f"""SELECT COUNT(*) FROM interventions i JOIN action_plan a USING (action_id)
        WHERE a.action_type IN ({','.join(f"'{t}'" for t in ss.GUEST_FACING)})
          AND i.as_of >= ? AND i.as_of <= ?""", (start.isoformat(), end.isoformat())).fetchone()[0]


def confirmed_between(con, start: date, end: date) -> int:
    """Bookings whose guest confirmed in [start, end], during a contact or later."""
    return con.execute("""SELECT COUNT(DISTINCT booking_id) FROM (
        SELECT a.booking_id FROM interventions i JOIN action_plan a USING (action_id)
        WHERE i.outcome = 'confirmed' AND i.as_of >= ? AND i.as_of <= ?
        UNION ALL
        SELECT booking_id FROM guest_responses
        WHERE response = 'confirmed' AND response_at >= ? AND response_at <= ?) AS answers""",
                       (start.isoformat(), end.isoformat()) * 2).fetchone()[0]


def due_between(con, start: date, end: date) -> pd.DataFrame:
    """Open actions in the log (not executed, not closed, not rejected) due in [start, end]."""
    rows = con.execute("""
      SELECT a.action_id, a.booking_id, a.stay_date, a.room_type, a.action_type, a.rule_id,
             COALESCE(a.scheduled_at, a.decision_at) AS due, a.approval_status
      FROM action_plan a LEFT JOIN interventions i ON i.action_id = a.action_id
      WHERE i.intervention_id IS NULL AND a.approval_status <> 'rejected'
        AND NOT EXISTS (SELECT 1 FROM action_closures c WHERE c.action_id = a.action_id)
        AND COALESCE(a.scheduled_at, a.decision_at) >= ? AND COALESCE(a.scheduled_at, a.decision_at) <= ?
      ORDER BY due, a.action_id""", (start.isoformat(), end.isoformat())).fetchall()
    return pd.DataFrame([dict(r) for r in rows],
                        columns=['action_id', 'booking_id', 'stay_date', 'room_type', 'action_type',
                                 'rule_id', 'due', 'approval_status'])


def recent_activity(con, as_of: date, limit: int | None = 12, booking_id: int | None = None) -> pd.DataFrame:
    """Latest logged events with a business date on or before `as_of`, newest first, with a
    plain description (what) and who did it.

    Approvals of single actions are left out: the store records them on this computer's clock,
    not on a business date, so they cannot be placed on the timeline. booking_id: that booking
    only (its history); limit None: everything."""
    # LIMIT is added only when asked: SQLite and PostgreSQL disagree on a "no limit" value.
    rows = con.execute("""
      SELECT * FROM (
        SELECT i.as_of AS date, 'done' AS kind, a.action_type, i.outcome AS detail, a.booking_id,
               a.stay_date, a.room_type, i.executed_by AS who, i.executed_at AS t
        FROM interventions i JOIN action_plan a USING (action_id)
        UNION ALL
        SELECT r.response_at, 'response', a.action_type, r.response, r.booking_id, NULL, NULL,
               'staff', r.recorded_at
        FROM guest_responses r JOIN action_plan a USING (action_id)
        UNION ALL
        SELECT c.as_of, 'closed', a.action_type, c.reason, a.booking_id, a.stay_date, a.room_type,
               c.closed_by, c.closed_at
        FROM action_closures c JOIN action_plan a USING (action_id)
        UNION ALL
        SELECT p.as_of, 'review', p.rule_id, p.decision || CASE WHEN p.batch_id IS NULL THEN ''
               ELSE ' in a batch' END, p.booking_id, NULL, NULL, p.reviewer, p.reviewed_at
        FROM plan_reviews p
        UNION ALL
        SELECT e.as_of, 'attempt', a.action_type, e.result, a.booking_id, a.stay_date, a.room_type,
               e.executor, e.attempted_at
        FROM execution_attempts e JOIN action_plan a USING (action_id)
        WHERE e.result <> 'accepted'  -- an accepted hand-over is the execution row above
        UNION ALL
        SELECT a.decision_at, 'proposed', a.action_type, NULL, a.booking_id, a.stay_date,
               a.room_type, 'staff', a.created_at
        FROM action_plan a WHERE a.needs_approval = 1
      ) AS events WHERE date <= ? AND (CAST(? AS INTEGER) IS NULL OR booking_id = ?)
      ORDER BY date DESC, t DESC""" + ('' if limit is None else f' LIMIT {int(limit)}'),
                       (as_of.isoformat(), booking_id, booking_id)).fetchall()
    cols = ['date', 'kind', 'action_type', 'detail', 'booking_id', 'stay_date', 'room_type', 'who', 't']
    df = pd.DataFrame([dict(r) for r in rows], columns=cols)
    df['what'] = [_describe(k, a, d, w) for k, a, d, w in
                  zip(df['kind'], df['action_type'], df['detail'], df['who'])]
    df['target'] = [f'booking {int(b)}' if pd.notna(b) else f'{s} × room {r}'
                    for b, s, r in zip(df['booking_id'], df['stay_date'], df['room_type'])]
    return df[['date', 'what', 'target', 'who']]


def _describe(kind, action_type, detail, who=None) -> str:
    if kind == 'done' and who == 'n8n':  # sent by automation; an answer, if any, comes later
        return f'{vocab.action(action_type)} sent' + ('' if detail == 'no_response'
                                                      else f': {vocab.outcome(detail)}')
    if kind == 'done':
        return f'{vocab.action(action_type)} recorded: {vocab.outcome(detail)}'
    if kind == 'response':
        return f'Guest response: {vocab.outcome(detail)}'
    if kind == 'closed':
        return f'{vocab.action(action_type)} closed without contact: {detail}'
    if kind == 'review':
        return f'Recommendation {detail}: {vocab.rule(action_type)}'
    if kind == 'attempt':
        return f'{vocab.action(action_type)}: {vocab.ATTEMPTS.get(detail, detail)}'
    return f'{vocab.action(action_type)} proposed, needs approval'


def period_end(as_of: date, days: int) -> date:
    return as_of + timedelta(days=days)


# ---------------------------------------------------------------- execution status
# How far an action got, on a given day. Never "sent" or "completed" without a logged
# execution; a refused / failed automation attempt shows as Failed until a later success.
SCHEDULED, DUE, SENT, COMPLETED, FAILED = 'Scheduled', 'Due', 'Sent', 'Completed', 'Failed'
CLOSED, AWAITING_APPROVAL, REJECTED_ACTION = 'Closed', 'Awaiting approval', 'Rejected'
NOT_STARTED, DONE = 'Not started', 'Done'


def action_status(con, as_of: date) -> pd.DataFrame:
    """Every action in the log with its execution status on `as_of`."""
    rows = con.execute("""
      SELECT a.action_id, a.booking_id, a.stay_date, a.room_type, a.action_type, a.rule_id,
             COALESCE(a.scheduled_at, a.decision_at) AS due, a.approval_status,
             i.as_of AS executed_on, i.executed_by, i.outcome,
             (SELECT c.reason FROM action_closures c WHERE c.action_id = a.action_id) AS closed,
             (SELECT e.result FROM execution_attempts e WHERE e.action_id = a.action_id
              ORDER BY e.attempt_id DESC LIMIT 1) AS last_attempt,
             (SELECT e.detail FROM execution_attempts e WHERE e.action_id = a.action_id
              ORDER BY e.attempt_id DESC LIMIT 1) AS last_detail
      FROM action_plan a LEFT JOIN interventions i ON i.action_id = a.action_id
      ORDER BY a.action_id""").fetchall()
    cols = ['action_id', 'booking_id', 'stay_date', 'room_type', 'action_type', 'rule_id', 'due',
            'approval_status', 'executed_on', 'executed_by', 'outcome', 'closed', 'last_attempt',
            'last_detail']
    df = pd.DataFrame([dict(r) for r in rows], columns=cols)
    day = as_of.isoformat()

    def one(r):
        if pd.notna(r['executed_on']):
            return SENT if r['executed_by'] == 'n8n' else COMPLETED
        if pd.notna(r['closed']):
            return CLOSED
        if r['approval_status'] == 'rejected':
            return REJECTED_ACTION
        if r['approval_status'] == 'pending':
            return AWAITING_APPROVAL
        if r['last_attempt'] in ('refused', 'error'):
            return FAILED
        return DUE if r['due'] <= day else SCHEDULED

    df['execution_status'] = [one(r) for r in df.to_dict('records')] if len(df) else []
    return df


def booking_execution(actions: pd.DataFrame) -> pd.Series:
    """One execution status per booking from its actions: the one that needs attention first
    (Failed, Due, Awaiting approval, Scheduled), else Done / Closed. Bookings without actions
    are Not started (absent from the result)."""
    order = [FAILED, DUE, AWAITING_APPROVAL, SCHEDULED]
    out = {}
    for bid, statuses in actions.dropna(subset=['booking_id']).groupby('booking_id')['execution_status']:
        seen = set(statuses)
        first = next((s for s in order if s in seen), None)
        out[int(bid)] = first or (DONE if seen & {SENT, COMPLETED} else CLOSED)
    return pd.Series(out, name='execution_status', dtype=object)


# ---------------------------------------------------------------- batch approval
def bulk_blocker(row, review: str, as_of: date, call_policy: dict | None) -> str | None:
    """Why a booking cannot be approved in a batch on `as_of` (None: eligible).

    Only routine recommendations awaiting review qualify. A planned call that could no longer
    be made (call window over, or too close to arrival) needs a person to look at it."""
    if review != AWAITING:
        return {APPROVED: 'Already approved', REJECTED: 'Rejected at review',
                MANUAL: 'No call slot: decide individually',
                NO_CONTACT: 'No contact planned'}.get(review, review)
    if row['arrival_date'] <= as_of:
        return 'Arrives today: too late to contact'
    if row['rule'] == 'I3' and call_policy:
        if (as_of - row['score_time']).days >= call_policy['window_days']:
            return 'Call window over: review individually'
        if (row['arrival_date'] - as_of).days < call_policy['min_days_before_arrival']:
            return 'Too close to arrival for a call: review individually'
    return None
