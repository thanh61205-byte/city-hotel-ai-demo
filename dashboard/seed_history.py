"""Prepared history: what the team already did before "today" (G2_TODAY, default 2017-05-01).

The dashboard opens on one working day. Everything before it was handled as it would have been
in a pilot. This module writes that history ONCE, into an empty log, through the store's own
service functions (state_store), so every guard runs exactly as it does for a click.

Written day by day, from the first scoring day up to yesterday:
- Review: every routine G1 recommendation (I1-I3) is approved by the "Reservations team" on the
  day its weekly batch was scored. Of the batch scored yesterday, only the calls (I3) were
  approved: G1 places calls from the scoring day on, within a daily capacity, so the call list
  cannot wait a day. Its emails and reminders (I1, I2) are left for today's review.
- Execution: every approved contact due before today is carried out on its due date. Emails and
  reminders go through automation (status Sent); calls are logged by staff (status Completed),
  within the daily call capacity. A follow-up that is no longer needed (the guest already
  answered, or cancelled) is closed without contact.
- Guest answers: the data has none, so they are derived from each booking's real outcome and
  never contradict it. A guest who later cancelled never confirms (some cancel during the call).
  A guest who kept the booking sometimes confirms (or asks to change, on a call); otherwise there
  is no answer. Answers are fixed per booking reference: a rebuild gives the same history.
- Rooms and prices: on each scoring day the revenue manager holds back rooms on the stay date x
  room type with the most expected cancellations (proposed, approved, carried out). One more
  proposal, made yesterday, waits for approval.

The real outcome (is_canceled) is read only here, to keep the invented answers consistent with
what happened. It never reaches the queue, the schedule or a recommendation.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, timedelta

import pandas as pd

from dashboard import state_store as ss
from dashboard.data_contract import cancellation_buffer

RESERVATIONS = 'Reservations team'
REVENUE_MANAGER = 'Revenue manager'
CHECKS = {k: True for k in ss.PRECHECKS}  # staff confirmed consent, no conflict, still active
REFUSED = (ValueError, PermissionError, sqlite3.Error)  # store refusals (also on PostgreSQL)
META_KEY = 'history'                     # store_meta: 'building', then 'done:<yesterday>'

# Shares of answers (fixed per booking reference, see _draw).
CALL_KEPT = (('confirmed', 0.50), ('requested_change', 0.10))  # rest: no answer
CALL_CANCELLED = (('cancelled', 0.35),)                          # rest: no answer
EMAIL_CONFIRMED = 0.25           # kept bookings that confirm after an email / reminder
ANSWER_DELAY = (1, 2)            # days between an email and the guest's reply


def _draw(booking_id: int, key: str) -> float:
    """A number in [0, 1) fixed by booking reference and purpose."""
    digest = hashlib.sha256(f'g2-history|{int(booking_id)}|{key}'.encode()).digest()
    return int.from_bytes(digest[:8], 'big') / 2 ** 64


def call_answer(booking_id: int, cancelled: bool) -> str:
    u, cut = _draw(booking_id, 'call'), 0.0
    for answer, share in (CALL_CANCELLED if cancelled else CALL_KEPT):
        cut += share
        if u < cut:
            return answer
    return 'no_response'


def email_reply(booking_id: int, message_no: int, cancelled: bool) -> int | None:
    """Days until the guest confirms after this email / reminder, or None (no answer)."""
    if cancelled or _draw(booking_id, f'email{message_no}') >= EMAIL_CONFIRMED:
        return None
    lo, hi = ANSWER_DELAY
    return lo + int(_draw(booking_id, f'delay{message_no}') * (hi - lo + 1))


def status(con) -> str | None:
    row = con.execute('SELECT value FROM store_meta WHERE key = ?', (META_KEY,)).fetchone()
    return row['value'] if row else None


def needs_history(con) -> bool:
    """True for a new, empty log that has no history yet (an existing log is never touched)."""
    if status(con) is not None:
        return False
    used = con.execute('SELECT (SELECT COUNT(*) FROM action_plan) + (SELECT COUNT(*) FROM plan_reviews)')
    return used.fetchone()[0] == 0


def _claim(con) -> bool:
    """Mark the log as being built; False if another session got there first."""
    with ss._unit(con):
        if not needs_history(con):
            return False
        con.execute('INSERT INTO store_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING',
                    (META_KEY, 'building'))
    return True


def _set_status(con, value: str) -> None:
    with ss._unit(con):
        con.execute('UPDATE store_meta SET value = ? WHERE key = ?', (value, META_KEY))


def _open_due(con, day: str) -> list[dict]:
    """Open actions that may run on `day` (approved or auto, not done, not closed), calls first."""
    rows = con.execute("""
      SELECT a.* FROM action_plan a LEFT JOIN interventions i ON i.action_id = a.action_id
      WHERE i.intervention_id IS NULL AND a.approval_status IN ('auto', 'approved')
        AND NOT EXISTS (SELECT 1 FROM action_closures c WHERE c.action_id = a.action_id)
        AND COALESCE(a.scheduled_at, a.decision_at) <= ?
      ORDER BY CASE a.action_type WHEN 'phone_call' THEN 0 ELSE 1 END, a.action_id""", (day,))
    return [dict(r) for r in rows.fetchall()]


def _close_after_answer(con, booking_id: int, day: date) -> int:
    """The guest answered on `day`: close the booking's open contacts that are no longer needed
    (follow-ups after any answer, everything after a cancellation), as staff would that day."""
    stop = ss.booking_stop_outcome(con, booking_id, day)
    if stop is None:
        return 0
    rows = con.execute("""
      SELECT a.action_id, a.action_type FROM action_plan a LEFT JOIN interventions i ON i.action_id = a.action_id
      WHERE a.booking_id = ? AND i.intervention_id IS NULL AND a.approval_status IN ('auto', 'approved')
        AND NOT EXISTS (SELECT 1 FROM action_closures c WHERE c.action_id = a.action_id)""",
                       (int(booking_id),)).fetchall()
    closed = 0
    for r in rows:
        if stop == 'cancelled' or r['action_type'] in ss.FOLLOW_UPS:
            ss.close_action(con, r['action_id'], reviewer=RESERVATIONS, as_of=day,
                            reason=('Guest cancelled the booking' if stop == 'cancelled'
                                    else 'Not needed: the guest already answered'))
            closed += 1
    return closed


def _automation(_action: dict) -> dict:
    return {'accepted': True, 'executor': 'automation'}


def _hold_rooms(con, queue: pd.DataFrame, day: date, run_key: str, held: set, approve: bool) -> int | None:
    """Propose holding back rooms on the cell with the most expected cancellations as known on
    `day`; with approve, the revenue manager approves it and it is carried out the same day."""
    buf = cancellation_buffer(queue, day)
    buf = buf[[(d, r) not in held for d, r in zip(buf['arrival_date'], buf['room_type'])]]
    if buf.empty:
        return None
    cell = buf.sort_values(['expected_cancellations', 'arrival_date'], ascending=[False, True]).iloc[0]
    note = (f"buffer as of {day.isoformat()}: {int(cell['bookings'])} bookings, "
            f"{cell['expected_cancellations']:.2f} expected cancellations")
    aid = ss.create_action(con, 'capacity_allocation', stay_date=cell['arrival_date'],
                           room_type=cell['room_type'], rule_id='G2', source_run_id=run_key,
                           decision_at=day, known_from=cell['first_decision'], note=note)
    held.add((cell['arrival_date'], cell['room_type']))
    if approve:
        ss.decide_action(con, aid, REVENUE_MANAGER, True)
        ss.execute_action(con, aid, as_of=day, outcome='completed', executed_by='staff')
    return aid


def build(con, queue: pd.DataFrame, schedule: pd.DataFrame, run_key: str, today: date,
          is_canceled: pd.Series, progress=None) -> dict | None:
    """Write the history up to the day before `today` into an empty log (see module doc).

    Returns counts of what was written, or None when the log is not new (nothing is written).
    progress(share, text): optional callback while building. A failure leaves the history
    marked 'building'; start again with a new log file."""
    if not _claim(con):
        return None
    yesterday = today - timedelta(days=1)
    first = min(queue['score_time'])
    days = [first + timedelta(days=n) for n in range((yesterday - first).days + 1)]
    arrival = queue.set_index('booking_id')['arrival_date']
    plans: dict[int, list[dict]] = {}
    for row in schedule.to_dict('records'):
        plans.setdefault(int(row['booking_id']), []).append(row)
    cancelled = {int(b): bool(v) for b, v in is_canceled.items()}
    review_days = {d for d in queue['score_time'].unique() if d <= yesterday}
    counts = dict.fromkeys(['approved', 'calls', 'messages', 'room_actions', 'closed',
                            'answers', 'pending'], 0)
    replies: dict[date, list[int]] = {}  # day -> email actions the guest confirms that day
    held: set = set()

    for n, day in enumerate(days):
        if progress is not None:
            progress(n / max(len(days), 1), f'Preparing history: {day:%d %b %Y}')
        # 1. Guest replies to earlier emails (before today's contacts, which they may stop).
        for aid in replies.pop(day, []):
            try:
                ss.record_response(con, aid, response='confirmed', response_at=day)
            except REFUSED:
                continue  # booking cancelled or already answered on a call meanwhile
            counts['answers'] += 1
            counts['closed'] += _close_after_answer(con, ss.get_action(con, aid)['booking_id'], day)
        # 2. This week's recommendations, approved on their scoring day (yesterday: calls only).
        if day in review_days:
            batch = queue[queue['score_time'].eq(day) & queue['booking_id'].isin(plans.keys())]
            if day == yesterday:
                batch = batch[batch['rule'].eq('I3')]
            out = ss.review_plans(con, [(int(b), plans[int(b)], arrival[b]) for b in batch['booking_id']],
                                  reviewer=RESERVATIONS, approve=True, as_of=day,
                                  batch_id=f'weekly-{day.isoformat()}')
            skipped = [o for o in out if o['status'] != 'approved']
            if skipped:
                raise RuntimeError(f'History: {len(skipped)} recommendations refused on {day}: '
                                   f"{skipped[0]['message']}")
            counts['approved'] += len(out)
            if day < yesterday and _hold_rooms(con, queue, day, run_key, held, approve=True) is not None:
                counts['room_actions'] += 1
        # 3. Everything due today, calls first (a contact closed meanwhile is skipped).
        for a in _open_due(con, day.isoformat()):
            bid = a['booking_id']
            if con.execute('SELECT 1 FROM action_closures WHERE action_id = ?', (a['action_id'],)).fetchone():
                continue
            if a['action_type'] == 'phone_call':
                answer = call_answer(bid, cancelled.get(bid, False))
                ss.execute_action(con, a['action_id'], as_of=day, precheck=CHECKS, executed_by='staff',
                                  outcome=answer)
                counts['calls'] += 1
                if answer in ss.STOP_OUTCOMES:
                    counts['closed'] += _close_after_answer(con, bid, day)
            elif a['action_type'] in ss.AUTO_MESSAGES:
                ss.execute_action(con, a['action_id'], as_of=day, outcome='no_response',
                                  precheck=CHECKS, executed_by='n8n', dispatch=_automation)
                counts['messages'] += 1
                wait = email_reply(bid, a['message_no'] or 1, cancelled.get(bid, False))
                answer_on = day + timedelta(days=wait) if wait else None
                if answer_on and answer_on < today and answer_on < date.fromisoformat(a['arrival_date']):
                    replies.setdefault(answer_on, []).append(a['action_id'])
            # Other actions (offers, deposits, date actions) are never left open by this history.
    # 4. Yesterday's proposal, waiting for the revenue manager today.
    if days and _hold_rooms(con, queue, yesterday, run_key, held, approve=False) is not None:
        counts['pending'] += 1
    _set_status(con, f'done:{yesterday.isoformat()}')
    if progress is not None:
        progress(1.0, 'History ready')
    return counts

