"""Two sessions at once (final review 20/10): the call limit, close vs execute and n8n hand-off
hold under concurrency, for the service (store write lock) and for direct SQL (database guards).

Each scenario opens two connections to the same store, lets the first one hold its transaction
open, starts the second in a thread and checks it waits and then sees the first one's commit.
Runs on SQLite and, with G2_TEST_PG_DSN, on PostgreSQL.
"""
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest import mock

from dashboard import n8n_client, pg_backend, state_store as ss
from _store import PG_DSN, store_target  # tests/ is on sys.path under discover

OK = dict.fromkeys(ss.PRECHECKS, True)
DAY = '2017-01-02'
CAP = 20
HOLD = 0.8  # seconds the first session keeps its transaction open


class TwoSessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.target = store_target(self.tmp.name, 'race')
        self.a = self.open()
        self.calls = [ss.create_action(self.a, 'phone_call', booking_id=1000 + i, decision_at=DAY)
                      for i in range(CAP + 1)]

    def tearDown(self):
        self.a.close()
        self.tmp.cleanup()

    def open(self):
        return ss.connect(self.target, run_id='race', call_capacity=CAP, check_same_thread=False)

    def logged_calls(self):
        return ss.calls_made(self.a, DAY)

    def fill(self, n):
        for aid in self.calls[:n]:
            ss.record_execution(self.a, aid, as_of=DAY, outcome='no_response', precheck=OK)

    def second_session(self, work):
        """Run work(con) on a second connection in a thread; return (future, connection)."""
        b = self.open()
        pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(pool.shutdown)
        self.addCleanup(b.close)
        return pool.submit(work, b)


class TestCallCapacity(TwoSessions):
    def test_last_slot_goes_to_one_session_only(self):
        self.fill(CAP - 1)
        inside, release = threading.Event(), threading.Event()

        def slow_dispatch(action):  # session A holds the unit (and the last slot) meanwhile
            inside.set()
            release.wait(10)
            return {'accepted': True}

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(ss.execute_action, self.a, self.calls[-2], as_of=DAY, outcome='no_response',
                                precheck=OK, dispatch=slow_dispatch)
            self.assertTrue(inside.wait(10))
            second = self.second_session(lambda b: ss.record_execution(
                b, self.calls[-1], as_of=DAY, outcome='no_response', precheck=OK))
            time.sleep(HOLD)
            self.assertFalse(second.done(), 'the second session must wait for the first unit')
            release.set()
            self.assertIn('intervention_id', first.result(20))
        with self.assertRaisesRegex(ValueError, 'capacity'):
            second.result(20)
        self.assertEqual(self.logged_calls(), CAP)

    def test_direct_sql_writers_cannot_exceed_the_limit(self):
        self.fill(CAP - 1)
        insert = """INSERT INTO interventions (action_id, channel, as_of, executed_at, executed_by,
                    outcome, precheck) VALUES (?, 'phone', ?, 'x', 'staff', 'no_response', '{}')"""
        self.a.execute('BEGIN')
        self.a.execute(insert, (self.calls[-2], DAY))  # uncommitted 20th call

        def direct(b):
            b.execute('BEGIN')
            try:
                b.execute(insert, (self.calls[-1], DAY))
            finally:
                b.rollback()

        second = self.second_session(direct)
        time.sleep(HOLD)
        self.a.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'capacity'):
            second.result(20)
        self.assertEqual(self.logged_calls(), CAP)


class TestCloseVersusExecute(TwoSessions):
    def assert_one_outcome(self, aid):
        closed = self.a.execute('SELECT COUNT(*) FROM action_closures WHERE action_id = ?', (aid,)).fetchone()[0]
        done = self.a.execute('SELECT COUNT(*) FROM interventions WHERE action_id = ?', (aid,)).fetchone()[0]
        self.assertEqual(closed + done, 1, (closed, done))

    def test_uncommitted_closure_blocks_a_concurrent_execution(self):
        aid = self.calls[0]
        self.a.execute('BEGIN')
        self.a.execute("""INSERT INTO action_closures (action_id, reason, closed_by, as_of, closed_at)
                          VALUES (?, 'window passed', 'lead', ?, 'x')""", (aid, DAY))
        second = self.second_session(lambda b: ss.record_execution(
            b, aid, as_of=DAY, outcome='no_response', precheck=OK))
        time.sleep(HOLD)
        self.assertFalse(second.done())
        self.a.commit()
        with self.assertRaisesRegex((ValueError, sqlite3.IntegrityError), 'closed without contact'):
            second.result(20)
        self.assert_one_outcome(aid)

    def test_execution_in_progress_blocks_a_concurrent_closure(self):
        aid = self.calls[0]
        inside, release = threading.Event(), threading.Event()

        def slow_dispatch(action):
            inside.set()
            release.wait(10)
            return {'accepted': True}

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(ss.execute_action, self.a, aid, as_of=DAY, outcome='no_response',
                                precheck=OK, dispatch=slow_dispatch)
            self.assertTrue(inside.wait(10))
            second = self.second_session(lambda b: ss.close_action(
                b, aid, reviewer='lead', reason='window passed', as_of=DAY))
            time.sleep(HOLD)
            self.assertFalse(second.done())
            release.set()
            first.result(20)
        with self.assertRaisesRegex(ValueError, 'already been executed'):
            second.result(20)
        self.assert_one_outcome(aid)


class TestN8nHandOff(TwoSessions):
    def test_same_action_is_sent_once(self):
        aid = ss.create_action(self.a, 'reminder', booking_id=1, decision_at=DAY)
        inside, release = threading.Event(), threading.Event()

        def first_post(url, json, timeout):
            inside.set()
            release.wait(10)
            resp = mock.Mock()
            resp.json.return_value = {'accepted': True, 'status': 'DEMO_ACCEPTED_NO_ACTION'}
            return resp

        second_post = mock.Mock()
        send = lambda con, post: n8n_client.send_action(  # noqa: E731
            con, aid, webhook='http://unused.test', as_of=date(2017, 1, 2), outcome='no_response',
            precheck=OK, post=post)
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(send, self.a, first_post)
            self.assertTrue(inside.wait(10))
            second = self.second_session(lambda b: send(b, second_post))
            time.sleep(HOLD)
            release.set()
            self.assertIs(first.result(20)['accepted'], True)
        with self.assertRaisesRegex(ValueError, 'already been executed'):
            second.result(20)
        second_post.assert_not_called()  # n8n never received the second request

    def test_payload_carries_an_idempotency_key(self):
        aid = ss.create_action(self.a, 'reminder', booking_id=1, decision_at=DAY)
        payload = n8n_client.build_payload(self.a, ss.get_action(self.a, aid), as_of=date(2017, 1, 2),
                                           outcome='no_response', precheck=OK)
        self.assertEqual(payload['idempotency_key'], f'race:{aid}')


class TestG3InDatabase(TwoSessions):
    def test_store_refuses_overbooking_rows(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'G3 overbooking is disabled by policy'):
            self.a.execute("""INSERT INTO action_plan (stay_date, room_type, action_type, rule_id,
                decision_at, needs_approval, approval_status, approved_by, approved_at, created_at)
                VALUES ('2017-03-01','A','overbooking','G3','2017-01-01',1,'approved','RM','2026','2026')""")
        self.a.rollback()


@unittest.skipUnless(PG_DSN, 'PostgreSQL only')
class TestPostgresTransactions(TwoSessions):
    def test_reads_leave_no_open_transaction(self):
        ss.get_actions(self.a)
        ss.calls_made(self.a, DAY)
        self.assertFalse(self.a.in_transaction())

    def test_refused_unit_rolls_back(self):
        with self.assertRaises(ValueError):
            ss.close_action(self.a, self.calls[0], reviewer='x', reason='y', as_of='2016-12-01')
        self.assertFalse(self.a.in_transaction())

    def test_lock_wait_is_bounded(self):
        self.assertEqual(pg_backend.LOCK_TIMEOUT, '15s')
        self.assertEqual(self.a.execute('SHOW lock_timeout').fetchone()[0], '15s')


if __name__ == '__main__':
    unittest.main()
