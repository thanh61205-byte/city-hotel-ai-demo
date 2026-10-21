"""Durable state of AI email drafts: requests, immutable revisions, approvals, send jobs, events.

Back ends
- PostgreSQL (shared, required for real test emails): schema g2_ai_v1 created by the bootstrap
  from sql/g2_ai_email_postgres.sql. The workflow steps n8n runs (claim / complete a generation,
  claim / begin / finish a send job) are SQL functions there; this module calls the same
  functions for the local mock executor, so n8n and Streamlit share one implementation.
- SQLite (local offline demo and tests only): same tables and guards; the workflow steps are
  mirrored in Python (_py_* methods). Real test emails are refused on SQLite: approval and
  de-duplication must survive a restart of Streamlit Community Cloud.

Protocol (docs/SPEC_AI_N8N_VI.md §6.1): approval -> send job with a logical send key
(workspace + revision + approval + recipient + mode + sender: never a browser session or a new
UUID per click) -> queued -> claimed (lease) -> dispatching (committed before the provider
call) -> simulated | provider_accepted | failed | unknown. Lock order is always approval, then
job. No network call happens inside a transaction of this module.

Nothing here writes the G2 action log (action_plan, interventions, guest_responses): an AI label
is not a booking state and a DEMO test email is not a guest contact.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard.ai_email import contract as c, render, validate

SCHEMA = 'g2_ai_v1'
SQL_FILE = Path(__file__).resolve().parents[2] / 'sql' / 'g2_ai_email_postgres.sql'
APPROVAL_TTL = timedelta(hours=24)
GENERATION_LEASE = timedelta(minutes=3)
SEND_LEASE = timedelta(minutes=2)
DISPATCH_STALE = timedelta(minutes=5)
TERMINAL = ('simulated', 'provider_accepted', 'failed', 'unknown')
JSON_COLS = {'context_json', 'result_json', 'validation_errors', 'token_usage', 'analysis_json',
             'missing_information', 'send_blockers', 'detail_json', 'detail'}
NOT_JSON = {'send_jobs.detail'}
SCHEMA_RE = re.compile(r'^[a-z_][a-z0-9_]{0,62}$')

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO meta (key, value) VALUES ('ai_schema_version', '1');
CREATE TABLE IF NOT EXISTS workspaces (
  workspace_id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL, policy_version TEXT NOT NULL,
  business_as_of TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_recipients (
  recipient_id TEXT PRIMARY KEY CHECK (recipient_id <> 'mock-preview'),
  address TEXT NOT NULL UNIQUE CHECK (address = lower(address) AND address LIKE '%_@_%._%'),
  active INTEGER NOT NULL DEFAULT 1, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS generation_requests (
  request_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces (workspace_id),
  demo_session_id TEXT,
  email_type TEXT NOT NULL CHECK (email_type IN ('reminder','booking_confirmation','guest_reply')),
  booking_id INTEGER, language TEXT NOT NULL CHECK (language IN ('en','vi')),
  context_json TEXT NOT NULL, context_hash TEXT NOT NULL CHECK (length(context_hash) = 64),
  engine TEXT NOT NULL CHECK (engine IN ('n8n','local')),
  requested_mode TEXT NOT NULL CHECK (requested_mode IN ('live_ai','fixture','manual')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','succeeded','failed')),
  lease_token TEXT, lease_expires_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  result_json TEXT, validation_errors TEXT,
  analysis_mode TEXT CHECK (analysis_mode IS NULL OR analysis_mode IN ('live_ai','fixture','manual')),
  model TEXT, prompt_version TEXT, token_usage TEXT, error TEXT, requested_by TEXT,
  created_at TEXT NOT NULL, claimed_at TEXT, completed_at TEXT,
  UNIQUE (request_id, workspace_id),
  CHECK (json_extract(context_json, '$.email_type') = email_type),
  CHECK ((email_type = 'guest_reply') = COALESCE(json_type(context_json, '$.guest_message') = 'object', 0)),
  CHECK (status <> 'succeeded' OR (result_json IS NOT NULL AND analysis_mode IS NOT NULL)),
  CHECK ((status = 'processing') = (lease_token IS NOT NULL)));
CREATE TABLE IF NOT EXISTS draft_revisions (
  revision_id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, request_id TEXT NOT NULL,
  revision_no INTEGER NOT NULL CHECK (revision_no >= 1), email_type TEXT NOT NULL, booking_id INTEGER,
  language TEXT NOT NULL, context_hash TEXT NOT NULL, editable_subject TEXT NOT NULL,
  editable_body TEXT NOT NULL, rendered_subject TEXT NOT NULL CHECK (rendered_subject LIKE '[DEMO] %'),
  rendered_body TEXT NOT NULL, content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
  analysis_json TEXT, analysis_mode TEXT NOT NULL CHECK (analysis_mode IN ('live_ai','fixture','manual')),
  missing_information TEXT NOT NULL DEFAULT '[]', send_blockers TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL CHECK (source IN ('generated','staff_edit')), created_by TEXT,
  demo_session_id TEXT, created_at TEXT NOT NULL,
  UNIQUE (request_id, revision_no), UNIQUE (revision_id, workspace_id),
  FOREIGN KEY (request_id, workspace_id) REFERENCES generation_requests (request_id, workspace_id));
CREATE TABLE IF NOT EXISTS approvals (
  approval_id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, revision_id INTEGER NOT NULL,
  email_type TEXT NOT NULL, booking_id INTEGER, context_hash TEXT NOT NULL, content_hash TEXT NOT NULL,
  recipient_id TEXT NOT NULL, recipient_address TEXT NOT NULL,
  sender_ref TEXT NOT NULL CHECK (trim(sender_ref) <> ''),
  delivery_mode TEXT NOT NULL CHECK (delivery_mode IN ('mock','team_test_email')),
  reviewer_label TEXT NOT NULL CHECK (trim(reviewer_label) <> ''), approved_at TEXT NOT NULL,
  expires_at TEXT NOT NULL, revoked_at TEXT, revoked_by TEXT, revoke_reason TEXT, demo_session_id TEXT,
  UNIQUE (approval_id, workspace_id),
  FOREIGN KEY (revision_id, workspace_id) REFERENCES draft_revisions (revision_id, workspace_id),
  CHECK ((revoked_at IS NULL) = (revoke_reason IS NULL)),
  CHECK ((delivery_mode = 'mock') = (recipient_id = 'mock-preview')),
  CHECK (expires_at > approved_at));
CREATE TABLE IF NOT EXISTS send_jobs (
  job_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, approval_id INTEGER NOT NULL,
  revision_id INTEGER NOT NULL, send_key TEXT NOT NULL UNIQUE CHECK (length(send_key) = 64),
  delivery_mode TEXT NOT NULL CHECK (delivery_mode IN ('mock','team_test_email')),
  recipient_id TEXT NOT NULL, recipient_address TEXT NOT NULL, sender_ref TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN
    ('queued','claimed','dispatching','simulated','provider_accepted','failed','unknown')),
  version INTEGER NOT NULL DEFAULT 1, lease_token TEXT, lease_expires_at TEXT, executor TEXT,
  provider_message_id TEXT, detail TEXT, receipt_verified_by TEXT, receipt_verified_at TEXT,
  requested_by TEXT, demo_session_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  dispatch_started_at TEXT, finished_at TEXT,
  FOREIGN KEY (approval_id, workspace_id) REFERENCES approvals (approval_id, workspace_id),
  FOREIGN KEY (revision_id, workspace_id) REFERENCES draft_revisions (revision_id, workspace_id),
  CHECK ((status IN ('claimed','dispatching')) = (lease_token IS NOT NULL)),
  CHECK (status <> 'simulated' OR delivery_mode = 'mock'),
  CHECK (status <> 'provider_accepted' OR delivery_mode = 'team_test_email'));
CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL REFERENCES workspaces (workspace_id),
  entity TEXT NOT NULL CHECK (entity IN ('request','revision','approval','job')), entity_id TEXT NOT NULL,
  kind TEXT NOT NULL, from_status TEXT, to_status TEXT, actor TEXT, detail TEXT, demo_session_id TEXT,
  at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_ai_jobs_approval ON send_jobs (approval_id);
CREATE INDEX IF NOT EXISTS ix_ai_revisions_request ON draft_revisions (request_id);
CREATE INDEX IF NOT EXISTS ix_ai_approvals_revision ON approvals (revision_id);
CREATE INDEX IF NOT EXISTS ix_ai_events_entity ON events (entity, entity_id);
CREATE TRIGGER IF NOT EXISTS revisions_no_update BEFORE UPDATE ON draft_revisions
BEGIN SELECT RAISE(ABORT, 'draft_revisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS revisions_no_delete BEFORE DELETE ON draft_revisions
BEGIN SELECT RAISE(ABORT, 'draft_revisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_delete BEFORE DELETE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS jobs_no_delete BEFORE DELETE ON send_jobs
BEGIN SELECT RAISE(ABORT, 'send_jobs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS revisions_match_request BEFORE INSERT ON draft_revisions
WHEN NOT EXISTS (SELECT 1 FROM generation_requests r WHERE r.request_id = NEW.request_id
  AND r.status = 'succeeded' AND r.email_type = NEW.email_type AND r.context_hash = NEW.context_hash
  AND r.language = NEW.language AND r.booking_id IS NEW.booking_id)
BEGIN SELECT RAISE(ABORT, 'Revision does not match its request (type, context, booking)'); END;
CREATE TRIGGER IF NOT EXISTS approvals_match_revision BEFORE INSERT ON approvals
WHEN NEW.revoked_at IS NOT NULL OR NOT EXISTS (SELECT 1 FROM draft_revisions r WHERE r.revision_id = NEW.revision_id
  AND r.email_type = NEW.email_type AND r.context_hash = NEW.context_hash
  AND r.content_hash = NEW.content_hash AND r.booking_id IS NEW.booking_id)
BEGIN SELECT RAISE(ABORT, 'Approval does not match the revision'); END;
CREATE TRIGGER IF NOT EXISTS approvals_immutable BEFORE UPDATE ON approvals
WHEN OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL OR NEW.approval_id IS NOT OLD.approval_id
  OR NEW.workspace_id IS NOT OLD.workspace_id OR NEW.revision_id IS NOT OLD.revision_id
  OR NEW.email_type IS NOT OLD.email_type OR NEW.booking_id IS NOT OLD.booking_id
  OR NEW.context_hash IS NOT OLD.context_hash OR NEW.content_hash IS NOT OLD.content_hash
  OR NEW.recipient_id IS NOT OLD.recipient_id OR NEW.recipient_address IS NOT OLD.recipient_address
  OR NEW.sender_ref IS NOT OLD.sender_ref OR NEW.delivery_mode IS NOT OLD.delivery_mode
  OR NEW.reviewer_label IS NOT OLD.reviewer_label OR NEW.approved_at IS NOT OLD.approved_at
  OR NEW.expires_at IS NOT OLD.expires_at
BEGIN SELECT RAISE(ABORT, 'Approval is immutable (only one revocation)'); END;
CREATE TRIGGER IF NOT EXISTS jobs_start_queued BEFORE INSERT ON send_jobs
WHEN NEW.status <> 'queued' OR NEW.version <> 1 OR NOT EXISTS (SELECT 1 FROM approvals a
  WHERE a.approval_id = NEW.approval_id AND a.revision_id = NEW.revision_id
  AND a.delivery_mode = NEW.delivery_mode AND a.recipient_id = NEW.recipient_id
  AND a.recipient_address = NEW.recipient_address AND a.sender_ref = NEW.sender_ref)
BEGIN SELECT RAISE(ABORT, 'Send job must start queued and match its approval'); END;
CREATE TRIGGER IF NOT EXISTS jobs_identity BEFORE UPDATE ON send_jobs
WHEN NEW.job_id IS NOT OLD.job_id OR NEW.workspace_id IS NOT OLD.workspace_id
  OR NEW.approval_id IS NOT OLD.approval_id OR NEW.revision_id IS NOT OLD.revision_id
  OR NEW.send_key IS NOT OLD.send_key OR NEW.delivery_mode IS NOT OLD.delivery_mode
  OR NEW.recipient_id IS NOT OLD.recipient_id OR NEW.recipient_address IS NOT OLD.recipient_address
  OR NEW.sender_ref IS NOT OLD.sender_ref OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'Send job identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS jobs_transition BEFORE UPDATE ON send_jobs
WHEN NEW.status <> OLD.status AND (OLD.status, NEW.status) NOT IN (VALUES ('queued','claimed'),
  ('queued','failed'), ('claimed','dispatching'), ('claimed','failed'), ('dispatching','simulated'),
  ('dispatching','provider_accepted'), ('dispatching','failed'), ('dispatching','unknown'), ('failed','queued'))
BEGIN SELECT RAISE(ABORT, 'Invalid send job transition'); END;
CREATE TRIGGER IF NOT EXISTS jobs_version BEFORE UPDATE ON send_jobs
WHEN ((NEW.status <> OLD.status OR NEW.lease_token IS NOT OLD.lease_token) AND NEW.version <> OLD.version + 1)
  OR (NEW.status = OLD.status AND NEW.lease_token IS OLD.lease_token
      AND (NEW.version <> OLD.version OR NEW.provider_message_id IS NOT OLD.provider_message_id))
BEGIN SELECT RAISE(ABORT, 'Send job version must increase by one'); END;
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_pg(target) -> bool:
    return str(target).startswith(('postgresql://', 'postgres://'))


def _plain(v):
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat(timespec='microseconds')
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


class AiStore:
    """One connection to the AI store. Not shared between threads / browser sessions."""

    def __init__(self, target: str | Path, *, schema: str = SCHEMA, create_sqlite: bool = True):
        self.target = str(target)
        self.pg = is_pg(target)
        if self.pg:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg.types.json import Jsonb
            if not SCHEMA_RE.match(schema):
                raise ValueError(f'Invalid AI schema name: {schema!r}')
            self._Jsonb = Jsonb
            self._errors = psycopg.errors
            # prepare_threshold=None: no server-side prepared statements (transaction poolers).
            self.con = psycopg.connect(self.target.split('#')[0], autocommit=True, row_factory=dict_row,
                                       prepare_threshold=None)
            self.schema = schema
            self.prefix = f'{schema}.'
            row = self.con.execute('SELECT to_regclass(%s) AS t', (f'{schema}.meta',)).fetchone()
            if row['t'] is None:
                self.con.close()
                raise RuntimeError(f'AI schema {schema} is missing: run the bootstrap '
                                   '(python -m dashboard.ai_email.bootstrap --apply-schema)')
        else:
            path = Path(self.target)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.con = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
            self.con.row_factory = sqlite3.Row
            self.con.execute('PRAGMA foreign_keys = ON')
            self.schema, self.prefix = 'main', ''
            if create_sqlite:
                self.con.executescript(SQLITE_DDL)
        version = self.one("SELECT value FROM {s}meta WHERE key = 'ai_schema_version'")
        if not version or version['value'] != c.AI_SCHEMA_VERSION:
            raise RuntimeError(f'AI store schema {version and version["value"]} != {c.AI_SCHEMA_VERSION}')

    backend = property(lambda self: 'postgresql' if self.pg else 'sqlite')

    def close(self):
        self.con.close()

    # ------------------------------------------------------------ SQL helpers
    def _sql(self, sql: str) -> str:
        sql = sql.replace('{s}', self.prefix).replace('{lock}', ' FOR UPDATE' if self.pg else '')
        sql = sql.replace('{lock_a}', ' FOR UPDATE OF a' if self.pg else '')
        return sql.replace('?', '%s') if self.pg else sql

    def q(self, sql: str, params=()):
        try:
            return self.con.execute(self._sql(sql), tuple(params))
        except Exception as exc:
            if self.pg and isinstance(exc, (self._errors.IntegrityError, self._errors.RaiseException)):
                raise sqlite3.IntegrityError(exc.diag.message_primary or str(exc)) from None
            raise

    def _row(self, row, table: str = '') -> dict | None:
        if row is None:
            return None
        d = {k: _plain(v) for k, v in dict(row).items()}
        for k in JSON_COLS & d.keys():
            if f'{table}.{k}' not in NOT_JSON and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        if 'active' in d and d['active'] is not None:
            d['active'] = bool(d['active'])
        return d

    def one(self, sql, params=(), table=''):
        return self._row(self.q(sql, params).fetchone(), table)

    def all(self, sql, params=(), table=''):
        return [self._row(r, table) for r in self.q(sql, params).fetchall()]

    def j(self, obj):
        return None if obj is None else (self._Jsonb(obj) if self.pg else c.canonical_json(obj))

    def ts(self, dt: datetime):
        return dt if self.pg else _plain(dt)

    @contextmanager
    def tx(self):
        if self.pg:
            with self.con.transaction():
                yield
            return
        self.con.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            self.con.execute('ROLLBACK')
            raise
        self.con.execute('COMMIT')

    def _lock_request(self, request_id) -> None:
        """Serialise revisions / approvals of one draft (PostgreSQL: transaction advisory lock, so
        the runtime role needs no UPDATE right on requests; SQLite: BEGIN IMMEDIATE already does)."""
        if self.pg:
            self.q('SELECT pg_advisory_xact_lock(hashtext(?))', (f'g2-ai-request:{request_id}',))

    def _event(self, ws, entity, entity_id, kind, from_status=None, to_status=None, actor=None,
               detail=None, session=None):
        self.q("""INSERT INTO {s}events (workspace_id, entity, entity_id, kind, from_status, to_status,
                  actor, detail, demo_session_id, at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
               (ws, entity, str(entity_id), kind, from_status, to_status, actor, self.j(detail), session,
                self.ts(_now())))

    def _fn(self, name: str, sig: str, *args) -> dict:
        """Call a workflow function of the PostgreSQL schema (one transaction)."""
        row = self.q(f'SELECT {self.schema}.{name}({sig}) AS r', args).fetchone()
        return {k: _plain(v) for k, v in row['r'].items()}

    # ------------------------------------------------------------ workspace and recipients
    def ensure_workspace(self, workspace_id: str, *, source_run_id: str, policy_version: str,
                         business_as_of) -> dict:
        """The shared demo workspace, bound to one F/G1 run, policy and business date."""
        as_of = c._iso(business_as_of)
        self.q("""INSERT INTO {s}workspaces (workspace_id, source_run_id, policy_version, business_as_of,
                  created_at) VALUES (?,?,?,?,?) ON CONFLICT (workspace_id) DO NOTHING""",
               (workspace_id, source_run_id, policy_version, as_of, self.ts(_now())))
        ws = self.one('SELECT * FROM {s}workspaces WHERE workspace_id = ?', (workspace_id,))
        if (ws['source_run_id'], ws['policy_version'], str(ws['business_as_of'])[:10]) != \
                (source_run_id, policy_version, as_of):
            raise ValueError(f"AI workspace {workspace_id!r} is bound to run {ws['source_run_id']}, policy "
                             f"{ws['policy_version']}, business date {str(ws['business_as_of'])[:10]}. Set "
                             'G2_AI_WORKSPACE_ID to a new id for this run / date (old history is kept).')
        return ws

    def recipients(self, active_only: bool = True) -> list[dict]:
        where = 'WHERE active' if active_only and self.pg else 'WHERE active = 1' if active_only else ''
        return self.all(f'SELECT * FROM {{s}}test_recipients {where} ORDER BY recipient_id')

    # ------------------------------------------------------------ requests (workflow A)
    def create_request(self, context: dict, *, engine: str, requested_mode: str,
                       demo_session_id: str | None = None, requested_by: str | None = None) -> dict:
        if engine not in ('n8n', 'local') or requested_mode not in c.ANALYSIS_MODES:
            raise ValueError('Invalid engine or mode')
        if engine == 'n8n' and not self.pg:
            raise PermissionError('n8n Cloud cannot reach a local SQLite file: use the shared PostgreSQL store')
        rid = str(uuid.uuid4())
        h = c.context_hash(context)
        with self.tx():
            self.q("""INSERT INTO {s}generation_requests (request_id, workspace_id, demo_session_id, email_type,
                      booking_id, language, context_json, context_hash, engine, requested_mode, status,
                      requested_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,'queued',?,?)""",
                   (rid, context['workspace_id'], demo_session_id, context['email_type'],
                    context['booking']['booking_id'], context['language'], self.j(context), h, engine,
                    requested_mode, requested_by, self.ts(_now())))
            self._event(context['workspace_id'], 'request', rid, 'created', None, 'queued', requested_by,
                        {'engine': engine, 'requested_mode': requested_mode,
                         'email_type': context['email_type']}, demo_session_id)
        return self.get_request(rid)

    def get_request(self, request_id: str) -> dict | None:
        return self.one('SELECT * FROM {s}generation_requests WHERE request_id = ?', (str(request_id),))

    def claim_generation(self, request_id: str, workspace_id: str, engine: str = 'n8n') -> dict:
        if self.pg:
            return self._fn('claim_generation', '%s::uuid, %s, %s', str(request_id), workspace_id, engine)
        with self.tx():
            return self._py_claim_generation(str(request_id), workspace_id, engine)

    def complete_generation(self, request_id: str, lease: str, status: str, result: dict | None,
                            meta: dict | None) -> dict:
        if self.pg:
            return self._fn('complete_generation', '%s::uuid, %s::uuid, %s, %s::jsonb, %s::jsonb',
                            str(request_id), str(lease), status, self.j(result), self.j(meta or {}))
        with self.tx():
            return self._py_complete_generation(str(request_id), str(lease), status, result, meta or {})

    def run_local_generation(self, request_id: str, workspace_id: str, produce) -> dict:
        """Local engine (offline fixture / manual label): same claim -> validate -> store steps as
        workflow A, without n8n. produce(context) -> (output or None, analysis_mode, error)."""
        claim = self.claim_generation(request_id, workspace_id, 'local')
        if claim['status'] != 'claimed':
            return claim
        context = claim['context']
        output, mode, error = produce(context)
        errors = validate.validate_output(context, output) if output is not None else [error or 'no output']
        meta = {'analysis_mode': mode, 'prompt_version': None, 'model': None,
                'validation_errors': errors or None}
        if errors:
            meta['error'] = 'Output failed validation: ' + '; '.join(errors)[:900]
        return self.complete_generation(request_id, claim['lease_token'], 'failed' if errors else 'succeeded',
                                        None if errors else output, meta)

    # ------------------------------------------------------------ revisions
    def revisions(self, request_id: str) -> list[dict]:
        return self.all('SELECT * FROM {s}draft_revisions WHERE request_id = ? ORDER BY revision_no',
                        (str(request_id),))

    def latest_revision(self, request_id: str) -> dict | None:
        return self.one("""SELECT * FROM {s}draft_revisions WHERE request_id = ?
                           ORDER BY revision_no DESC LIMIT 1""", (str(request_id),))

    def get_revision(self, revision_id: int) -> dict | None:
        return self.one('SELECT * FROM {s}draft_revisions WHERE revision_id = ?', (int(revision_id),))

    def _insert_revision(self, req: dict, no: int, subject: str, body: str, source: str, actor, session):
        context = req['context_json']
        analysis = req['result_json']
        checked = validate.check_revision(context, subject, body)
        missing = list(dict.fromkeys((analysis.get('missing_information') or [])
                                     + validate.server_missing_information(context, analysis)))
        self.q("""INSERT INTO {s}draft_revisions (workspace_id, request_id, revision_no, email_type, booking_id,
                  language, context_hash, editable_subject, editable_body, rendered_subject, rendered_body,
                  content_hash, analysis_json, analysis_mode, missing_information, send_blockers, source,
                  created_by, demo_session_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (req['workspace_id'], req['request_id'], no, req['email_type'], req['booking_id'],
                req['language'], req['context_hash'], subject, body, checked['rendered_subject'],
                checked['rendered_body'], checked['content_hash'],
                self.j({k: analysis.get(k) for k in ('intent', 'certainty', 'evidence_quotes',
                                                     'requested_changes', 'requires_clarification',
                                                     'source_refs')}),
                req['analysis_mode'], self.j(missing), self.j(checked['send_blockers']), source, actor,
                session, self.ts(_now())))
        rev = self.one('SELECT * FROM {s}draft_revisions WHERE request_id = ? AND revision_no = ?',
                       (req['request_id'], no))
        self._event(req['workspace_id'], 'revision', rev['revision_id'], 'created', None, source, actor,
                    {'revision_no': no, 'blockers': len(checked['send_blockers'])}, session)
        return rev

    def ensure_first_revision(self, request_id: str, *, actor=None, session=None) -> tuple[dict | None, list[str]]:
        """Revision 1 of a succeeded request (created once, also after a lost HTTP response).
        The stored output is validated again here: n8n's verdict is not trusted blindly."""
        req = self.get_request(request_id)
        if req is None or req['status'] != 'succeeded':
            return None, []
        existing = self.latest_revision(request_id)
        if existing:
            return existing, []
        errors = validate.validate_output(req['context_json'], req['result_json'])
        if errors:
            return None, errors
        out = req['result_json']
        try:
            with self.tx():
                return self._insert_revision(req, 1, out['draft_subject'], out['draft_body'], 'generated',
                                             actor, session), []
        except sqlite3.IntegrityError:
            existing = self.latest_revision(request_id)
            if existing:  # another session created it first
                return existing, []
            raise

    def create_revision(self, request_id: str, *, base_revision_id: int, subject: str, body: str,
                        actor: str | None = None, session: str | None = None) -> dict:
        """Staff edit -> new immutable revision; approvals of older revisions are revoked in the
        same transaction. Refused while one of this draft's emails is being dispatched."""
        if not (subject or '').strip() or not (body or '').strip():
            raise ValueError('Subject and text are required')
        if len(subject) > c.MAX_SUBJECT or len(body) > c.MAX_BODY:
            raise ValueError('Subject or text too long')
        with self.tx():
            self._lock_request(request_id)
            req = self.get_request(request_id)
            if req is None or req['status'] != 'succeeded':
                raise ValueError('This draft has no generated result')
            latest = self.one("""SELECT * FROM {s}draft_revisions WHERE request_id = ?
                                 ORDER BY revision_no DESC LIMIT 1""", (str(request_id),))
            if latest is None or int(latest['revision_id']) != int(base_revision_id):
                raise ValueError('Someone saved a newer revision of this draft: reload it before editing')
            if latest['editable_subject'] == subject and latest['editable_body'] == body:
                raise ValueError('Nothing changed')
            approvals = self.all("""SELECT a.approval_id FROM {s}approvals a JOIN {s}draft_revisions r
                                    USING (revision_id) WHERE r.request_id = ? AND a.revoked_at IS NULL
                                    ORDER BY a.approval_id{lock_a}""", (str(request_id),))
            ids = [a['approval_id'] for a in approvals]
            if ids and self.one(f"""SELECT 1 AS x FROM {{s}}send_jobs WHERE status = 'dispatching'
                                    AND approval_id IN ({','.join('?' * len(ids))})""", ids):
                raise ValueError('An approved revision of this draft is being sent: wait for the result '
                                 'before creating a new revision')
            for aid in ids:
                self._revoke(aid, actor or 'staff edit', f"new revision {latest['revision_no'] + 1}", session)
            return self._insert_revision(req, latest['revision_no'] + 1, subject, body, 'staff_edit', actor,
                                         session)

    # ------------------------------------------------------------ approvals
    def approve(self, revision_id: int, *, reviewer: str, delivery_mode: str, recipient_id: str,
                sender_ref: str, session: str | None = None, ttl: timedelta = APPROVAL_TTL) -> dict:
        """Approve one revision for one recipient, sender and delivery mode. Idempotent: the same
        active approval is returned on a double click."""
        reviewer = (reviewer or '').strip()
        if not reviewer:
            raise ValueError('Enter a reviewer label')
        if delivery_mode not in c.DELIVERY_MODES:
            raise ValueError('Unknown delivery mode')
        if delivery_mode == 'team_test_email' and not self.pg:
            raise PermissionError('Real test emails need the shared PostgreSQL store (approval and '
                                  'de-duplication must survive restarts); SQLite is for mock only')
        sender_ref = (sender_ref or '').strip()
        if delivery_mode == 'mock':
            recipient_id, address, sender_ref = c.MOCK_RECIPIENT_ID, c.MOCK_RECIPIENT_ADDRESS, sender_ref or 'mock'
        with self.tx():
            rev = self.get_revision(revision_id)
            if rev is None:
                raise ValueError('Revision not found')
            self._lock_request(rev['request_id'])
            latest = self.latest_revision(rev['request_id'])
            if int(latest['revision_id']) != int(revision_id):
                raise ValueError(f"Revision {rev['revision_no']} is not the latest: approve revision "
                                 f"{latest['revision_no']}")
            if rev['send_blockers']:
                raise ValueError('This revision has send blockers: fix them in a new revision first')
            if delivery_mode == 'team_test_email':
                rc = self.one('SELECT * FROM {s}test_recipients WHERE recipient_id = ?', (recipient_id,))
                if rc is None or not rc['active']:
                    raise PermissionError('Recipient is not in the server-side test allowlist')
                if not sender_ref:
                    raise ValueError('Sender reference missing (G2_AI_SENDER_REF)')
                address = rc['address']
            same = self.one("""SELECT * FROM {s}approvals WHERE revision_id = ? AND recipient_id = ?
                               AND delivery_mode = ? AND sender_ref = ? AND revoked_at IS NULL
                               AND expires_at > ? ORDER BY approval_id DESC LIMIT 1""",
                            (int(revision_id), recipient_id, delivery_mode, sender_ref, self.ts(_now())))
            if same:
                return same
            now = _now()
            self.q("""INSERT INTO {s}approvals (workspace_id, revision_id, email_type, booking_id, context_hash,
                      content_hash, recipient_id, recipient_address, sender_ref, delivery_mode, reviewer_label,
                      approved_at, expires_at, demo_session_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (rev['workspace_id'], int(revision_id), rev['email_type'], rev['booking_id'],
                    rev['context_hash'], rev['content_hash'], recipient_id, address, sender_ref, delivery_mode,
                    reviewer, self.ts(now), self.ts(now + ttl), session))
            a = self.one("""SELECT * FROM {s}approvals WHERE revision_id = ? ORDER BY approval_id DESC LIMIT 1""",
                         (int(revision_id),))
            self._event(rev['workspace_id'], 'approval', a['approval_id'], 'approved', None, delivery_mode,
                        reviewer, {'revision_id': int(revision_id), 'recipient_id': recipient_id}, session)
            return a

    def approvals(self, revision_id: int) -> list[dict]:
        return self.all('SELECT * FROM {s}approvals WHERE revision_id = ? ORDER BY approval_id',
                        (int(revision_id),))

    def get_approval(self, approval_id: int) -> dict | None:
        return self.one('SELECT * FROM {s}approvals WHERE approval_id = ?', (int(approval_id),))

    def _revoke(self, approval_id: int, actor: str, reason: str, session=None) -> None:
        a = self.one('SELECT * FROM {s}approvals WHERE approval_id = ?{lock}', (int(approval_id),))
        if a is None or a['revoked_at'] is not None:
            return
        if self.one("SELECT 1 AS x FROM {s}send_jobs WHERE approval_id = ? AND status = 'dispatching'",
                    (int(approval_id),)):
            raise ValueError('This approval is being sent right now: it can no longer be withdrawn')
        self.q("""UPDATE {s}approvals SET revoked_at = ?, revoked_by = ?, revoke_reason = ?
                  WHERE approval_id = ?""", (self.ts(_now()), actor, reason, int(approval_id)))
        for j in self.all("""SELECT * FROM {s}send_jobs WHERE approval_id = ? AND status IN ('queued','claimed')
                             {lock}""", (int(approval_id),), 'send_jobs'):
            self.q("""UPDATE {s}send_jobs SET status = 'failed', lease_token = NULL, lease_expires_at = NULL,
                      version = ?, detail = ?, updated_at = ?, finished_at = ? WHERE job_id = ?""",
                   (j['version'] + 1, f'Not sent: approval revoked ({reason})', self.ts(_now()),
                    self.ts(_now()), j['job_id']))
            self._event(j['workspace_id'], 'job', j['job_id'], 'approval_revoked', j['status'], 'failed',
                        actor, None, session)
        self._event(a['workspace_id'], 'approval', approval_id, 'revoked', a['delivery_mode'], 'revoked',
                    actor, {'reason': reason}, session)

    def revoke(self, approval_id: int, *, actor: str, reason: str, session: str | None = None) -> None:
        if not (reason or '').strip():
            raise ValueError('Give a reason')
        with self.tx():
            self._revoke(approval_id, (actor or 'staff').strip() or 'staff', reason.strip(), session)

    def approval_problem(self, approval_id: int, job_id: str | None = None) -> str | None:
        if self.pg:
            row = self.q(f'SELECT {self.schema}.approval_problem(%s::bigint, %s::uuid) AS p',
                         (int(approval_id), job_id)).fetchone()
            return row['p']
        return self._py_approval_problem(int(approval_id), job_id)

    # ------------------------------------------------------------ send jobs (workflow B)
    @staticmethod
    def send_key(a: dict) -> str:
        return c.sha256('|'.join(map(str, (a['workspace_id'], a['revision_id'], a['approval_id'],
                                           a['recipient_id'], a['delivery_mode'], a['sender_ref']))))

    def create_send_job(self, approval_id: int, *, requested_by: str | None = None,
                        session: str | None = None) -> dict:
        """The send job of this approval: created once, returned again on any later click / tab."""
        with self.tx():
            a = self.one('SELECT * FROM {s}approvals WHERE approval_id = ?{lock}', (int(approval_id),))
            if a is None:
                raise ValueError('Approval not found')
            key = self.send_key(a)
            job = self.one('SELECT * FROM {s}send_jobs WHERE send_key = ?', (key,), 'send_jobs')
            if job:
                return job
            problem = self.approval_problem(approval_id)
            if problem:
                raise ValueError(f'Cannot send: {problem}')
            jid = str(uuid.uuid4())
            now = self.ts(_now())
            self.q("""INSERT INTO {s}send_jobs (job_id, workspace_id, approval_id, revision_id, send_key,
                      delivery_mode, recipient_id, recipient_address, sender_ref, status, version, requested_by,
                      demo_session_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,'queued',1,?,?,?,?)""",
                   (jid, a['workspace_id'], a['approval_id'], a['revision_id'], key, a['delivery_mode'],
                    a['recipient_id'], a['recipient_address'], a['sender_ref'], requested_by, session, now, now))
            self._event(a['workspace_id'], 'job', jid, 'created', None, 'queued', requested_by,
                        {'approval_id': a['approval_id']}, session)
            return self.get_job(jid)

    def get_job(self, job_id: str) -> dict | None:
        return self.one('SELECT * FROM {s}send_jobs WHERE job_id = ?', (str(job_id),), 'send_jobs')

    def jobs_for_approval(self, approval_id: int) -> list[dict]:
        return self.all('SELECT * FROM {s}send_jobs WHERE approval_id = ? ORDER BY created_at',
                        (int(approval_id),), 'send_jobs')

    def retry_failed_job(self, job_id: str, *, actor: str, session=None) -> dict:
        """failed (known, nothing sent) -> queued, same job and key, after re-checking the approval.
        Never for 'unknown': that one may have been sent."""
        with self.tx():
            j = self.get_job(job_id)
            if j is None:
                raise ValueError('Send job not found')
            self.one('SELECT 1 AS x FROM {s}approvals WHERE approval_id = ?{lock}', (j['approval_id'],))
            j = self.one('SELECT * FROM {s}send_jobs WHERE job_id = ?{lock}', (str(job_id),), 'send_jobs')
            if j['status'] != 'failed':
                raise ValueError(f"Only a failed send can be retried (this one is {j['status']})")
            problem = self.approval_problem(j['approval_id'], job_id)
            if problem:
                raise ValueError(f'Cannot retry: {problem}')
            self.q("""UPDATE {s}send_jobs SET status = 'queued', version = ?, updated_at = ?, finished_at = NULL
                      WHERE job_id = ?""", (j['version'] + 1, self.ts(_now()), str(job_id)))
            self._event(j['workspace_id'], 'job', job_id, 'retry', 'failed', 'queued', actor, None, session)
        return self.get_job(job_id)

    def claim_send_job(self, job_id: str, workspace_id: str, executor: str, sender_ref: str | None) -> dict:
        if self.pg:
            return self._fn('claim_send_job', '%s::uuid, %s, %s, %s', str(job_id), workspace_id, executor,
                            sender_ref)
        with self.tx():
            return self._py_claim_send_job(str(job_id), workspace_id, executor, sender_ref)

    def begin_dispatch(self, job_id: str, lease: str) -> dict:
        if self.pg:
            return self._fn('begin_dispatch', '%s::uuid, %s::uuid', str(job_id), str(lease))
        with self.tx():
            return self._py_begin_dispatch(str(job_id), str(lease))

    def finish_dispatch(self, job_id: str, lease: str, outcome: str, provider_message_id: str | None,
                        detail: str | None) -> dict:
        if self.pg:
            return self._fn('finish_dispatch', '%s::uuid, %s::uuid, %s, %s, %s', str(job_id), str(lease),
                            outcome, provider_message_id, detail)
        with self.tx():
            return self._py_finish_dispatch(str(job_id), str(lease), outcome, provider_message_id, detail)

    def reconcile_stale(self, workspace_id: str) -> int:
        if self.pg:
            return self.q(f'SELECT {self.schema}.reconcile_stale(%s) AS n', (workspace_id,)).fetchone()['n']
        with self.tx():
            return self._py_reconcile_stale(workspace_id)

    def run_local_mock_send(self, job_id: str, workspace_id: str) -> dict:
        """Mock delivery without n8n (offline demo): the same claim -> dispatching -> result steps
        as workflow B; nothing leaves the computer."""
        j = self.get_job(job_id)
        if j is None or j['delivery_mode'] != 'mock':
            raise PermissionError('The local executor only simulates mock sends; real test emails go '
                                  'through n8n workflow B')
        claim = self.claim_send_job(job_id, workspace_id, 'local_mock', 'mock')
        if claim['status'] != 'claimed':
            return claim
        begun = self.begin_dispatch(job_id, claim['lease_token'])
        if not begun.get('ok'):
            return begun
        return self.finish_dispatch(job_id, claim['lease_token'], 'simulated',
                                    f'local-mock-{str(job_id)[:8]}', 'Simulated locally: n8n not called, no email sent')

    def mark_receipt_verified(self, job_id: str, *, tester: str, session=None) -> dict:
        tester = (tester or '').strip()
        if not tester:
            raise ValueError('Enter who checked the test inbox')
        with self.tx():
            j = self.one('SELECT * FROM {s}send_jobs WHERE job_id = ?{lock}', (str(job_id),), 'send_jobs')
            if j is None or j['status'] not in ('provider_accepted', 'unknown'):
                raise ValueError('Only a real test email (accepted or unknown) can be marked as received')
            self.q('UPDATE {s}send_jobs SET receipt_verified_by = ?, receipt_verified_at = ? WHERE job_id = ?',
                   (tester, self.ts(_now()), str(job_id)))
            self._event(j['workspace_id'], 'job', job_id, 'receipt_verified_by_tester', j['status'], j['status'],
                        tester, None, session)
        return self.get_job(job_id)

    # ------------------------------------------------------------ reading the shared history
    def list_requests(self, workspace_id: str, limit: int = 50) -> list[dict]:
        return self.all("""SELECT r.request_id, r.email_type, r.booking_id, r.language, r.status, r.engine,
                             r.requested_mode, r.analysis_mode, r.created_at, r.completed_at, r.error,
                             r.context_hash, r.demo_session_id,
                             (SELECT MAX(revision_no) FROM {s}draft_revisions d WHERE d.request_id = r.request_id)
                               AS revisions
                           FROM {s}generation_requests r WHERE r.workspace_id = ?
                           ORDER BY r.created_at DESC LIMIT ?""", (workspace_id, int(limit)))

    def send_log(self, workspace_id: str, limit: int = 100) -> list[dict]:
        return self.all("""SELECT j.job_id, j.status, j.delivery_mode, j.recipient_address, j.executor,
                             j.provider_message_id, j.detail, j.created_at, j.finished_at, j.receipt_verified_by,
                             a.reviewer_label, a.approved_at, a.revoked_at, d.revision_no, d.email_type,
                             d.booking_id, q.request_id, q.analysis_mode
                           FROM {s}send_jobs j JOIN {s}approvals a ON a.approval_id = j.approval_id
                           JOIN {s}draft_revisions d ON d.revision_id = j.revision_id
                           JOIN {s}generation_requests q ON q.request_id = d.request_id
                           WHERE j.workspace_id = ? ORDER BY j.created_at DESC LIMIT ?""",
                        (workspace_id, int(limit)), 'send_jobs')

    def events(self, entity: str, entity_id) -> list[dict]:
        return self.all('SELECT * FROM {s}events WHERE entity = ? AND entity_id = ? ORDER BY event_id',
                        (entity, str(entity_id)))

    # ------------------------------------------------------------ SQLite mirror of the SQL functions
    def _py_reconcile_stale(self, ws: str) -> int:
        now, n = _now(), 0
        for r in self.all("""SELECT request_id FROM generation_requests WHERE workspace_id = ?
                             AND status = 'processing' AND lease_expires_at < ?""", (ws, self.ts(now))):
            self._expire_generation(r['request_id'], ws, 'reconcile')
            n += 1
        for j in self.all("""SELECT job_id, version FROM send_jobs WHERE workspace_id = ? AND status = 'dispatching'
                             AND dispatch_started_at < ?""", (ws, self.ts(now - DISPATCH_STALE))):
            self.q("""UPDATE send_jobs SET status = 'unknown', lease_token = NULL, lease_expires_at = NULL,
                      version = ?, updated_at = ?, detail = ? WHERE job_id = ?""",
                   (j['version'] + 1, self.ts(now),
                    'No provider result stored within 5 minutes of dispatch: the email may have been sent. '
                    'Reconcile with the test inbox; never resend automatically.', j['job_id']))
            self._event(ws, 'job', j['job_id'], 'dispatch_unknown', 'dispatching', 'unknown', 'reconcile')
            n += 1
        return n

    def _expire_generation(self, request_id, ws, actor):
        self.q("""UPDATE generation_requests SET status = 'failed', lease_token = NULL, lease_expires_at = NULL,
                  error = 'No result stored before the lease expired; not retried automatically: regenerate',
                  completed_at = ? WHERE request_id = ?""", (self.ts(_now()), request_id))
        self._event(ws, 'request', request_id, 'lease_expired', 'processing', 'failed', actor)

    def _py_claim_generation(self, request_id: str, ws: str, engine: str) -> dict:
        r = self.get_request(request_id)
        if r is None or r['workspace_id'] != ws:
            return {'status': 'not_found', 'request_id': request_id}
        if r['engine'] != engine:
            return {'status': 'refused', 'request_id': request_id, 'reason': f"request belongs to engine {r['engine']}"}
        if r['status'] == 'processing' and r['lease_expires_at'] < self.ts(_now()):
            self._expire_generation(request_id, ws, engine)
            r = self.get_request(request_id)
        if r['status'] == 'processing':
            return {'status': 'in_progress', 'request_id': request_id}
        if r['status'] in ('succeeded', 'failed'):
            return {'status': r['status'], 'request_id': request_id, 'result': r['result_json'],
                    'errors': r['validation_errors'], 'error': r['error'], 'analysis_mode': r['analysis_mode']}
        lease = str(uuid.uuid4())
        now = _now()
        self.q("""UPDATE generation_requests SET status = 'processing', lease_token = ?, lease_expires_at = ?,
                  attempts = attempts + 1, claimed_at = ? WHERE request_id = ?""",
               (lease, self.ts(now + GENERATION_LEASE), self.ts(now), request_id))
        self._event(ws, 'request', request_id, 'claimed', 'queued', 'processing', engine)
        return {'status': 'claimed', 'request_id': request_id, 'lease_token': lease,
                'requested_mode': r['requested_mode'], 'context': r['context_json'],
                'context_hash': r['context_hash']}

    def _py_complete_generation(self, request_id, lease, status, result, meta) -> dict:
        if status not in ('succeeded', 'failed'):
            return {'stored': False, 'reason': 'status must be succeeded or failed'}
        r = self.get_request(request_id)
        if r is None or r['status'] != 'processing' or r['lease_token'] != lease:
            return {'stored': False, 'reason': 'lease lost: the request was completed or expired'}
        if status == 'succeeded' and (not isinstance(result, dict) or result.get('email_type') != r['email_type']
                                      or meta.get('analysis_mode') not in c.ANALYSIS_MODES):
            status, meta = 'failed', {**meta, 'error': 'result does not match the request'}
        self.q("""UPDATE generation_requests SET status = ?, lease_token = NULL, lease_expires_at = NULL,
                  result_json = ?, validation_errors = ?, analysis_mode = ?, model = ?, prompt_version = ?,
                  token_usage = ?, error = ?, completed_at = ? WHERE request_id = ?""",
               (status, self.j(result) if status == 'succeeded' else None, self.j(meta.get('validation_errors')),
                meta.get('analysis_mode') if status == 'succeeded' else None, meta.get('model'),
                meta.get('prompt_version'), self.j(meta.get('token_usage')),
                (meta.get('error') or None) and str(meta['error'])[:1000], self.ts(_now()), request_id))
        r = self.get_request(request_id)
        self._event(r['workspace_id'], 'request', request_id, 'completed', 'processing', status, r['engine'],
                    {'analysis_mode': r['analysis_mode']})
        return {'stored': True, 'request_id': request_id, 'status': r['status'], 'result': r['result_json'],
                'errors': r['validation_errors'], 'error': r['error'], 'analysis_mode': r['analysis_mode']}

    def _py_approval_problem(self, approval_id: int, job_id: str | None) -> str | None:
        a = self.get_approval(approval_id)
        if a is None:
            return 'approval not found'
        if a['revoked_at'] is not None:
            return f"approval revoked: {a['revoke_reason']}"
        if a['expires_at'] <= self.ts(_now()):
            return 'approval expired: approve again'
        r = self.get_revision(a['revision_id'])
        if r['workspace_id'] != a['workspace_id']:
            return 'workspace mismatch'
        if self.one('SELECT 1 AS x FROM draft_revisions WHERE request_id = ? AND revision_no > ?',
                    (r['request_id'], r['revision_no'])):
            return 'a newer revision exists: approve the current revision'
        if (r['email_type'], r['context_hash'], r['content_hash'], r['booking_id']) != \
                (a['email_type'], a['context_hash'], a['content_hash'], a['booking_id']):
            return 'approval does not match the revision (type, context or content)'
        if render.content_hash(r['rendered_subject'], r['rendered_body']) != a['content_hash']:
            return 'content hash mismatch'
        if r['send_blockers']:
            return 'revision has send blockers'
        if a['delivery_mode'] == 'team_test_email':
            rc = self.one('SELECT * FROM test_recipients WHERE recipient_id = ?', (a['recipient_id'],))
            if rc is None or not rc['active'] or rc['address'] != a['recipient_address']:
                return 'recipient not in the active test allowlist'
        if job_id is not None:
            j = self.get_job(job_id)
            keys = ('approval_id', 'revision_id', 'delivery_mode', 'recipient_id', 'recipient_address',
                    'sender_ref', 'workspace_id')
            if any(j[k] != a[k] for k in keys):
                return 'send job does not match its approval'
        return None

    def _job_failed(self, j, problem, kind, actor):
        now = self.ts(_now())
        self.q("""UPDATE send_jobs SET status = 'failed', lease_token = NULL, lease_expires_at = NULL, version = ?,
                  detail = ?, updated_at = ?, finished_at = ?, executor = ? WHERE job_id = ?""",
               (j['version'] + 1, f'Not sent: {problem}', now, now, actor, j['job_id']))
        self._event(j['workspace_id'], 'job', j['job_id'], kind, j['status'], 'failed', actor, {'reason': problem})

    def _py_claim_send_job(self, job_id, ws, executor, sender_ref) -> dict:
        self._py_reconcile_stale(ws)
        j = self.get_job(job_id)
        if j is None or j['workspace_id'] != ws:
            return {'status': 'refused', 'job_id': job_id, 'reason': 'send job not found'}
        if j['status'] in TERMINAL:
            return {'status': 'already', 'job_id': job_id, 'job_status': j['status'],
                    'provider_message_id': j['provider_message_id'], 'detail': j['detail']}
        now = _now()
        if j['status'] == 'dispatching' or (j['status'] == 'claimed' and j['lease_expires_at'] > self.ts(now)):
            return {'status': 'in_progress', 'job_id': job_id, 'job_status': j['status']}
        problem = self._py_approval_problem(j['approval_id'], job_id)
        if problem is None and j['delivery_mode'] == 'team_test_email' and j['sender_ref'] != sender_ref:
            problem = f"sender changed (workflow sender {sender_ref or 'none'}): approve again"
        if problem:
            self._job_failed(j, problem, 'refused_at_claim', executor)
            return {'status': 'refused', 'job_id': job_id, 'reason': problem}
        lease = str(uuid.uuid4())
        self.q("""UPDATE send_jobs SET status = 'claimed', lease_token = ?, lease_expires_at = ?, version = ?,
                  executor = ?, updated_at = ? WHERE job_id = ?""",
               (lease, self.ts(now + SEND_LEASE), j['version'] + 1, executor, self.ts(now), job_id))
        self._event(ws, 'job', job_id, 'claimed', j['status'], 'claimed', executor)
        return {'status': 'claimed', 'job_id': job_id, 'lease_token': lease, 'version': j['version'] + 1,
                'delivery_mode': j['delivery_mode']}

    def _py_begin_dispatch(self, job_id, lease) -> dict:
        j = self.get_job(job_id)
        if j is None:
            return {'ok': False, 'reason': 'send job not found'}
        now = _now()
        if j['status'] != 'claimed' or j['lease_token'] != lease or j['lease_expires_at'] <= self.ts(now):
            return {'ok': False, 'reason': 'lease lost: another execution owns this job or it expired',
                    'job_status': j['status']}
        problem = self._py_approval_problem(j['approval_id'], job_id)
        if problem:
            self._job_failed(j, problem, 'refused_before_dispatch', j['executor'])
            return {'ok': False, 'reason': problem, 'job_status': 'failed'}
        r = self.get_revision(j['revision_id'])
        self.q("""UPDATE send_jobs SET status = 'dispatching', version = ?, dispatch_started_at = ?, updated_at = ?
                  WHERE job_id = ?""", (j['version'] + 1, self.ts(now), self.ts(now), job_id))
        self._event(j['workspace_id'], 'job', job_id, 'dispatching', 'claimed', 'dispatching', j['executor'])
        return {'ok': True, 'job_id': job_id, 'delivery_mode': j['delivery_mode'], 'to': j['recipient_address'],
                'subject': r['rendered_subject'], 'body': r['rendered_body'], 'content_hash': r['content_hash'],
                'version': j['version'] + 1}

    def _py_finish_dispatch(self, job_id, lease, outcome, provider_message_id, detail) -> dict:
        j = self.get_job(job_id)
        if j is None or j['status'] != 'dispatching' or j['lease_token'] != lease:
            return {'stored': False, 'reason': 'not the dispatching lease holder', 'job_status': j and j['status']}
        if outcome not in TERMINAL or (outcome == 'simulated' and j['delivery_mode'] != 'mock') \
                or (outcome == 'provider_accepted' and j['delivery_mode'] != 'team_test_email'):
            detail = f"Invalid outcome {outcome} recorded as unknown. {detail or ''}"
            outcome = 'unknown'
        now = self.ts(_now())
        self.q("""UPDATE send_jobs SET status = ?, lease_token = NULL, lease_expires_at = NULL, version = ?,
                  provider_message_id = ?, detail = ?, updated_at = ?, finished_at = ? WHERE job_id = ?""",
               (outcome, j['version'] + 1, (provider_message_id or None) and str(provider_message_id)[:200],
                (detail or None) and str(detail)[:1000], now, now, job_id))
        j = self.get_job(job_id)
        self._event(j['workspace_id'], 'job', job_id, 'dispatched', 'dispatching', outcome, j['executor'],
                    {'provider_message_id': j['provider_message_id']})
        return {'stored': True, 'job_id': job_id, 'job_status': j['status'],
                'provider_message_id': j['provider_message_id'], 'detail': j['detail']}


def apply_pg_schema(owner_dsn: str, schema: str = SCHEMA) -> None:
    """Bootstrap (schema owner): create / update the AI schema. Idempotent."""
    import psycopg
    if not SCHEMA_RE.match(schema):
        raise ValueError(f'Invalid AI schema name: {schema!r}')
    sql = SQL_FILE.read_text(encoding='utf-8').replace('g2_ai_v1', schema)
    with psycopg.connect(owner_dsn.split('#')[0], autocommit=True) as con:
        con.execute('SELECT pg_advisory_lock(hashtext(%s))', (f'g2-ai-bootstrap:{schema}',))
        try:
            with con.transaction():
                con.execute(sql)
        finally:
            con.execute('SELECT pg_advisory_unlock(hashtext(%s))', (f'g2-ai-bootstrap:{schema}',))


def upsert_test_recipients(store: AiStore, recipients: list[tuple[str, str]], *, deactivate_others=True) -> None:
    """Bootstrap / owner only: the server-side allowlist of the team's test inboxes."""
    with store.tx():
        for rid, address in recipients:
            address = address.strip().lower()
            store.q("""INSERT INTO {s}test_recipients (recipient_id, address, active, created_at)
                       VALUES (?,?,?,?) ON CONFLICT (recipient_id) DO UPDATE SET address = excluded.address,
                       active = excluded.active""", (rid, address, True if store.pg else 1, store.ts(_now())))
        if deactivate_others:
            ids = [r for r, _ in recipients] or ['']
            store.q(f"UPDATE {{s}}test_recipients SET active = {'false' if store.pg else '0'} "
                    f"WHERE recipient_id NOT IN ({','.join('?' * len(ids))})", ids)
