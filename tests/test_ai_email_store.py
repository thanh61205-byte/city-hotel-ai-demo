"""AI email store and send protocol (docs/SPEC_AI_N8N_VI.md §6.1) on SQLite, and on PostgreSQL when
G2_TEST_PG_DSN is set (fresh schema per test; the SQL functions n8n calls are exercised there).

    set G2_TEST_PG_DSN=postgresql://user@127.0.0.1:5432/postgres
    python -m unittest discover -s tests -p "test_ai_email_*.py"
"""
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from datetime import timedelta
from pathlib import Path

from dashboard.ai_email import contract as c, fixtures, store as st

PG_DSN = os.environ.get('G2_TEST_PG_DSN', '').strip()
PG_ONLY = unittest.skipUnless(PG_DSN, 'PostgreSQL only (set G2_TEST_PG_DSN)')
SQLITE_ONLY = unittest.skipIf(bool(PG_DSN), 'SQLite only')
WS = 'demo'
BOOKING = {'booking_id': 27767, 'arrival_date': '2017-05-08', 'room_type_code': 'A', 'known_from': '2017-01-01'}
SCHEDULE = [{'booking_id': 27767, 'decision_at': '2017-01-01', 'scheduled_at': '2017-05-01', 'rule': 'I1',
             'action_type': 'reminder', 'schedule_reason': 'standard reminder', 'message_no': 2}]
REFUSED = (sqlite3.Error, ValueError, PermissionError)


def context(email_type='reminder', message=None):
    gm = {'text': message, 'message_business_at': '2017-05-01', 'source': 'sample_input'} if message else None
    return c.build_context(workspace_id=WS, email_type=email_type, language='en', business_as_of='2017-05-01',
                           source_run_id='run', policy_version='pol', booking=BOOKING, schedule_rows=SCHEDULE,
                           guest_message=gm)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        if PG_DSN:
            self.schema = 't_ai_' + uuid.uuid4().hex[:12]
            st.apply_pg_schema(PG_DSN, self.schema)
            self.target = PG_DSN
        else:
            self.schema = st.SCHEMA
            self.target = str(Path(self.tmp.name) / 'ai.sqlite')
        self.s = self.open()
        self.s.ensure_workspace(WS, source_run_id='run', policy_version='pol', business_as_of='2017-05-01')
        self.calls = 0

    def open(self):
        return st.AiStore(self.target, schema=self.schema)

    def tearDown(self):
        self.s.close()
        if PG_DSN:
            import psycopg
            with psycopg.connect(PG_DSN, autocommit=True) as con:
                con.execute(f'DROP SCHEMA IF EXISTS {self.schema} CASCADE')
        self.tmp.cleanup()

    def producer(self, cx):
        self.calls += 1
        return fixtures.fixture_output(cx), 'fixture', None

    def draft(self, email_type='reminder', message=None):
        req = self.s.create_request(context(email_type, message), engine='local', requested_mode='fixture')
        self.s.run_local_generation(req['request_id'], WS, self.producer)
        rev, errors = self.s.ensure_first_revision(req['request_id'])
        self.assertEqual(errors, [])
        return req['request_id'], rev

    def approved_job(self, **kw):
        rid, rev = self.draft()
        a = self.s.approve(rev['revision_id'], reviewer='Lan', delivery_mode='mock', recipient_id='-',
                           sender_ref='', **kw)
        return rid, rev, a, self.s.create_send_job(a['approval_id'])


class TestGeneration(StoreCase):
    def test_claim_is_atomic_and_result_is_reused(self):
        req = self.s.create_request(context(), engine='local', requested_mode='fixture')
        first = self.s.claim_generation(req['request_id'], WS, 'local')
        self.assertEqual(first['status'], 'claimed')
        self.assertEqual(self.s.claim_generation(req['request_id'], WS, 'local')['status'], 'in_progress')
        out = fixtures.fixture_output(first['context'])
        done = self.s.complete_generation(req['request_id'], first['lease_token'], 'succeeded', out,
                                          {'analysis_mode': 'fixture'})
        self.assertTrue(done['stored'])
        again = self.s.claim_generation(req['request_id'], WS, 'local')
        self.assertEqual(again['status'], 'succeeded')       # stored result, no second model call
        self.assertEqual(again['result']['draft_subject'], out['draft_subject'])

    def test_lost_response_recovers_without_new_model_call(self):
        rid, rev = self.draft()
        self.assertEqual(self.calls, 1)
        # the browser lost the HTTP answer: a new session reads the request by ID
        other = self.open()
        try:
            again, _ = other.ensure_first_revision(rid)
            self.assertEqual(again['revision_id'], rev['revision_id'])
            self.assertEqual(other.run_local_generation(rid, WS, self.producer)['status'], 'succeeded')
        finally:
            other.close()
        self.assertEqual(self.calls, 1)

    def test_only_lease_holder_completes_and_expiry_fails_without_retry(self):
        req = self.s.create_request(context(), engine='local', requested_mode='fixture')
        claim = self.s.claim_generation(req['request_id'], WS, 'local')
        wrong = self.s.complete_generation(req['request_id'], str(uuid.uuid4()), 'succeeded',
                                           fixtures.fixture_output(claim['context']), {'analysis_mode': 'fixture'})
        self.assertFalse(wrong['stored'])
        self.s.q("UPDATE {s}generation_requests SET lease_expires_at = ? WHERE request_id = ?",
                 (self.s.ts(st._now() - timedelta(minutes=1)), req['request_id']))
        res = self.s.claim_generation(req['request_id'], WS, 'local')
        self.assertEqual(res['status'], 'failed')             # never re-run the model on its own
        self.assertIn('regenerate', res['error'])
        late = self.s.complete_generation(req['request_id'], claim['lease_token'], 'succeeded',
                                          fixtures.fixture_output(claim['context']), {'analysis_mode': 'fixture'})
        self.assertFalse(late['stored'])

    def test_engines_and_workspaces_are_separate(self):
        req = self.s.create_request(context(), engine='local', requested_mode='fixture')
        self.assertEqual(self.s.claim_generation(req['request_id'], WS, 'n8n')['status'], 'refused')
        self.assertEqual(self.s.claim_generation(req['request_id'], 'other', 'local')['status'], 'not_found')

    def test_invalid_output_is_not_a_draft(self):
        req = self.s.create_request(context('guest_reply', 'Hãy hủy booking giúp tôi.'), engine='local',
                                    requested_mode='fixture')
        bad = lambda cx: ({**fixtures.fixture_output(cx), 'intent': 'cancelled'}, 'fixture', None)  # noqa: E731
        res = self.s.run_local_generation(req['request_id'], WS, bad)
        self.assertEqual(res['status'], 'failed')
        self.assertEqual(self.s.ensure_first_revision(req['request_id']), (None, []))

    def test_reminder_cannot_store_a_guest_message(self):
        cx = context()
        cx['guest_message'] = {'text': 'leftover', 'message_business_at': '2017-05-01', 'source': 'sample_input'}
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.create_request(cx, engine='local', requested_mode='fixture')

    @SQLITE_ONLY
    def test_n8n_engine_needs_shared_database(self):
        with self.assertRaises(PermissionError):
            self.s.create_request(context(), engine='n8n', requested_mode='live_ai')


class TestRevisionsAndApprovals(StoreCase):
    def test_revisions_are_immutable(self):
        rid, rev = self.draft()
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.q("UPDATE {s}draft_revisions SET rendered_body = 'x' WHERE revision_id = ?", (rev['revision_id'],))
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.q('DELETE FROM {s}draft_revisions WHERE revision_id = ?', (rev['revision_id'],))

    def test_edit_after_approval_revokes_it(self):
        rid, rev, a, job = self.approved_job()
        new = self.s.create_revision(rid, base_revision_id=rev['revision_id'], subject='Hello again',
                                     body='Dear guest, see you on [[booking.arrival_date]].', actor='Lan')
        self.assertEqual(new['revision_no'], 2)
        self.assertIsNotNone(self.s.get_approval(a['approval_id'])['revoked_at'])
        self.assertEqual(self.s.get_job(job['job_id'])['status'], 'failed')
        again = self.s.create_send_job(a['approval_id'])   # same job, still not sent
        self.assertEqual((again['job_id'], again['status']), (job['job_id'], 'failed'))
        self.assertIn('revoked', again['detail'])
        with self.assertRaisesRegex(ValueError, 'Cannot retry: approval revoked'):
            self.s.retry_failed_job(job['job_id'], actor='Lan')
        with self.assertRaisesRegex(ValueError, 'not the latest'):
            self.s.approve(rev['revision_id'], reviewer='Lan', delivery_mode='mock', recipient_id='-', sender_ref='')
        a2 = self.s.approve(new['revision_id'], reviewer='Lan', delivery_mode='mock', recipient_id='-', sender_ref='')
        self.assertNotEqual(self.s.create_send_job(a2['approval_id'])['send_key'], job['send_key'])

    def test_stale_base_revision_is_refused(self):
        rid, rev = self.draft()
        self.s.create_revision(rid, base_revision_id=rev['revision_id'], subject='A', body='Text A')
        with self.assertRaisesRegex(ValueError, 'newer revision'):
            self.s.create_revision(rid, base_revision_id=rev['revision_id'], subject='B', body='Text B')

    def test_blocked_revision_cannot_be_approved(self):
        rid, rev = self.draft()
        bad = self.s.create_revision(rid, base_revision_id=rev['revision_id'], subject='Refund',
                                     body='You will be refunded €50 on 9 May 2017.')
        self.assertTrue(bad['send_blockers'])
        with self.assertRaisesRegex(ValueError, 'send blockers'):
            self.s.approve(bad['revision_id'], reviewer='Lan', delivery_mode='mock', recipient_id='-', sender_ref='')

    def test_approval_is_immutable_except_one_revocation(self):
        rid, rev, a, job = self.approved_job()
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.q('UPDATE {s}approvals SET delivery_mode = ? WHERE approval_id = ?',
                     ('team_test_email', a['approval_id']))
        self.s.revoke(a['approval_id'], actor='Lan', reason='changed my mind')
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.q('UPDATE {s}approvals SET revoked_at = NULL, revoke_reason = NULL WHERE approval_id = ?',
                     (a['approval_id'],))

    def test_double_approve_returns_same_approval(self):
        rid, rev = self.draft()
        kw = dict(reviewer='Lan', delivery_mode='mock', recipient_id='-', sender_ref='')
        self.assertEqual(self.s.approve(rev['revision_id'], **kw)['approval_id'],
                         self.s.approve(rev['revision_id'], **kw)['approval_id'])

    def test_type_change_needs_its_own_approval(self):
        rid, rev, a, job = self.approved_job()          # reminder approved
        rid2, rev2 = self.draft('booking_confirmation')  # same booking, other type
        self.assertEqual(self.s.approvals(rev2['revision_id']), [])
        claim = self.s.claim_send_job(job['job_id'], WS, 'test', 'mock')
        begun = self.s.begin_dispatch(job['job_id'], claim['lease_token'])
        self.assertEqual(begun['subject'], rev['rendered_subject'])   # the reminder, never the other type
        self.assertNotEqual(rev['context_hash'], rev2['context_hash'])
        with self.assertRaises(sqlite3.IntegrityError):  # an approval cannot point at another revision's content
            self.s.q("""INSERT INTO {s}approvals (workspace_id, revision_id, email_type, booking_id, context_hash,
                        content_hash, recipient_id, recipient_address, sender_ref, delivery_mode, reviewer_label,
                        approved_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (WS, rev2['revision_id'], 'reminder', 27767, rev['context_hash'], rev['content_hash'],
                      c.MOCK_RECIPIENT_ID, c.MOCK_RECIPIENT_ADDRESS, 'mock', 'mock', 'x',
                      self.s.ts(st._now()), self.s.ts(st._now() + timedelta(hours=1))))

    def test_expired_approval_cannot_send(self):
        rid, rev = self.draft()
        a = self.s.approve(rev['revision_id'], reviewer='Lan', delivery_mode='mock', recipient_id='-',
                           sender_ref='', ttl=timedelta(seconds=1))
        time.sleep(1.2)
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.s.create_send_job(a['approval_id'])

    @SQLITE_ONLY
    def test_real_email_needs_shared_database(self):
        rid, rev = self.draft()
        with self.assertRaisesRegex(PermissionError, 'PostgreSQL'):
            self.s.approve(rev['revision_id'], reviewer='Lan', delivery_mode='team_test_email',
                           recipient_id='team', sender_ref='gmail:x')


class TestSendProtocol(StoreCase):
    def test_mock_send_end_to_end_and_no_resend(self):
        rid, rev, a, job = self.approved_job()
        res = self.s.run_local_mock_send(job['job_id'], WS)
        self.assertEqual(res['job_status'], 'simulated')
        self.assertEqual(self.s.create_send_job(a['approval_id'])['job_id'], job['job_id'])  # same key, same job
        again = self.s.claim_send_job(job['job_id'], WS, 'test', 'mock')
        self.assertEqual((again['status'], again['job_status']), ('already', 'simulated'))
        kinds = [e['kind'] for e in self.s.events('job', job['job_id'])]
        self.assertEqual(kinds, ['created', 'claimed', 'dispatching', 'dispatched'])

    def test_two_tabs_create_one_job(self):
        rid, rev = self.draft()
        a = self.s.approve(rev['revision_id'], reviewer='Lan', delivery_mode='mock', recipient_id='-', sender_ref='')
        ids, errors = [], []

        def tab():
            s = self.open()
            try:
                ids.append(s.create_send_job(a['approval_id'], session=uuid.uuid4().hex)['job_id'])
            except Exception as e:  # pragma: no cover - reported below
                errors.append(e)
            finally:
                s.close()
        threads = [threading.Thread(target=tab) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(ids)), 1)

    def test_concurrent_claims_only_one_wins(self):
        rid, rev, a, job = self.approved_job()
        results = []

        def worker():
            s = self.open()
            try:
                results.append(s.claim_send_job(job['job_id'], WS, 'w', 'mock')['status'])
            finally:
                s.close()
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count('claimed'), 1, results)
        self.assertEqual(results.count('in_progress'), 4, results)

    def test_revoke_between_claim_and_dispatch_blocks_sending(self):
        rid, rev, a, job = self.approved_job()
        claim = self.s.claim_send_job(job['job_id'], WS, 'w', 'mock')
        self.s.revoke(a['approval_id'], actor='Lan', reason='wrong wording')
        begun = self.s.begin_dispatch(job['job_id'], claim['lease_token'])
        self.assertFalse(begun['ok'])
        self.assertEqual(self.s.get_job(job['job_id'])['status'], 'failed')

    def test_revoke_and_dispatch_race_never_both(self):
        for _ in range(3):
            rid, rev, a, job = self.approved_job()
            claim = self.s.claim_send_job(job['job_id'], WS, 'w', 'mock')
            out = {}

            def dispatch():
                s = self.open()
                try:
                    out['begin'] = s.begin_dispatch(job['job_id'], claim['lease_token'])
                finally:
                    s.close()

            def revoke():
                s = self.open()
                try:
                    s.revoke(a['approval_id'], actor='x', reason='race')
                    out['revoked'] = True
                except ValueError:
                    out['revoked'] = False
                finally:
                    s.close()
            ts = [threading.Thread(target=dispatch), threading.Thread(target=revoke)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertNotEqual(out['begin']['ok'], out['revoked'], out)

    def test_old_worker_cannot_overwrite_new_one(self):
        rid, rev, a, job = self.approved_job()
        old = self.s.claim_send_job(job['job_id'], WS, 'old', 'mock')
        self.s.q('UPDATE {s}send_jobs SET lease_expires_at = ? WHERE job_id = ?',
                 (self.s.ts(st._now() - timedelta(seconds=5)), job['job_id']))
        new = self.s.claim_send_job(job['job_id'], WS, 'new', 'mock')      # lease expired, never dispatched
        self.assertEqual(new['status'], 'claimed')
        self.assertFalse(self.s.begin_dispatch(job['job_id'], old['lease_token'])['ok'])
        self.assertTrue(self.s.begin_dispatch(job['job_id'], new['lease_token'])['ok'])
        self.assertFalse(self.s.finish_dispatch(job['job_id'], old['lease_token'], 'simulated', 'x', None)['stored'])
        self.assertTrue(self.s.finish_dispatch(job['job_id'], new['lease_token'], 'simulated', 'y', None)['stored'])
        self.assertEqual(self.s.get_job(job['job_id'])['provider_message_id'], 'y')

    def test_dispatching_timeout_becomes_unknown_and_is_never_resent(self):
        # = provider accepted but the result was never written to the database
        rid, rev, a, job = self.approved_job()
        claim = self.s.claim_send_job(job['job_id'], WS, 'w', 'mock')
        self.assertTrue(self.s.begin_dispatch(job['job_id'], claim['lease_token'])['ok'])
        self.assertEqual(self.s.claim_send_job(job['job_id'], WS, 'w2', 'mock')['status'], 'in_progress')
        self.s.q('UPDATE {s}send_jobs SET dispatch_started_at = ? WHERE job_id = ?',
                 (self.s.ts(st._now() - timedelta(minutes=6)), job['job_id']))
        again = self.s.claim_send_job(job['job_id'], WS, 'w2', 'mock')
        self.assertEqual((again['status'], again['job_status']), ('already', 'unknown'))
        with self.assertRaisesRegex(ValueError, 'Only a failed send'):
            self.s.retry_failed_job(job['job_id'], actor='Lan')
        self.assertEqual(self.s.create_send_job(a['approval_id'])['job_id'], job['job_id'])
        late = self.s.finish_dispatch(job['job_id'], claim['lease_token'], 'simulated', 'x', None)
        self.assertFalse(late['stored'])

    def test_failed_send_retries_same_job_after_recheck(self):
        rid, rev, a, job = self.approved_job()
        claim = self.s.claim_send_job(job['job_id'], WS, 'w', 'mock')
        self.s.begin_dispatch(job['job_id'], claim['lease_token'])
        self.s.finish_dispatch(job['job_id'], claim['lease_token'], 'failed', None, 'auth refused')
        retried = self.s.retry_failed_job(job['job_id'], actor='Lan')
        self.assertEqual((retried['job_id'], retried['status']), (job['job_id'], 'queued'))
        self.assertEqual(self.s.run_local_mock_send(job['job_id'], WS)['job_status'], 'simulated')

    def test_illegal_transitions_are_refused_by_the_database(self):
        rid, rev, a, job = self.approved_job()
        for status in ('simulated', 'provider_accepted', 'dispatching'):
            with self.subTest(status=status), self.assertRaises(sqlite3.IntegrityError):
                self.s.q('UPDATE {s}send_jobs SET status = ?, version = version + 1 WHERE job_id = ?',
                         (status, job['job_id']))
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.q("UPDATE {s}send_jobs SET recipient_address = 'x@y.z' WHERE job_id = ?", (job['job_id'],))
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.q('DELETE FROM {s}send_jobs WHERE job_id = ?', (job['job_id'],))

    def test_wrong_outcome_for_mode_becomes_unknown(self):
        rid, rev, a, job = self.approved_job()
        claim = self.s.claim_send_job(job['job_id'], WS, 'w', 'mock')
        self.s.begin_dispatch(job['job_id'], claim['lease_token'])
        res = self.s.finish_dispatch(job['job_id'], claim['lease_token'], 'provider_accepted', 'gm-1', None)
        self.assertEqual(res['job_status'], 'unknown')

    def test_local_executor_never_sends_real_email(self):
        rid, rev, a, job = self.approved_job()
        self.s.run_local_mock_send(job['job_id'], WS)
        rid2, rev2 = self.draft('booking_confirmation')
        if not self.s.pg:
            return
        st.upsert_test_recipients(self.s, [('team', 'team@example.com')])
        a2 = self.s.approve(rev2['revision_id'], reviewer='Lan', delivery_mode='team_test_email',
                            recipient_id='team', sender_ref='gmail:x')
        j2 = self.s.create_send_job(a2['approval_id'])
        with self.assertRaises(PermissionError):
            self.s.run_local_mock_send(j2['job_id'], WS)


@PG_ONLY
class TestRealEmailBindings(StoreCase):
    def setUp(self):
        super().setUp()
        st.upsert_test_recipients(self.s, [('team', 'Team@Example.com'), ('alt', 'alt@example.com')])
        rid, self.rev = self.draft()

    def approve(self, recipient='team', sender='gmail:g2-team-test-sender'):
        return self.s.approve(self.rev['revision_id'], reviewer='Lan', delivery_mode='team_test_email',
                              recipient_id=recipient, sender_ref=sender)

    def test_allowlist_and_normalised_address(self):
        a = self.approve()
        self.assertEqual(a['recipient_address'], 'team@example.com')
        with self.assertRaisesRegex(PermissionError, 'allowlist'):
            self.approve(recipient='attacker')

    def test_recipient_mode_and_sender_are_bound(self):
        a = self.approve()
        mock = self.s.approve(self.rev['revision_id'], reviewer='Lan', delivery_mode='mock', recipient_id='-',
                              sender_ref='')
        alt = self.approve(recipient='alt')
        keys = {self.s.create_send_job(x['approval_id'])['send_key'] for x in (a, mock, alt)}
        self.assertEqual(len(keys), 3)           # each change needs its own approval and job
        job = self.s.create_send_job(a['approval_id'])
        refused = self.s.claim_send_job(job['job_id'], WS, 'n8n', 'gmail:another-sender')
        self.assertEqual(refused['status'], 'refused')
        self.assertIn('sender changed', refused['reason'])

    def test_deactivated_recipient_blocks_at_claim(self):
        job = self.s.create_send_job(self.approve()['approval_id'])
        st.upsert_test_recipients(self.s, [('alt', 'alt@example.com')])  # team removed from the allowlist
        res = self.s.claim_send_job(job['job_id'], WS, 'n8n', 'gmail:g2-team-test-sender')
        self.assertEqual(res['status'], 'refused')
        self.assertIn('allowlist', res['reason'])

    def test_dispatch_returns_database_email(self):
        job = self.s.create_send_job(self.approve()['approval_id'])
        claim = self.s.claim_send_job(job['job_id'], WS, 'n8n', 'gmail:g2-team-test-sender')
        begun = self.s.begin_dispatch(job['job_id'], claim['lease_token'])
        self.assertEqual((begun['to'], begun['subject']), ('team@example.com', self.rev['rendered_subject']))
        self.assertTrue(begun['subject'].startswith('[DEMO] '))
        self.assertEqual(begun['content_hash'], self.rev['content_hash'])
        done = self.s.finish_dispatch(job['job_id'], claim['lease_token'], 'provider_accepted', 'gmail-id-1', 'ok')
        self.assertEqual(done['job_status'], 'provider_accepted')
        self.assertEqual(self.s.mark_receipt_verified(job['job_id'], tester='Minh')['receipt_verified_by'], 'Minh')


@PG_ONLY
class TestPgRoles(StoreCase):
    """Least privilege: the n8n role can only call the workflow functions; the app role cannot
    rewrite revisions or the allowlist."""

    def setUp(self):
        super().setUp()
        import psycopg
        self.suffix = uuid.uuid4().hex[:8]
        self.app, self.n8n = f'g2t_app_{self.suffix}', f'g2t_n8n_{self.suffix}'
        grants = (st.SQL_FILE.parent / 'g2_ai_email_roles_postgres.sql').read_text(encoding='utf-8')
        grants = grants.split('-- @grants', 1)[1].replace('g2_ai_v1', self.schema) \
            .replace('g2_ai_app', self.app).replace('g2_ai_n8n', self.n8n)
        with psycopg.connect(PG_DSN, autocommit=True) as con:
            for r in (self.app, self.n8n):
                con.execute(f"CREATE ROLE {r} LOGIN PASSWORD 'pw_{self.suffix}'")
            con.execute(grants)
        self.rid, self.rev = self.draft()

    def tearDown(self):
        import psycopg
        super().tearDown()
        with psycopg.connect(PG_DSN, autocommit=True) as con:
            for r in (self.app, self.n8n):
                con.execute(f'DROP ROLE IF EXISTS {r}')

    def role_dsn(self, role):
        from urllib.parse import quote
        from psycopg.conninfo import conninfo_to_dict
        info = conninfo_to_dict(PG_DSN)
        return (f"postgresql://{role}:{quote(f'pw_{self.suffix}')}@{info.get('host', '127.0.0.1')}:"
                f"{info.get('port', 5432)}/{info.get('dbname', 'postgres')}")

    def connect_as(self, role):
        import psycopg
        return psycopg.connect(self.role_dsn(role), autocommit=True)

    def test_n8n_role_has_no_table_access(self):
        import psycopg
        with self.connect_as(self.n8n) as con:
            for sql in (f'SELECT * FROM {self.schema}.approvals',
                        f"UPDATE {self.schema}.send_jobs SET status = 'queued'",
                        f"INSERT INTO {self.schema}.test_recipients (recipient_id, address) VALUES ('x', 'x@y.z')"):
                with self.subTest(sql=sql), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    con.execute(sql)
            r = con.execute(f'SELECT {self.schema}.claim_send_job(%s::uuid, %s, %s, %s) AS r',
                            (str(uuid.uuid4()), WS, 'n8n', 'x')).fetchone()[0]
            self.assertEqual(r['status'], 'refused')

    def test_app_role_cannot_rewrite_revisions_or_allowlist(self):
        import psycopg
        with self.connect_as(self.app) as con:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                con.execute(f"INSERT INTO {self.schema}.test_recipients (recipient_id, address) VALUES ('x','x@y.z')")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                con.execute(f"UPDATE {self.schema}.draft_revisions SET rendered_body = 'x'")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                con.execute(f"UPDATE {self.schema}.generation_requests SET status = 'succeeded'")
            con.execute(f'SELECT count(*) FROM {self.schema}.send_jobs')

    def test_app_role_runs_the_whole_mock_flow(self):
        s = st.AiStore(self.role_dsn(self.app), schema=self.schema)
        try:
            rid = s.create_request(context('booking_confirmation'), engine='local',
                                   requested_mode='fixture')['request_id']
            s.run_local_generation(rid, WS, self.producer)
            rev, _ = s.ensure_first_revision(rid)
            rev2 = s.create_revision(rid, base_revision_id=rev['revision_id'], subject='Hi',
                                     body='Dear guest, see you on [[booking.arrival_date]].')
            a = s.approve(rev2['revision_id'], reviewer='Lan', delivery_mode='mock', recipient_id='-', sender_ref='')
            job = s.create_send_job(a['approval_id'])
            self.assertEqual(s.run_local_mock_send(job['job_id'], WS)['job_status'], 'simulated')
            with self.assertRaisesRegex(ValueError, 'Only a failed send'):
                s.retry_failed_job(job['job_id'], actor='Lan')
        finally:
            s.close()


if __name__ == '__main__':
    unittest.main()
