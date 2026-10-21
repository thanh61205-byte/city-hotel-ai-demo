"""Demo action and approval log: a local SQLite file, or a PostgreSQL schema (pg_backend.py).

TEST-ONLY store: no login, no guest contact data, no price or inventory edits. The same
approval rules are written for the `hotel` data model in sql/g2_approval_guard_postgres.sql.

Rules enforced (docs/decision_table_draft.md):
- §6 approval guard, in this service AND in the database (CHECK + triggers):
  sensitive actions always need approval and can never be 'auto'; an intervention is written
  only for 'approved' or 'auto' actions; an approval decision is final (pending -> approved /
  rejected only); the intervention and response logs are append-only.
- Action level: guest actions belong to a booking; capacity / resale / overbooking / price
  actions belong to a stay date x room type (group track, rule G2-G5).
- Timeline (replay): every action has a decision date. It cannot be decided before F knew
  the booking (its score time) nor on / after arrival, and it cannot be executed before its
  decision or due date, nor on / after arrival. Per booking (or date x room type), replay
  time only moves forward: nothing can be decided, executed or answered before the latest
  event already recorded for it. History is read as of the date being checked, so a later
  event never changes an earlier decision. To replay a period again, use a new state DB.
- Guest responses: given when a contact is logged, or recorded later for an executed contact
  (record_response). Any response stops follow-ups (§3); a cancellation stops everything.
- §2.4 and §3, in this service: consent, open-action and active-status checks at execution
  time; max 2 reminders / 2 call attempts; never an automated message (I1 / I2) on the same
  day as a call (I3), and an open due call goes first; I4 offer only after a call whose
  latest response is a change request, bound to that exact request (basis_event_id): a newer
  request makes the old offer dead and needs a new offer and approval; I6 deposit request only under a written policy, for a
  high-tier No Deposit booking, with the policy reference that justifies "high value" (G1 has
  not set a value threshold, so none is invented here).
- F schedules: only the action types the rule allows (RULE_ACTIONS) can be imported.
- One store per run (F run + G1 policy id): the store records it and refuses another run's
  schedule. Daily call capacity (G1 policy) is fixed per store; calls beyond it are refused.

`check_execution` runs every execution check without writing; `execute_action` runs it again
inside the write unit and, for n8n, calls the webhook there, so the action (and its call slot)
is reserved before anything leaves the system.

Concurrency: every write is one unit of work (`_unit`). Its first statement takes the store's
write lock (SQLite: the database write lock; PostgreSQL: a row lock on store_meta), so two
sessions run their checks and writes one after another and each check sees what the other
committed. One connection per session: never share a connection between threads / sessions.
The database guards (capacity, closed vs executed, G3) also hold for direct SQL writers.

Schema 9 adds two append-only logs:
- plan_reviews: staff review of G1's routine plan for a booking (I1 confirmation / reminders,
  I2 reminders, I3 call), one final decision per booking. Approving imports the plan into
  action_plan in the same unit of work; rejecting records who and why, and the database then
  refuses to import that booking's G1 plan. Routine plans may be reviewed in a batch
  (review_plans); sensitive actions (I4 offer, I6 deposit, date x room-type actions) keep their
  own pending -> approved / rejected decision, one action at a time (decide_action).
- execution_attempts: every hand-over to n8n and what came back (accepted / refused / error),
  so a failed or refused automation stays visible and can be retried. Only an accepted attempt
  has an execution (interventions row); the dashboard never shows "sent" without one.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median

SCHEMA_VERSION = '9'
BOOKING_ACTIONS = ('confirm_email', 'reminder', 'phone_call', 'offer_change', 'request_deposit')
DATE_ACTIONS = ('price_adjustment', 'capacity_allocation', 'resale', 'overbooking')
ACTIONS = BOOKING_ACTIONS + DATE_ACTIONS
# Guest actions F may schedule per rule (I5 and G2 schedule none).
RULE_ACTIONS = {'I1': frozenset({'confirm_email', 'reminder'}),
                'I2': frozenset({'reminder'}),
                'I3': frozenset({'phone_call'})}
# Conservative G2 policy: offer_change always needs review (the decision table asks it only
# when price changes; the schema has no price-change flag yet). Relax only if G1 agrees.
APPROVAL_REQUIRED = frozenset({'offer_change', 'request_deposit', *DATE_ACTIONS})
GUEST_FACING = frozenset(BOOKING_ACTIONS)
FOLLOW_UPS = frozenset({'confirm_email', 'reminder', 'phone_call'})
AUTO_MESSAGES = frozenset({'confirm_email', 'reminder'})  # I1 / I2, sent by n8n
MAX_PER_BOOKING = {'reminder': 2, 'phone_call': 2, 'offer_change': 1, 'request_deposit': 1}
RESPONSES = ('confirmed', 'requested_change', 'cancelled')
GUEST_OUTCOMES = ('no_response',) + RESPONSES
STOP_OUTCOMES = frozenset(RESPONSES)
DATE_OUTCOMES = ('completed',)
PRECHECKS = ('consent_confirmed', 'no_open_conflict', 'booking_active')
CHANNEL = {'confirm_email': 'email', 'reminder': 'email', 'phone_call': 'phone'}
DEFAULT_RULE = {'offer_change': 'I4', 'request_deposit': 'I6'}
# Routine G1 plans a reviewer may approve in a batch; everything else is decided one by one.
BATCH_RULES = frozenset(RULE_ACTIONS)
ATTEMPT_RESULTS = ('accepted', 'refused', 'error')

_SQL_LIST = lambda xs: ','.join(f"'{x}'" for x in xs)  # noqa: E731 - constants only

# Lookups by booking, stay date x room type and business date (the checks run them on every write).
INDEXES = """
CREATE INDEX IF NOT EXISTS ix_action_booking ON action_plan (booking_id);
CREATE INDEX IF NOT EXISTS ix_action_cell ON action_plan (stay_date, room_type);
CREATE INDEX IF NOT EXISTS ix_interventions_as_of ON interventions (as_of);
CREATE INDEX IF NOT EXISTS ix_responses_booking ON guest_responses (booking_id);
CREATE INDEX IF NOT EXISTS ix_responses_action ON guest_responses (action_id);
CREATE INDEX IF NOT EXISTS ix_attempts_action ON execution_attempts (action_id);
"""

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS store_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_plan (
  action_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  booking_id      INTEGER,
  stay_date       TEXT,
  room_type       TEXT,
  action_type     TEXT NOT NULL CHECK (action_type IN ({_SQL_LIST(ACTIONS)})),
  rule_id         TEXT NOT NULL,
  source_run_id   TEXT,
  decision_at     TEXT NOT NULL,
  known_from      TEXT,              -- original F score time; manual calls cannot reset G1 window
  scheduled_at    TEXT,
  arrival_date    TEXT,              -- no contact / change on or after this date
  message_no      INTEGER,
  note            TEXT,              -- e.g. the written policy behind an I6 deposit request
  basis_event_id  INTEGER REFERENCES response_events(event_id),  -- I4: the change request answered
  needs_approval  INTEGER NOT NULL CHECK (needs_approval IN (0,1)),
  approval_status TEXT NOT NULL CHECK (approval_status IN ('auto','pending','approved','rejected')),
  approved_by     TEXT,
  approved_at     TEXT,
  created_at      TEXT NOT NULL,
  CHECK (needs_approval = 0 OR approval_status <> 'auto'),
  CHECK ((approval_status IN ('approved','rejected') AND approved_by IS NOT NULL
          AND approved_at IS NOT NULL) OR
         (approval_status IN ('auto','pending') AND approved_by IS NULL AND approved_at IS NULL)),
  CHECK (approved_at IS NULL OR approved_at >= created_at),
  CHECK (scheduled_at IS NULL OR scheduled_at >= decision_at),
  CHECK (arrival_date IS NULL OR decision_at < arrival_date),
  CHECK (action_type NOT IN ({_SQL_LIST(sorted(APPROVAL_REQUIRED))}) OR needs_approval = 1),
  CHECK (action_type NOT IN ({_SQL_LIST(BOOKING_ACTIONS)}) OR booking_id IS NOT NULL),
  CHECK (action_type NOT IN ({_SQL_LIST(DATE_ACTIONS)})
         OR (stay_date IS NOT NULL AND room_type IS NOT NULL)),
  CHECK (action_type <> 'request_deposit' OR (note IS NOT NULL AND trim(note) <> '')),
  CHECK (action_type <> 'offer_change' OR basis_event_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_action_schedule
  ON action_plan (booking_id, action_type, scheduled_at) WHERE scheduled_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS interventions (
  intervention_id INTEGER PRIMARY KEY AUTOINCREMENT,
  action_id       INTEGER NOT NULL UNIQUE REFERENCES action_plan(action_id),
  channel         TEXT NOT NULL CHECK (channel IN ('task','email','sms','phone','pms')),
  as_of           TEXT NOT NULL,     -- business (replay) date of the execution
  executed_at     TEXT NOT NULL,     -- wall-clock time the demo row was written
  executed_by     TEXT NOT NULL CHECK (executed_by IN ('staff','n8n')),
  outcome         TEXT NOT NULL CHECK (outcome IN ({_SQL_LIST(GUEST_OUTCOMES + DATE_OUTCOMES)})),
  cost            REAL,
  precheck        TEXT NOT NULL
);

-- A guest answer that arrives after the contact was logged (e.g. confirms the next day).
CREATE TABLE IF NOT EXISTS guest_responses (
  response_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  booking_id    INTEGER NOT NULL,
  action_id     INTEGER NOT NULL REFERENCES action_plan(action_id),   -- the contact answered
  response      TEXT NOT NULL CHECK (response IN ({_SQL_LIST(RESPONSES)})),
  response_at   TEXT NOT NULL,     -- business (replay) date of the answer
  recorded_at   TEXT NOT NULL
);

-- A single persisted sequence across both logs. Wall-clock seconds and the two
-- independent table IDs cannot order a late answer followed by a contact outcome.
CREATE TABLE IF NOT EXISTS response_events (
  event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  intervention_id INTEGER UNIQUE REFERENCES interventions(intervention_id),
  response_id     INTEGER UNIQUE REFERENCES guest_responses(response_id),
  CHECK ((intervention_id IS NOT NULL) <> (response_id IS NOT NULL))
);
CREATE TRIGGER IF NOT EXISTS sequence_intervention
AFTER INSERT ON interventions FOR EACH ROW
BEGIN INSERT INTO response_events (intervention_id) VALUES (NEW.intervention_id); END;
CREATE TRIGGER IF NOT EXISTS sequence_response
AFTER INSERT ON guest_responses FOR EACH ROW
BEGIN INSERT INTO response_events (response_id) VALUES (NEW.response_id); END;
CREATE TRIGGER IF NOT EXISTS guard_response_events_update
BEFORE UPDATE ON response_events FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Response event order is append-only'); END;
CREATE TRIGGER IF NOT EXISTS guard_response_events_delete
BEFORE DELETE ON response_events FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Response event order is append-only'); END;

-- §6.2: execution only for approved or auto actions.
CREATE TRIGGER IF NOT EXISTS guard_interventions
BEFORE INSERT ON interventions FOR EACH ROW
WHEN NOT EXISTS (
  SELECT 1 FROM action_plan a
  WHERE a.action_id = NEW.action_id
    AND (a.approval_status = 'approved' OR (a.approval_status = 'auto' AND a.needs_approval = 0)))
BEGIN SELECT RAISE(ABORT, 'Blocked: action lacks required approval'); END;

-- A late response answers a contact that was actually made, on or after that day.
CREATE TRIGGER IF NOT EXISTS guard_responses
BEFORE INSERT ON guest_responses FOR EACH ROW
WHEN NOT EXISTS (
  SELECT 1 FROM interventions i JOIN action_plan a USING (action_id)
  WHERE i.action_id = NEW.action_id AND a.booking_id = NEW.booking_id
    AND i.as_of <= NEW.response_at)
BEGIN SELECT RAISE(ABORT, 'A response needs an executed contact of this booking on or before it'); END;

-- The logs are append-only: a row cannot be re-pointed to another action or removed.
CREATE TRIGGER IF NOT EXISTS guard_interventions_update
BEFORE UPDATE ON interventions FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Intervention log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS guard_interventions_delete
BEFORE DELETE ON interventions FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Intervention log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS guard_responses_update
BEFORE UPDATE ON guest_responses FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Response log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS guard_responses_delete
BEFORE DELETE ON guest_responses FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Response log is append-only'); END;

-- An open action closed WITHOUT contact (e.g. a G1 call whose window has passed). Not an
-- execution: it frees the booking for a manual review, and the action can never run after.
CREATE TABLE IF NOT EXISTS action_closures (
  closure_id  INTEGER PRIMARY KEY AUTOINCREMENT,
  action_id   INTEGER NOT NULL UNIQUE REFERENCES action_plan(action_id),
  reason      TEXT NOT NULL CHECK (trim(reason) <> ''),
  closed_by   TEXT NOT NULL CHECK (trim(closed_by) <> ''),
  as_of       TEXT NOT NULL,     -- business (replay) date of the closure
  closed_at   TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS guard_closures_executed
BEFORE INSERT ON action_closures FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM interventions WHERE action_id = NEW.action_id)
BEGIN SELECT RAISE(ABORT, 'An executed action cannot be closed'); END;
CREATE TRIGGER IF NOT EXISTS guard_interventions_closed
BEFORE INSERT ON interventions FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM action_closures WHERE action_id = NEW.action_id)
BEGIN SELECT RAISE(ABORT, 'Blocked: action was closed without contact'); END;
CREATE TRIGGER IF NOT EXISTS guard_closures_update
BEFORE UPDATE ON action_closures FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Closure log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS guard_closures_delete
BEFORE DELETE ON action_closures FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Closure log is append-only'); END;

-- G3 overbooking is disabled by policy in this version: no approval or direct write enables it.
CREATE TRIGGER IF NOT EXISTS guard_g3_disabled
BEFORE INSERT ON action_plan FOR EACH ROW WHEN NEW.action_type = 'overbooking'
BEGIN SELECT RAISE(ABORT, 'G3 overbooking is disabled by policy'); END;
CREATE TRIGGER IF NOT EXISTS guard_interventions_g3
BEFORE INSERT ON interventions FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM action_plan WHERE action_id = NEW.action_id AND action_type = 'overbooking')
BEGIN SELECT RAISE(ABORT, 'G3 overbooking is disabled by policy'); END;

-- G1 daily call capacity, also for direct writers (SQLite has one writer at a time).
CREATE TRIGGER IF NOT EXISTS guard_call_capacity
BEFORE INSERT ON interventions FOR EACH ROW
WHEN (SELECT action_type FROM action_plan WHERE action_id = NEW.action_id) = 'phone_call'
 AND (SELECT COUNT(*) FROM interventions i JOIN action_plan a USING (action_id)
      WHERE a.action_type = 'phone_call' AND i.as_of = NEW.as_of)
     >= (SELECT CAST(value AS INTEGER) FROM store_meta WHERE key = 'call_capacity_per_day')
BEGIN SELECT RAISE(ABORT, 'Daily call capacity reached'); END;

-- §6.3: a decision is final; only pending -> approved / rejected, never back or to auto.
CREATE TRIGGER IF NOT EXISTS guard_approval_transition
BEFORE UPDATE OF approval_status, approved_by, approved_at, needs_approval ON action_plan
FOR EACH ROW
WHEN OLD.approval_status <> 'pending'
  OR NEW.approval_status NOT IN ('approved','rejected')
  OR NEW.needs_approval <> OLD.needs_approval
BEGIN SELECT RAISE(ABORT, 'Approval decision is final (only pending -> approved/rejected)'); END;

-- What was approved cannot be swapped afterwards.
CREATE TRIGGER IF NOT EXISTS guard_action_identity
BEFORE UPDATE OF booking_id, stay_date, room_type, action_type, rule_id, source_run_id,
                 decision_at, known_from, scheduled_at, arrival_date, message_no, note,
                 basis_event_id, created_at
ON action_plan FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Action identity is immutable'); END;

-- Staff review of a booking's routine G1 plan (I1-I3): one final decision per booking.
CREATE TABLE IF NOT EXISTS plan_reviews (
  review_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  booking_id  INTEGER NOT NULL UNIQUE,
  rule_id     TEXT NOT NULL CHECK (rule_id IN ({_SQL_LIST(sorted(RULE_ACTIONS))})),
  decision    TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
  reviewer    TEXT NOT NULL CHECK (trim(reviewer) <> ''),
  reason      TEXT,
  batch_id    TEXT,              -- set when decided in a batch
  as_of       TEXT NOT NULL,     -- business (replay) date of the decision
  reviewed_at TEXT NOT NULL,
  CHECK (decision <> 'rejected' OR (reason IS NOT NULL AND trim(reason) <> ''))
);
CREATE TRIGGER IF NOT EXISTS guard_plan_reviews_update
BEFORE UPDATE ON plan_reviews FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Plan review log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS guard_plan_reviews_delete
BEFORE DELETE ON plan_reviews FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Plan review log is append-only'); END;
-- A rejected G1 plan is never imported afterwards (manual actions remain possible).
CREATE TRIGGER IF NOT EXISTS guard_rejected_plan
BEFORE INSERT ON action_plan FOR EACH ROW
WHEN NEW.scheduled_at IS NOT NULL AND EXISTS (
  SELECT 1 FROM plan_reviews WHERE booking_id = NEW.booking_id AND decision = 'rejected')
BEGIN SELECT RAISE(ABORT, 'Blocked: the G1 plan of this booking was rejected at review'); END;

-- Every hand-over to automation (n8n) and its result; accepted ones point to the execution.
CREATE TABLE IF NOT EXISTS execution_attempts (
  attempt_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  action_id       INTEGER NOT NULL REFERENCES action_plan(action_id),
  executor        TEXT NOT NULL CHECK (executor IN ('n8n')),
  result          TEXT NOT NULL CHECK (result IN ({_SQL_LIST(ATTEMPT_RESULTS)})),
  detail          TEXT,
  intervention_id INTEGER UNIQUE REFERENCES interventions(intervention_id),
  as_of           TEXT NOT NULL,     -- business (replay) date of the attempt
  attempted_at    TEXT NOT NULL,
  CHECK ((result = 'accepted') = (intervention_id IS NOT NULL))
);
CREATE TRIGGER IF NOT EXISTS guard_attempts_update
BEFORE UPDATE ON execution_attempts FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Execution attempt log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS guard_attempts_delete
BEFORE DELETE ON execution_attempts FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'Execution attempt log is append-only'); END;
""" + INDEXES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _iso(d) -> str | None:
    if d is None:
        return None
    return d.isoformat() if isinstance(d, (date, datetime)) else str(d)


def connect(db_path: str | Path, *, run_id: str | None = None, call_capacity: int | None = None,
            execution_policy: dict | None = None,
            check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the store. With run_id, the store is bound to that run (refuses another one); the
    dashboard passes F run + G1 policy id. call_capacity: calls the team can make per business
    day (G1 policy); executions beyond it are refused. Fixed for the life of the store.

    db_path: a SQLite file (local demo) or "postgresql://...#schema" (shared store; one
    PostgreSQL schema per store, see dashboard/pg_backend.py). Same rules on both."""
    from dashboard import pg_backend
    if pg_backend.is_pg(db_path):
        con = pg_backend.connect(str(db_path))
        path = Path(con.schema)  # used in messages only
        tables = con.tables()
        schema_sql = pg_backend.pg_schema_sql()
    else:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=15, check_same_thread=check_same_thread)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys = ON')
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        schema_sql = SCHEMA
    old_tables = 'action_plan' in tables
    has_meta = 'store_meta' in tables
    if old_tables and not has_meta:
        con.close()
        raise ValueError(f'{path.name} was made by an older G2 version: keep it for reference '
                         'and choose a new G2_DB_PATH for this demo')
    if has_meta:
        version = con.execute("SELECT value FROM store_meta WHERE key = 'schema_version'").fetchone()
        if version and version['value'] != SCHEMA_VERSION:
            con.close()
            raise ValueError(f"{path.name} has schema {version['value']}, expected "
                             f'{SCHEMA_VERSION}: keep the old log and choose a new G2_DB_PATH')
    # An existing store of this version already has its DDL, and opening it writes nothing:
    # a session that opens the store never waits for another session's unit of work.
    if not has_meta:
        con.executescript(schema_sql)
    meta = dict(con.execute('SELECT key, value FROM store_meta').fetchall())
    saved_policy = meta.get('execution_policy')
    if execution_policy is not None:
        execution_policy = normalize_execution_policy(execution_policy)
        encoded = json.dumps(execution_policy, sort_keys=True)
        if saved_policy is not None and saved_policy != encoded:
            con.close()
            raise ValueError('Execution policy is fixed per store: use a new state DB')
    else:
        encoded = saved_policy
    if '+g1-' in (run_id or meta.get('run_id', '')) and encoded is None:
        con.close()
        raise ValueError('A G1 store requires its execution policy')
    if run_id is not None and meta.get('run_id', run_id) != run_id:
        con.close()
        raise ValueError(f"{path.name} holds actions of run {meta['run_id']}, not {run_id}. "
                         'Use one state DB per run (the dashboard does this by default).')
    if call_capacity is not None and int(meta.get('call_capacity_per_day', call_capacity)) != int(call_capacity):
        con.close()
        raise ValueError(f"{path.name} was opened with {meta['call_capacity_per_day']} calls/day, "
                         f'not {call_capacity}: use a new state DB')
    missing = {'schema_version': SCHEMA_VERSION, 'run_id': run_id,
               'call_capacity_per_day': None if call_capacity is None else str(int(call_capacity)),
               'execution_policy': encoded}
    missing = {k: v for k, v in missing.items() if v is not None and k not in meta}
    for key, value in missing.items():
        con.execute('INSERT INTO store_meta VALUES (?, ?) ON CONFLICT (key) DO NOTHING', (key, value))
    if missing:
        con.commit()
    return con


_LOCK = "UPDATE store_meta SET value = value WHERE key = 'schema_version'"


@contextmanager
def _unit(con):
    """One unit of work: store write lock first, then checks and writes, then commit (rollback on
    any error, so a refused or interrupted unit leaves nothing behind)."""
    begin = getattr(con, 'begin', None)  # PostgreSQL: explicit transaction
    if begin is not None:
        begin()
    try:
        con.execute(_LOCK)
        yield
    except BaseException:
        con.rollback()
        raise
    con.commit()


def normalize_execution_policy(policy: dict) -> dict:
    """Only the currently supported operational G1 policy may enter the store."""
    if policy.get('overbooking', {}).get('enabled') is not False:
        raise ValueError('G3 overbooking is disabled in this version')
    call = policy.get('call_schedule', {})
    keys = ('start_day', 'window_days', 'min_days_before_arrival')
    if any(type(call.get(k)) is not int for k in keys):
        raise ValueError('G1 call schedule needs integer limits')
    if not (0 <= call['start_day'] < call['window_days'] and call['min_days_before_arrival'] >= 8):
        raise ValueError('Invalid G1 call window or arrival lead')
    return {'overbooking': {'enabled': False}, 'call_schedule': {k: call[k] for k in keys}}


def execution_policy(con: sqlite3.Connection) -> dict:
    row = con.execute("SELECT value FROM store_meta WHERE key = 'execution_policy'").fetchone()
    # Legacy non-G1 unit/demo stores have no call policy; overbooking remains disabled.
    return json.loads(row['value']) if row else {'overbooking': {'enabled': False}}


def _check_enabled(action_type: str) -> None:
    if action_type == 'overbooking':
        raise PermissionError('G3 overbooking is disabled by policy; approval cannot enable it')


def _check_call_window(con, action_type, when, known_from, arrival_date) -> None:
    call = execution_policy(con).get('call_schedule')
    if action_type != 'phone_call' or call is None:
        return
    if known_from is None or arrival_date is None:
        raise ValueError('G1 phone calls require original F score time and arrival date')
    day = date.fromisoformat(when)
    offset = (day - date.fromisoformat(known_from)).days
    lead = (date.fromisoformat(arrival_date) - day).days
    if not call['start_day'] <= offset < call['window_days']:
        raise ValueError('Outside G1 call window: manual review required; do not reset the score date '
                         '(close this call without contact to free the booking)')
    if lead < call['min_days_before_arrival']:
        raise ValueError(f"G1 call needs at least {call['min_days_before_arrival']} days before arrival; "
                         'manual review required')


def call_capacity(con: sqlite3.Connection) -> int | None:
    row = con.execute("SELECT value FROM store_meta WHERE key = 'call_capacity_per_day'").fetchone()
    return int(row['value']) if row else None


def calls_made(con: sqlite3.Connection, as_of) -> int:
    """Phone-call tasks already executed on a business date (all bookings)."""
    return con.execute("""SELECT COUNT(*) FROM interventions i JOIN action_plan a USING (action_id)
                          WHERE a.action_type = 'phone_call' AND i.as_of = ?""", (_iso(as_of),)).fetchone()[0]


def store_run_id(con: sqlite3.Connection) -> str | None:
    row = con.execute("SELECT value FROM store_meta WHERE key = 'run_id'").fetchone()
    return row['value'] if row else None


def guest_responses(con: sqlite3.Connection, booking_id: int, as_of=None) -> list[dict]:
    """Responses of a booking known on `as_of` (all if None), oldest first.

    Two sources: the outcome given when a contact was logged, and late responses.
    Each item: date, response, action_type of the contact answered, source, seq.
    Within a business date, seq follows the actual insert order across both logs,
    even if every event was recorded in the same wall-clock second.
    """
    as_of = _iso(as_of)
    rows = con.execute(f"""
      SELECT i.as_of AS date, i.outcome AS response, a.action_type, 'at contact' AS source,
             e.event_id AS seq, 0 AS late
      FROM interventions i JOIN action_plan a USING (action_id)
      JOIN response_events e ON e.intervention_id = i.intervention_id
      WHERE a.booking_id = ? AND i.outcome IN ({_SQL_LIST(RESPONSES)})
        AND (CAST(? AS TEXT) IS NULL OR i.as_of <= ?)
      UNION ALL
      SELECT r.response_at, r.response, a.action_type, 'late', e.event_id, 1
      FROM guest_responses r JOIN action_plan a USING (action_id)
      JOIN response_events e ON e.response_id = r.response_id
      WHERE r.booking_id = ? AND (CAST(? AS TEXT) IS NULL OR r.response_at <= ?)
      ORDER BY 1, seq""", (int(booking_id), as_of, as_of, int(booking_id), as_of, as_of))
    return [dict(r) for r in rows.fetchall()]


def booking_stop_outcome(con: sqlite3.Connection, booking_id: int, as_of=None) -> str | None:
    """Response that stops follow-ups as known on `as_of`: 'cancelled' if any, else the latest."""
    seen = guest_responses(con, booking_id, as_of)
    if any(r['response'] == 'cancelled' for r in seen):
        return 'cancelled'
    return seen[-1]['response'] if seen else None


def offer_basis(con: sqlite3.Connection, booking_id: int, as_of) -> int:
    """Event id of the change request an I4 offer answers: the booking's latest response as of
    `as_of`, which must be a requested_change given on a phone call."""
    seen = guest_responses(con, booking_id, as_of)
    last = seen[-1] if seen else None
    if not (last and last['response'] == 'requested_change'
            and last['action_type'] == 'phone_call'):
        raise ValueError('I4 offer_change only follows a call whose latest response '
                         f'(as of {as_of}) is a change request')
    return int(last['seq'])


def _check_offer_response(con, booking_id: int, as_of, basis: int | None = None) -> int:
    """I4 must still be justified at execution, by the SAME change request it was approved for.
    A newer change request needs a new offer and a new approval."""
    current = offer_basis(con, booking_id, as_of)
    if basis is not None and current != int(basis):
        raise ValueError(f'The guest made a newer change request (event {current}) after this offer was '
                         f'proposed for event {basis}: create and approve a new offer')
    return current


def latest_event(con: sqlite3.Connection, *, booking_id=None, stay_date=None,
                 room_type=None) -> str | None:
    """Latest business date already recorded (execution or response) for a booking / cell."""
    if booking_id is not None:
        row = con.execute("""
          SELECT MAX(d) FROM (
            SELECT i.as_of AS d FROM interventions i JOIN action_plan a USING (action_id)
            WHERE a.booking_id = ?
            UNION ALL SELECT response_at FROM guest_responses WHERE booking_id = ?
            UNION ALL SELECT c.as_of FROM action_closures c JOIN action_plan a USING (action_id)
            WHERE a.booking_id = ?)""",
                          (int(booking_id), int(booking_id), int(booking_id))).fetchone()
    else:
        row = con.execute("""
          SELECT MAX(d) FROM (
            SELECT i.as_of AS d FROM interventions i JOIN action_plan a USING (action_id)
            WHERE a.stay_date = ? AND a.room_type = ?
            UNION ALL SELECT c.as_of FROM action_closures c JOIN action_plan a USING (action_id)
            WHERE a.stay_date = ? AND a.room_type = ?)""",
                          (_iso(stay_date), room_type, _iso(stay_date), room_type)).fetchone()
    return row[0]


def _check_forward(con, when: str, *, booking_id=None, stay_date=None, room_type=None) -> None:
    last = latest_event(con, booking_id=booking_id, stay_date=stay_date, room_type=room_type)
    if last is not None and when < last:
        target = f'booking {booking_id}' if booking_id is not None else f'{stay_date} × {room_type}'
        raise ValueError(f'Back-dated: {target} already has an event on {last}; replay moves forward '
                         f'only (use a new state DB to replay an earlier date)')


def _check_new_action(con, action_type, booking_id, stay_date, room_type, rule_id,
                      source_run_id, decision_at, scheduled_at, known_from, arrival_date,
                      deposit_policy_enabled, risk_tier, deposit_type, note):
    if action_type not in ACTIONS:
        raise ValueError('Unsupported action')
    _check_enabled(action_type)
    basis = None  # I4: event id of the change request the offer answers
    if action_type in BOOKING_ACTIONS and booking_id is None:
        raise ValueError(f'{action_type} is a booking-level action: booking_id is required')
    if action_type in DATE_ACTIONS and (stay_date is None or not room_type):
        raise ValueError(f'{action_type} is a stay date x room type action: '
                         'stay_date and room_type are required')
    if action_type in DATE_ACTIONS and booking_id is not None:
        raise ValueError(f'{action_type} applies to a date x room type, not to one booking')
    if action_type == 'request_deposit':
        # Decision table I6: p >= t_high (tier high), No Deposit, high value, policy allows.
        if not deposit_policy_enabled:
            raise PermissionError('I6 deposit request is off by default (needs a written policy)')
        if risk_tier != 'high':
            raise ValueError(f'I6 needs a high-tier booking (this one: {risk_tier})')
        if deposit_type != 'No Deposit':
            raise ValueError(f'I6 applies to No Deposit bookings (this one: {deposit_type})')
        if not (note or '').strip():
            raise ValueError('I6 needs the written-policy reference that qualifies the booking as '
                             'high value (no value threshold has been set by G1)')

    run = store_run_id(con)
    if run is not None and source_run_id is not None and source_run_id != run:
        raise ValueError(f'Action from F run {source_run_id} cannot enter the store of run {run}')
    if decision_at is None:
        raise ValueError('decision_at is required (the replay date the action is decided)')
    if known_from is not None and decision_at < known_from:
        raise ValueError(f'Not known yet on {decision_at}: F decides this booking on {known_from}')
    if arrival_date is not None and decision_at >= arrival_date:
        raise ValueError(f'Too late on {decision_at}: arrival / stay date is {arrival_date}')
    _check_forward(con, decision_at, booking_id=booking_id, stay_date=stay_date, room_type=room_type)
    _check_call_window(con, action_type, scheduled_at or decision_at, known_from, arrival_date)

    if action_type in BOOKING_ACTIONS:
        stop = booking_stop_outcome(con, booking_id, decision_at)
        if stop == 'cancelled':
            raise ValueError('Booking was cancelled: no further actions')
        if stop and action_type in FOLLOW_UPS:
            raise ValueError(f'Follow-ups stopped: guest already responded ({stop})')
        if action_type == 'offer_change':
            basis = _check_offer_response(con, booking_id, decision_at)
        limit = MAX_PER_BOOKING.get(action_type)
        if limit is not None:
            # An open offer for an older change request is dead (it can never execute), so it
            # neither counts toward "one offer per booking" nor blocks the new offer below.
            n = con.execute("""SELECT COUNT(*) FROM action_plan a
                LEFT JOIN interventions i ON i.action_id = a.action_id
                WHERE a.booking_id = ? AND a.action_type = ? AND a.approval_status <> 'rejected'
                  AND NOT (i.intervention_id IS NULL
                           AND EXISTS (SELECT 1 FROM action_closures c WHERE c.action_id = a.action_id))
                  AND NOT (a.action_type = 'offer_change' AND i.intervention_id IS NULL
                           AND COALESCE(a.basis_event_id, -1) <> ?)""",
                            (booking_id, action_type, basis if basis is not None else -1)).fetchone()[0]
            if n >= limit:
                raise ValueError(f'Limit reached: max {limit} {action_type} per booking')
        target_sql, target = 'booking_id = ?', (booking_id,)
    else:
        target_sql, target = 'stay_date = ? AND room_type = ?', (stay_date, room_type)

    # §2.4: no other OPEN action of the same type. Exception: the rows of ONE F schedule
    # (same rule and decision, each with its own due date, e.g. the two I2 reminders).
    from_schedule = scheduled_at is not None
    clash = con.execute(f"""
      SELECT a.action_id FROM action_plan a
      LEFT JOIN interventions i ON i.action_id = a.action_id
      WHERE {target_sql} AND a.action_type = ? AND a.approval_status <> 'rejected'
        AND i.intervention_id IS NULL
        AND NOT EXISTS (SELECT 1 FROM action_closures c WHERE c.action_id = a.action_id)
        AND NOT (CAST(? AS BOOLEAN) AND a.scheduled_at IS NOT NULL AND a.rule_id = ? AND a.decision_at = ?)
        AND NOT (a.action_type = 'offer_change' AND COALESCE(a.basis_event_id, -1) <> ?)""",
                        (*target, action_type, bool(from_schedule), rule_id, decision_at,
                         basis if basis is not None else -1)).fetchone()
    if clash:
        raise ValueError(f'An open {action_type} action (#{clash[0]}) already exists')
    return basis


def create_action(con: sqlite3.Connection, action_type: str, **kwargs) -> int:
    """Create one action (one unit of work). Arguments: see _create_action."""
    with _unit(con):
        return _create_action(con, action_type, **kwargs)


def _create_action(con: sqlite3.Connection, action_type: str, *, decision_at,
                   booking_id: int | None = None, stay_date=None, room_type: str | None = None,
                   rule_id: str | None = None, source_run_id: str | None = None,
                   scheduled_at=None, known_from=None, arrival_date=None,
                   message_no: int | None = None, deposit_policy_enabled: bool = False,
                   risk_tier: str | None = None, deposit_type: str | None = None,
                   note: str | None = None) -> int:
    """Create one action (inside a unit of work).

    decision_at: replay date the action is decided (F's score time for scheduled actions,
      the replay date for manual ones). known_from: first date F knew the booking / cell
      (score time). arrival_date: arrival (booking) or stay date (date x room type); nothing
      may be decided or executed on or after it. Defaults to stay_date for date actions.
    risk_tier / deposit_type / note: the I6 facts of the booking and the policy reference.
    """
    booking_id = None if booking_id is None else int(booking_id)
    stay_date, decision_at, scheduled_at = _iso(stay_date), _iso(decision_at), _iso(scheduled_at)
    known_from, arrival_date = _iso(known_from), _iso(arrival_date)
    rule_id = rule_id or DEFAULT_RULE.get(action_type, 'G2_DEMO_MANUAL')
    note = note.strip() if note else None
    if action_type in DATE_ACTIONS and arrival_date is None:
        arrival_date = stay_date
    source_run_id = source_run_id or store_run_id(con)
    basis = _check_new_action(con, action_type, booking_id, stay_date, room_type, rule_id,
                              source_run_id, decision_at, scheduled_at, known_from, arrival_date,
                              deposit_policy_enabled, risk_tier, deposit_type, note)
    needs_approval = int(action_type in APPROVAL_REQUIRED)
    cur = con.execute("""
      INSERT INTO action_plan (booking_id, stay_date, room_type, action_type, rule_id,
        source_run_id, decision_at, known_from, scheduled_at, arrival_date, message_no, note,
        basis_event_id, needs_approval, approval_status, created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (booking_id, stay_date, room_type, action_type, rule_id, source_run_id,
                       decision_at, known_from, scheduled_at, arrival_date, message_no, note, basis,
                       needs_approval, 'pending' if needs_approval else 'auto', _now()))
    return cur.lastrowid


def create_actions_from_schedule(con: sqlite3.Connection, schedule_rows: list[dict], *,
                                 arrival_date=None) -> tuple[list[int], int]:
    """Turn F's dated schedule for ONE booking into action_plan rows. Idempotent.

    Returns (created action ids, number already present). A row whose action type is not
    allowed for its rule is refused. A row that matches an existing action on booking / type /
    due date but differs in run, rule or decision time is a conflict and raises; it is never
    skipped silently. I5 has no schedule rows, so it creates nothing. One unit of work: two
    sessions importing the same schedule cannot both create it.
    """
    with _unit(con):
        return _import_schedule(con, schedule_rows, arrival_date)


def _import_schedule(con, schedule_rows, arrival_date):
    created, existing = [], 0
    if len({r['booking_id'] for r in schedule_rows}) > 1:
        raise ValueError('Pass the schedule of one booking at a time')
    for r in schedule_rows:
        if r['action_type'] not in RULE_ACTIONS.get(r['rule'], frozenset()):
            raise ValueError(f"Schedule row refused: rule {r['rule']} does not schedule "
                             f"{r['action_type']} (booking {r['booking_id']})")
    for r in sorted(schedule_rows, key=lambda r: (str(r['scheduled_at']), r['action_type'])):
        found = con.execute("""SELECT * FROM action_plan WHERE booking_id = ? AND
            action_type = ? AND scheduled_at = ?""",
                            (int(r['booking_id']), r['action_type'],
                             _iso(r['scheduled_at']))).fetchone()
        if found:
            same = (found['source_run_id'] == r.get('run_id') and found['rule_id'] == r['rule']
                    and found['decision_at'] == _iso(r['decision_at']))
            if not same:
                raise ValueError(
                    f"Conflict: action #{found['action_id']} ({found['action_type']} "
                    f"{found['scheduled_at']}) came from run {found['source_run_id']} rule "
                    f"{found['rule_id']}; the new schedule says run {r.get('run_id')} rule "
                    f"{r['rule']}. Use a separate state DB for the new run.")
            existing += 1
            continue
        created.append(_create_action(
            con, r['action_type'], booking_id=r['booking_id'], rule_id=r['rule'],
            source_run_id=r.get('run_id'), decision_at=r['decision_at'],
            known_from=r['decision_at'], scheduled_at=r['scheduled_at'],
            arrival_date=arrival_date, message_no=r.get('message_no')))
    return created, existing


def decide_action(con: sqlite3.Connection, action_id: int, reviewer: str, approve: bool) -> None:
    reviewer = (reviewer or '').strip()
    if not reviewer:
        raise ValueError('Enter a reviewer name')
    with _unit(con):
        cur = con.execute("""
          UPDATE action_plan SET approval_status = ?, approved_by = ?, approved_at = ?
          WHERE action_id = ? AND approval_status = 'pending'""",
                          ('approved' if approve else 'rejected', reviewer, _now(), int(action_id)))
        if cur.rowcount != 1:
            raise ValueError('Only pending actions can be reviewed')


def is_executable(action: dict) -> bool:
    return (action['approval_status'] == 'approved'
            or (action['approval_status'] == 'auto' and not action['needs_approval']))


def _same_day_conflict(con, action: dict, as_of: str) -> str | None:
    """§3: never an automated message (I1/I2) on the same day as a call (I3); calls go first."""
    if action['action_type'] not in AUTO_MESSAGES | {'phone_call'}:
        return None
    bid = action['booking_id']
    if action['action_type'] in AUTO_MESSAGES:
        done = con.execute("""SELECT 1 FROM interventions i JOIN action_plan a USING (action_id)
            WHERE a.booking_id = ? AND a.action_type = 'phone_call' AND i.as_of = ?""",
                           (bid, as_of)).fetchone()
        if done:
            return 'a call was made to this booking today; no automated message on the same day'
        due = con.execute("""SELECT a.action_id FROM action_plan a
            LEFT JOIN interventions i ON i.action_id = a.action_id
            WHERE a.booking_id = ? AND a.action_type = 'phone_call' AND i.intervention_id IS NULL
              AND a.approval_status <> 'rejected'
              AND NOT EXISTS (SELECT 1 FROM action_closures c WHERE c.action_id = a.action_id)
              AND COALESCE(a.scheduled_at, a.decision_at) <= ?""", (bid, as_of)).fetchone()
        if due:
            return f'call #{due[0]} is due: the call goes first (I3 > I2 > I1)'
        return None
    sent = con.execute(f"""SELECT a.action_type FROM interventions i JOIN action_plan a USING (action_id)
        WHERE a.booking_id = ? AND a.action_type IN ({_SQL_LIST(sorted(AUTO_MESSAGES))})
          AND i.as_of = ?""", (bid, as_of)).fetchone()
    if sent:
        return f'a {sent[0]} was already sent today; make the call on another day'
    return None


def check_execution(con: sqlite3.Connection, action_id: int, *, as_of, outcome: str,
                    precheck: dict | None = None) -> dict:
    """Every execution check, without writing. Returns the action; raises if blocked."""
    action = get_action(con, action_id)
    if action is None:
        raise ValueError('Action not found')
    _check_enabled(action['action_type'])
    if not is_executable(action):
        raise PermissionError('Action is pending/rejected; execution blocked')
    if con.execute('SELECT 1 FROM interventions WHERE action_id = ?', (action_id,)).fetchone():
        raise ValueError('This action has already been executed')
    closed = con.execute('SELECT reason FROM action_closures WHERE action_id = ?', (action_id,)).fetchone()
    if closed:
        raise ValueError(f"This action was closed without contact ({closed['reason']})")
    as_of = _iso(as_of)
    if as_of < action['decision_at']:
        raise ValueError(f"Cannot execute before the decision date {action['decision_at']}")
    if action['scheduled_at'] and as_of < action['scheduled_at']:
        raise ValueError(f"Not due yet: scheduled for {action['scheduled_at']}")
    if action['arrival_date'] and as_of >= action['arrival_date']:
        raise ValueError(f"Too late: arrival / stay date {action['arrival_date']} reached; "
                         'no contact or change')
    _check_call_window(con, action['action_type'], as_of, action['known_from'], action['arrival_date'])
    _check_forward(con, as_of, booking_id=action['booking_id'], stay_date=action['stay_date'],
                   room_type=action['room_type'])

    precheck = precheck or {}
    if action['action_type'] in GUEST_FACING:
        if outcome not in GUEST_OUTCOMES:
            raise ValueError(f'Outcome must be one of {GUEST_OUTCOMES}')
        failed = [k for k in PRECHECKS if precheck.get(k) is not True]
        if failed:
            raise PermissionError(f'Execution-time checks not passed: {failed}')
        stop = booking_stop_outcome(con, action['booking_id'], as_of)
        if stop == 'cancelled' or (stop and action['action_type'] in FOLLOW_UPS):
            raise ValueError(f'Stopped: guest already responded ({stop})')
        if action['action_type'] == 'offer_change':
            _check_offer_response(con, action['booking_id'], as_of, action['basis_event_id'])
        cap = call_capacity(con)
        if action['action_type'] == 'phone_call' and cap is not None and calls_made(con, as_of) >= cap:
            raise ValueError(f'Daily call capacity reached: {cap} calls already made on {as_of}')
        clash = _same_day_conflict(con, action, as_of)
        if clash:
            raise ValueError(f'Blocked: {clash}')
    elif outcome not in DATE_OUTCOMES:
        raise ValueError(f'Outcome must be one of {DATE_OUTCOMES}')
    return action


def execute_action(con: sqlite3.Connection, action_id: int, *, as_of, outcome: str,
                   precheck: dict | None = None, executed_by: str = 'staff', dispatch=None) -> dict:
    """Check and log a SIMULATED execution in one unit of work. Never contacts guests or
    changes PMS / prices.

    dispatch(action) -> reply: optional external executor (n8n), called inside the unit after
    every check passed, so no other session can execute this action or take the last call slot
    meanwhile. Only a reply with accepted True is logged as an execution; a refusal is logged as
    a refused attempt (execution_attempts), an exception from dispatch rolls everything back
    (the caller logs it with log_attempt_error). Returns the reply plus intervention_id when
    logged."""
    if executed_by not in ('staff', 'n8n'):
        raise ValueError('Invalid executor')
    with _unit(con):
        action = check_execution(con, action_id, as_of=as_of, outcome=outcome, precheck=precheck)
        reply = dict(dispatch(action)) if dispatch is not None else {'accepted': True}
        if reply.get('accepted') is not True:  # HTTP 200 alone is not acceptance
            reasons = reply.get('reasons')  # n8n guard: list of reasons (n8n/src/dispatch.js)
            _log_attempt(con, action_id, as_of, 'refused',
                         '; '.join(map(str, reasons)) if isinstance(reasons, list) and reasons
                         else json.dumps(reply, sort_keys=True, default=str)[:300])
            return reply
        logged = dict(precheck or {})
        logged.update(demo_only=True, note='no guest contacted, no PMS / price / inventory changed')
        cur = con.execute("""
          INSERT INTO interventions (action_id, channel, as_of, executed_at, executed_by, outcome,
                                     precheck)
          VALUES (?,?,?,?,?,?,?)""",
                          (action_id, CHANNEL.get(action['action_type'], 'task'), _iso(as_of), _now(),
                           executed_by, outcome, json.dumps(logged, sort_keys=True)))
        reply['intervention_id'] = cur.lastrowid
        if dispatch is not None:
            _log_attempt(con, action_id, as_of, 'accepted', None, reply['intervention_id'])
    return reply


def record_execution(con: sqlite3.Connection, action_id: int, *, as_of, outcome: str,
                     precheck: dict | None = None, executed_by: str = 'staff') -> int:
    """Log a SIMULATED execution by staff (see execute_action)."""
    return execute_action(con, action_id, as_of=as_of, outcome=outcome, precheck=precheck,
                          executed_by=executed_by)['intervention_id']


def record_response(con: sqlite3.Connection, action_id: int, *, response: str, response_at) -> int:
    """Record a guest answer that arrives after a logged contact (simulated). Append-only."""
    if response not in RESPONSES:
        raise ValueError(f'Response must be one of {RESPONSES}')
    with _unit(con):
        return _record_response(con, action_id, response, response_at)


def _record_response(con, action_id, response, response_at):
    action = get_action(con, action_id)
    if action is None or action['action_type'] not in GUEST_FACING:
        raise ValueError('A response answers a guest contact (booking action)')
    contact = con.execute('SELECT as_of FROM interventions WHERE action_id = ?',
                          (action_id,)).fetchone()
    if contact is None:
        raise ValueError('This contact has not been executed yet: log the contact first')
    response_at = _iso(response_at)
    if response_at < contact['as_of']:
        raise ValueError(f"The response cannot precede the contact ({contact['as_of']})")
    if action['arrival_date'] and response_at >= action['arrival_date']:
        raise ValueError(f"Arrival {action['arrival_date']} reached: no pre-arrival response")
    _check_forward(con, response_at, booking_id=action['booking_id'])
    if booking_stop_outcome(con, action['booking_id'], response_at) == 'cancelled':
        raise ValueError('Booking already cancelled')
    cur = con.execute("""INSERT INTO guest_responses (booking_id, action_id, response, response_at,
                         recorded_at) VALUES (?,?,?,?,?)""",
                      (action['booking_id'], action_id, response, response_at, _now()))
    return cur.lastrowid


def close_action(con: sqlite3.Connection, action_id: int, *, reviewer: str, reason: str, as_of) -> int:
    """Close an open auto / approved action WITHOUT contact (e.g. a call whose G1 window has
    passed). Logged with who, why and the business date; the action can never run afterwards,
    no longer blocks reminders, and does not count as a call attempt. Pending actions are
    rejected instead (decide_action)."""
    reviewer, reason = (reviewer or '').strip(), (reason or '').strip()
    if not reviewer or not reason:
        raise ValueError('Enter who closes the action and why')
    with _unit(con):  # same lock as execute_action: never both closed and executed
        return _close_action(con, action_id, reviewer, reason, as_of)


def _close_action(con, action_id, reviewer, reason, as_of):
    action = get_action(con, action_id)
    if action is None:
        raise ValueError('Action not found')
    if action['approval_status'] not in ('auto', 'approved'):
        raise ValueError('Only auto or approved actions can be closed; reject a pending one instead')
    if con.execute('SELECT 1 FROM interventions WHERE action_id = ?', (action_id,)).fetchone():
        raise ValueError('This action has already been executed')
    if con.execute('SELECT 1 FROM action_closures WHERE action_id = ?', (action_id,)).fetchone():
        raise ValueError('This action is already closed')
    as_of = _iso(as_of)
    if as_of < action['decision_at']:
        raise ValueError(f"Cannot close before the decision date {action['decision_at']}")
    _check_forward(con, as_of, booking_id=action['booking_id'], stay_date=action['stay_date'],
                   room_type=action['room_type'])
    cur = con.execute("""INSERT INTO action_closures (action_id, reason, closed_by, as_of, closed_at)
                         VALUES (?,?,?,?,?)""", (action_id, reason, reviewer, as_of, _now()))
    return cur.lastrowid


def replace_action(con: sqlite3.Connection, action_id: int, new_type: str, *, reviewer: str,
                   reason: str, as_of, deposit_policy_enabled: bool = False,
                   risk_tier: str | None = None, deposit_type: str | None = None,
                   note: str | None = None) -> tuple[int, int]:
    """Edit = replace: close an open booking action without contact and propose another one for
    the same booking, decided on the replay date, in ONE unit of work. Every rule of a new
    action applies (approval for I4 / I6, limits, call window from the original score date); if
    the new action is refused, the old one stays open. Returns (closure id, new action id)."""
    reviewer, reason = (reviewer or '').strip(), (reason or '').strip()
    if not reviewer or not reason:
        raise ValueError('Enter who changes the action and why')
    with _unit(con):
        old = get_action(con, action_id)
        if old is None or old['booking_id'] is None:
            raise ValueError('Only booking actions can be replaced here')
        closure = _close_action(con, action_id, reviewer,
                                f'Replaced by {new_type}: {reason}', as_of)
        new = _create_action(con, new_type, booking_id=old['booking_id'], decision_at=as_of,
                             known_from=old['known_from'] or old['decision_at'],
                             arrival_date=old['arrival_date'], source_run_id=old['source_run_id'],
                             deposit_policy_enabled=deposit_policy_enabled, risk_tier=risk_tier,
                             deposit_type=deposit_type, note=note)
        return closure, new


def get_actions(con: sqlite3.Connection, booking_id: int | None = None) -> list[dict]:
    where, args = ('WHERE a.booking_id = ?', (int(booking_id),)) if booking_id is not None else ('', ())
    return [dict(r) for r in con.execute(f"""
      SELECT a.*, i.intervention_id, i.as_of AS executed_on, i.executed_at, i.executed_by,
             i.outcome,
             (SELECT r.response || ' ' || r.response_at FROM guest_responses r
              WHERE r.action_id = a.action_id ORDER BY r.response_id DESC LIMIT 1) AS late_response,
             (SELECT c.as_of || ': ' || c.reason FROM action_closures c
              WHERE c.action_id = a.action_id) AS closed
      FROM action_plan a LEFT JOIN interventions i ON a.action_id = i.action_id
      {where} ORDER BY a.action_id DESC""", args).fetchall()]


def get_action(con: sqlite3.Connection, action_id: int) -> dict | None:
    row = con.execute('SELECT * FROM action_plan WHERE action_id = ?', (int(action_id),)).fetchone()
    return dict(row) if row else None


def approval_kpis(con: sqlite3.Connection) -> dict:
    """KPI P3 (kpi_register.csv): median approval turnaround in hours; open ones counted apart."""
    rows = con.execute("""SELECT approval_status, created_at, approved_at FROM action_plan
                          WHERE needs_approval = 1""").fetchall()
    hours = [(datetime.fromisoformat(r['approved_at']) - datetime.fromisoformat(r['created_at']))
             .total_seconds() / 3600 for r in rows if r['approval_status'] == 'approved']
    count = lambda s: sum(r['approval_status'] == s for r in rows)  # noqa: E731
    return {'needs_approval': len(rows), 'approved': count('approved'),
            'rejected': count('rejected'), 'pending': count('pending'),
            'median_turnaround_h': round(median(hours), 2) if hours else None}


# ---------------------------------------------------------------- schema 9: plan reviews
def plan_review(con: sqlite3.Connection, booking_id: int) -> dict | None:
    row = con.execute('SELECT * FROM plan_reviews WHERE booking_id = ?', (int(booking_id),)).fetchone()
    return dict(row) if row else None


def plan_reviews(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute('SELECT * FROM plan_reviews ORDER BY review_id').fetchall()]


def _review_plan(con, booking_id, schedule_rows, arrival_date, *, reviewer, approve, reason,
                 as_of, batch_id):
    booking_id = int(booking_id)
    reviewer, reason, as_of = (reviewer or '').strip(), (reason or '').strip() or None, _iso(as_of)
    if not reviewer:
        raise ValueError('Enter a reviewer name')
    if not approve and not reason:
        raise ValueError('Give a reason for rejecting the recommendation')
    if not schedule_rows:
        raise ValueError(f'Booking {booking_id} has no G1 plan to review (manual decision needed)')
    if {int(r['booking_id']) for r in schedule_rows} != {booking_id}:
        raise ValueError('Pass the G1 plan of this booking only')
    rules = {r['rule'] for r in schedule_rows}
    if len(rules) != 1 or not rules <= BATCH_RULES:
        raise ValueError(f'Only routine G1 plans (I1-I3) are reviewed here, not {sorted(rules)}')
    done = plan_review(con, booking_id)
    if done:
        raise ValueError(f"Booking {booking_id} was already {done['decision']} by {done['reviewer']} "
                         f"on {done['as_of']}")
    decided = min(_iso(r['decision_at']) for r in schedule_rows)
    if as_of < decided:
        raise ValueError(f'Not known yet on {as_of}: F scores this booking on {decided}')
    if arrival_date is not None and as_of >= _iso(arrival_date):
        raise ValueError(f'Too late on {as_of}: arrival is {_iso(arrival_date)}')
    con.execute("""INSERT INTO plan_reviews (booking_id, rule_id, decision, reviewer, reason, batch_id,
                                             as_of, reviewed_at) VALUES (?,?,?,?,?,?,?,?)""",
                (booking_id, rules.pop(), 'approved' if approve else 'rejected', reviewer, reason,
                 batch_id, as_of, _now()))
    return _import_schedule(con, schedule_rows, arrival_date) if approve else ([], 0)


def review_plan(con: sqlite3.Connection, booking_id: int, schedule_rows: list[dict], *,
                arrival_date=None, reviewer: str, approve: bool, reason: str | None = None,
                as_of, batch_id: str | None = None) -> tuple[list[int], int]:
    """Approve (import into action_plan) or reject one booking's routine G1 plan, in one unit of
    work. Final: a second review of the same booking is refused. Returns (created ids, existing)."""
    with _unit(con):
        return _review_plan(con, booking_id, schedule_rows, arrival_date, reviewer=reviewer,
                            approve=approve, reason=reason, as_of=as_of, batch_id=batch_id)


def review_plans(con: sqlite3.Connection, plans: list[tuple[int, list[dict], object]], *,
                 reviewer: str, approve: bool = True, reason: str | None = None, as_of,
                 batch_id: str | None = None) -> list[dict]:
    """Batch review: one unit of work per booking, so a refused booking is reported and the
    others still go through. plans: (booking_id, schedule rows, arrival date). Each result:
    booking_id, status ('approved' / 'rejected' / 'skipped'), created, message."""
    batch_id = batch_id or f'batch-{_now()}'
    out = []
    for booking_id, rows, arrival in plans:
        try:
            created, _ = review_plan(con, booking_id, rows, arrival_date=arrival, reviewer=reviewer,
                                     approve=approve, reason=reason, as_of=as_of, batch_id=batch_id)
        except (ValueError, PermissionError, sqlite3.Error) as e:
            out.append({'booking_id': int(booking_id), 'status': 'skipped', 'created': 0,
                        'message': str(e)})
        else:
            out.append({'booking_id': int(booking_id), 'status': 'approved' if approve else 'rejected',
                        'created': len(created), 'message': ''})
    return out


# ---------------------------------------------------------------- schema 9: automation attempts
def _log_attempt(con, action_id, as_of, result, detail=None, intervention_id=None) -> int:
    cur = con.execute("""INSERT INTO execution_attempts (action_id, executor, result, detail,
                           intervention_id, as_of, attempted_at) VALUES (?,?,?,?,?,?,?)""",
                      (int(action_id), 'n8n', result, (str(detail)[:500] if detail else None),
                       intervention_id, _iso(as_of), _now()))
    return cur.lastrowid


def log_attempt_error(con: sqlite3.Connection, action_id: int, *, as_of, detail: str) -> int:
    """Record an automation hand-over that failed before any answer (network, HTTP, bad JSON).
    Its own unit of work: the failed execution unit was rolled back."""
    with _unit(con):
        return _log_attempt(con, action_id, as_of, 'error', detail)


def execution_attempts(con: sqlite3.Connection, action_id: int | None = None) -> list[dict]:
    where, args = ('WHERE action_id = ?', (int(action_id),)) if action_id is not None else ('', ())
    return [dict(r) for r in con.execute(
        f'SELECT * FROM execution_attempts {where} ORDER BY attempt_id', args).fetchall()]
