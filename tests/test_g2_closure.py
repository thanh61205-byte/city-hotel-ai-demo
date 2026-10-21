"""Closing an open action without contact: an expired G1 call must not leave a booking stuck."""
import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from dashboard import n8n_client, state_store as ss
from _store import SQLITE_ONLY, store_target  # tests/ is on sys.path under discover
from test_g2_app import AppCase, AppTest  # tests/ is on sys.path under discover
from test_g2_n8n_client import reply

OK = {'consent_confirmed': True, 'no_open_conflict': True, 'booking_active': True}
POLICY = {'overbooking': {'enabled': False},
          'call_schedule': {'start_day': 0, 'window_days': 7, 'min_days_before_arrival': 8}}
RUN = 'f-test+g1-test'
# Booking 22719 of the audit: decided 01/01/2017, arrives 21/01; G1 call on the decision day.
CALL = {'run_id': RUN, 'booking_id': 22719, 'decision_at': '2017-01-01', 'scheduled_at': '2017-01-01',
        'rule': 'I3', 'action_type': 'phone_call', 'message_no': 1}
TIMES = dict(known_from='2017-01-01', arrival_date='2017-01-21')


class ClosureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.con = ss.connect(store_target(self.tmp.name, 'c'), run_id=RUN, call_capacity=20,
                              execution_policy=POLICY)
        self.call = ss.create_actions_from_schedule(self.con, [CALL], arrival_date='2017-01-21')[0][0]

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def close(self, aid=None, when='2017-01-09', who='Reservations lead', why='G1 call window passed'):
        return ss.close_action(self.con, aid or self.call, reviewer=who, reason=why, as_of=when)

    def reminder(self, when='2017-01-09'):
        return ss.create_action(self.con, 'reminder', booking_id=22719, decision_at=when, **TIMES)


class TestExpiredCall(ClosureCase):
    def test_expired_call_used_to_leave_the_booking_stuck(self):
        with self.assertRaisesRegex(ValueError, 'Outside G1 call window'):
            ss.record_execution(self.con, self.call, as_of='2017-01-09', outcome='no_response', precheck=OK)
        with self.assertRaisesRegex(ValueError, 'call goes first'):
            ss.record_execution(self.con, self.reminder(), as_of='2017-01-09', outcome='no_response', precheck=OK)
        with self.assertRaisesRegex(ValueError, 'Only pending'):
            ss.decide_action(self.con, self.call, 'RM', False)

    def test_closing_frees_the_booking_for_a_fallback_reminder(self):
        self.close()
        ss.record_execution(self.con, self.reminder(), as_of='2017-01-09', outcome='no_response', precheck=OK)
        self.assertEqual(ss.get_actions(self.con, 22719)[-1]['closed'], '2017-01-09: G1 call window passed')

    def test_closed_action_never_executes(self):
        self.close()
        with self.assertRaisesRegex(ValueError, 'closed without contact'):
            ss.record_execution(self.con, self.call, as_of='2017-01-09', outcome='no_response', precheck=OK)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'closed without contact'):
            self.con.execute("INSERT INTO interventions (action_id, channel, as_of, executed_at, executed_by, "
                             "outcome, precheck) VALUES (?, 'phone', '2017-01-09', 'x', 'staff', 'no_response', '{}')",
                             (self.call,))
        post = reply(True)
        with self.assertRaisesRegex(ValueError, 'closed without contact'):
            n8n_client.send_action(self.con, self.call, webhook='http://unused.test', as_of=date(2017, 1, 9),
                                   outcome='no_response', precheck=OK, post=post)
        post.assert_not_called()

    def test_closed_call_is_not_an_attempt(self):
        self.close(when='2017-01-02')
        for day in ('2017-01-02', '2017-01-03'):  # still two real attempts inside the window
            c = ss.create_action(self.con, 'phone_call', booking_id=22719, decision_at=day, **TIMES)
            ss.record_execution(self.con, c, as_of=day, outcome='no_response', precheck=OK)
        with self.assertRaisesRegex(ValueError, 'max 2'):
            ss.create_action(self.con, 'phone_call', booking_id=22719, decision_at='2017-01-04', **TIMES)


class TestClosureRules(ClosureCase):
    def test_who_and_why_required(self):
        for who, why in (('', 'x'), ('rm', '  ')):
            with self.assertRaisesRegex(ValueError, 'who closes'):
                self.close(who=who, why=why)

    def test_cannot_close_executed_pending_or_twice(self):
        ss.record_execution(self.con, self.call, as_of='2017-01-01', outcome='no_response', precheck=OK)
        with self.assertRaisesRegex(ValueError, 'already been executed'):
            self.close(when='2017-01-02')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'executed action'):
            self.con.execute("INSERT INTO action_closures (action_id, reason, closed_by, as_of, closed_at) "
                             "VALUES (?, 'x', 'y', '2017-01-02', 'z')", (self.call,))
        resale = ss.create_action(self.con, 'resale', stay_date='2017-03-01', room_type='A', rule_id='G2',
                                  decision_at='2017-01-01')
        with self.assertRaisesRegex(ValueError, 'reject a pending'):
            self.close(resale)
        ss.decide_action(self.con, resale, 'RM', True)
        self.close(resale, why='rooms no longer available')
        with self.assertRaisesRegex(ValueError, 'already closed'):
            self.close(resale)

    def test_closure_log_is_append_only_and_moves_forward(self):
        self.close()
        for sql in ("UPDATE action_closures SET reason = 'x'", 'DELETE FROM action_closures'):
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
                self.con.execute(sql)
        with self.assertRaisesRegex(ValueError, 'Back-dated'):
            self.reminder(when='2017-01-05')

    def test_cannot_close_before_decision(self):
        with self.assertRaisesRegex(ValueError, 'before the decision'):
            self.close(when='2016-12-31')


@unittest.skipUnless(AppTest, 'streamlit not installed')
class TestClosureUI(AppCase):
    def test_close_form_closes_the_selected_action(self):
        from dashboard.data_contract import load_outputs
        _, schedule, meta = load_outputs()
        con = ss.connect(os.environ['G2_DB_PATH'], run_id=meta['policy_key'],
                         call_capacity=meta['call_capacity_per_day'], execution_policy=meta['execution_policy'])
        try:
            row = schedule[schedule['booking_id'].eq(22719)].to_dict('records')
            aid = ss.create_actions_from_schedule(con, row, arrival_date='2017-01-21')[0][0]
            self.set_today(date(2017, 1, 9))
            at = self.page(self.app(), 'execution')
            next(x for x in at.selectbox if x.label == 'Open action').set_value(aid).run()
            next(x for x in at.text_input if x.label == 'Closed by').set_value('Reservations lead')
            next(x for x in at.text_input if x.label == 'Reason').set_value('G1 call window passed')
            next(x for x in at.button if x.label == 'Close action without contact').click().run()
            self.assertFalse(at.exception, [x.value for x in at.exception])
            self.assertTrue(any('closed without contact' in x.value for x in at.success),
                            [x.value for x in at.error])
            self.assertIsNotNone(con.execute('SELECT 1 FROM action_closures WHERE action_id = ?', (aid,)).fetchone())
        finally:
            con.close()


if __name__ == '__main__':
    unittest.main()
