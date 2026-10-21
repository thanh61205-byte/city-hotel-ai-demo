"""Schema 9: staff review of routine G1 plans (plan_reviews) and the automation attempt log
(execution_attempts); plain-language refusals (dashboard/vocab.py)."""
import sqlite3
import tempfile
import unittest
from datetime import date
from unittest import mock

from dashboard import n8n_client, state_store as ss, vocab
from _store import store_target  # tests/ is on sys.path under discover

OK = {'consent_confirmed': True, 'no_open_conflict': True, 'booking_active': True}
URL = 'http://localhost:5678/webhook-test/g2-hotel-approval-demo'


def plan(bid, rule='I2', decided='2017-01-01', dates=('2017-01-05', '2017-01-21'), kind='reminder'):
    return [{'run_id': 'r', 'booking_id': bid, 'decision_at': decided, 'scheduled_at': d,
             'rule': rule, 'action_type': kind, 'message_no': n} for n, d in enumerate(dates, 1)]


def post_returning(body):
    resp = mock.Mock()
    resp.json.return_value = body
    return mock.Mock(return_value=resp)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = ss.connect(store_target(self.tmp.name, 's9'), run_id='r')

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()


class TestPlanReview(StoreCase):
    def review(self, bid, rows, approve=True, reason=None, as_of='2017-01-02', reviewer='Res lead'):
        return ss.review_plan(self.con, bid, rows, arrival_date='2017-02-01', reviewer=reviewer,
                              approve=approve, reason=reason, as_of=as_of)

    def test_approve_imports_the_plan_and_records_who(self):
        created, existing = self.review(900, plan(900))
        self.assertEqual((len(created), existing), (2, 0))
        r = ss.plan_review(self.con, 900)
        self.assertEqual((r['decision'], r['reviewer'], r['as_of'], r['rule_id']),
                         ('approved', 'Res lead', '2017-01-02', 'I2'))
        self.assertTrue(all(a['approval_status'] == 'auto' for a in ss.get_actions(self.con, 900)))

    def test_decision_is_final(self):
        self.review(900, plan(900))
        with self.assertRaisesRegex(ValueError, 'already approved'):
            self.review(900, plan(900), approve=False, reason='duplicate')
        with self.assertRaises(sqlite3.IntegrityError):
            with ss._unit(self.con):
                self.con.execute("UPDATE plan_reviews SET decision = 'rejected', reason = 'x'")
        with self.assertRaises(sqlite3.IntegrityError):
            with ss._unit(self.con):
                self.con.execute('DELETE FROM plan_reviews')

    def test_reject_needs_reason_and_blocks_the_g1_plan(self):
        with self.assertRaisesRegex(ValueError, 'reason'):
            self.review(901, plan(901), approve=False)
        created, _ = self.review(901, plan(901), approve=False, reason='Guest already confirmed by phone')
        self.assertEqual(created, [])
        self.assertEqual(ss.get_actions(self.con, 901), [])
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'rejected at review'):
            ss.create_actions_from_schedule(self.con, plan(901), arrival_date='2017-02-01')
        # A manual action (Edit = replace) is still possible for a rejected booking.
        aid = ss.create_action(self.con, 'phone_call', booking_id=901, decision_at='2017-01-02',
                               arrival_date='2017-02-01')
        self.assertTrue(aid)

    def test_reviewer_and_replay_window(self):
        with self.assertRaisesRegex(ValueError, 'reviewer'):
            self.review(902, plan(902), reviewer=' ')
        with self.assertRaisesRegex(ValueError, 'Not known yet'):
            self.review(902, plan(902, decided='2017-01-08', dates=('2017-01-09',)), as_of='2017-01-02')
        with self.assertRaisesRegex(ValueError, 'Too late'):
            self.review(902, plan(902), as_of='2017-02-01')
        self.assertIsNone(ss.plan_review(self.con, 902))  # refused reviews leave nothing behind

    def test_only_routine_plans(self):
        with self.assertRaisesRegex(ValueError, 'no G1 plan'):
            self.review(903, [])
        rows = plan(903) + plan(903, rule='I3', dates=('2017-01-03',), kind='phone_call')
        with self.assertRaisesRegex(ValueError, 'routine'):
            self.review(903, rows)
        with self.assertRaisesRegex(ValueError, 'this booking only'):
            self.review(903, plan(904))

    def test_batch_reports_each_booking_and_never_duplicates(self):
        self.review(910, plan(910))  # already reviewed before the batch
        plans = [(910, plan(910), '2017-02-01'), (911, plan(911), '2017-02-01'),
                 (912, plan(912, decided='2017-01-08', dates=('2017-01-09',)), '2017-02-01'),
                 (913, plan(913), '2017-02-01')]
        out = ss.review_plans(self.con, plans, reviewer='Res lead', as_of='2017-01-02', batch_id='b1')
        self.assertEqual([r['status'] for r in out], ['skipped', 'approved', 'skipped', 'approved'])
        self.assertIn('already approved', out[0]['message'])
        self.assertIn('Not known yet', out[2]['message'])
        self.assertEqual({r['booking_id'] for r in ss.plan_reviews(self.con) if r['batch_id'] == 'b1'},
                         {911, 913})
        again = ss.review_plans(self.con, plans, reviewer='Res lead', as_of='2017-01-02')
        self.assertEqual({r['status'] for r in again}, {'skipped'})
        self.assertEqual(len(ss.get_actions(self.con, 911)), 2)  # second click created nothing


class TestExecutionAttempts(StoreCase):
    def setUp(self):
        super().setUp()
        self.ids, _ = ss.create_actions_from_schedule(self.con, plan(920), arrival_date='2017-02-01')

    def send(self, post, as_of=date(2017, 1, 5), precheck=OK):
        return n8n_client.send_action(self.con, self.ids[0], webhook=URL, as_of=as_of,
                                      outcome='no_response', precheck=precheck, post=post)

    def test_accepted_attempt_points_to_the_execution(self):
        body = self.send(post_returning({'accepted': True}))
        [att] = ss.execution_attempts(self.con, self.ids[0])
        self.assertEqual((att['result'], att['intervention_id'], att['as_of']),
                         ('accepted', body['intervention_id'], '2017-01-05'))

    def test_refused_attempt_is_kept_without_execution(self):
        body = self.send(post_returning({'accepted': False, 'reasons': ['not due', 'no consent']}))
        self.assertFalse(body['accepted'])
        [att] = ss.execution_attempts(self.con, self.ids[0])
        self.assertEqual((att['result'], att['detail'], att['intervention_id']),
                         ('refused', 'not due; no consent', None))
        self.assertIsNone(ss.get_actions(self.con, 920)[-1]['intervention_id'])

    def test_network_error_is_a_failed_attempt_and_can_be_retried(self):
        boom = mock.Mock(side_effect=ConnectionError('n8n down'))
        with self.assertRaises(ConnectionError):
            self.send(boom)
        [att] = ss.execution_attempts(self.con, self.ids[0])
        self.assertEqual(att['result'], 'error')
        self.assertIn('n8n down', att['detail'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM interventions').fetchone()[0], 0)
        self.send(post_returning({'accepted': True}))  # retry
        self.assertEqual([a['result'] for a in ss.execution_attempts(self.con, self.ids[0])],
                         ['error', 'accepted'])

    def test_unreadable_reply_is_a_failed_attempt(self):
        resp = mock.Mock()
        resp.json.side_effect = ValueError('Expecting value')
        with self.assertRaises(ValueError):
            self.send(mock.Mock(return_value=resp))
        self.assertEqual([a['result'] for a in ss.execution_attempts(self.con)], ['error'])

    def test_locally_blocked_is_not_an_attempt(self):
        post = post_returning({'accepted': True})
        with self.assertRaises(PermissionError):
            self.send(post, precheck={})
        with self.assertRaisesRegex(ValueError, 'Not due yet'):
            self.send(post, as_of=date(2017, 1, 4))
        post.assert_not_called()
        self.assertEqual(ss.execution_attempts(self.con), [])

    def test_staff_execution_has_no_attempt(self):
        ss.record_execution(self.con, self.ids[0], as_of='2017-01-05', outcome='no_response', precheck=OK)
        self.assertEqual(ss.execution_attempts(self.con), [])

    def test_attempt_log_is_append_only_and_consistent(self):
        self.send(post_returning({'accepted': False, 'reasons': ['x']}))
        for sql in ("UPDATE execution_attempts SET result = 'accepted'", 'DELETE FROM execution_attempts'):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                with ss._unit(self.con):
                    self.con.execute(sql)
        with self.assertRaises(sqlite3.IntegrityError):  # accepted without an execution
            with ss._unit(self.con):
                ss._log_attempt(self.con, self.ids[1], '2017-01-21', 'accepted')


class TestFriendlyErrors(StoreCase):
    """Real refusals of the store read as plain sentences with the next step."""

    def refusal(self, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except (ValueError, PermissionError, sqlite3.Error) as e:
            return vocab.friendly_error(e)
        self.fail('expected a refusal')

    def test_store_refusals(self):
        [aid, _] = ss.create_actions_from_schedule(self.con, plan(930), arrival_date='2017-02-01')[0]
        run = lambda **k: ss.record_execution(self.con, aid, outcome='no_response', **k)  # noqa: E731
        self.assertIn('Not due yet: planned for 2017-01-05',
                      self.refusal(run, as_of='2017-01-04', precheck=OK))
        self.assertIn('Tick the three checks', self.refusal(run, as_of='2017-01-05', precheck={}))
        run(as_of='2017-01-06', precheck=OK)
        self.assertIn('Already done', self.refusal(run, as_of='2017-01-06', precheck=OK))
        offer = lambda: ss.create_action(self.con, 'offer_change', booking_id=931,  # noqa: E731
                                         decision_at='2017-01-02', arrival_date='2017-02-01')
        self.assertIn('change offer (I4)', self.refusal(offer))
        self.assertIn('reviewer', self.refusal(ss.decide_action, self.con, aid, '', True))
        self.assertIn('Already decided', self.refusal(ss.decide_action, self.con, aid, 'RM', True))

    def test_messages_and_unknown_text(self):
        cases = {
            'Daily call capacity reached: 20 calls already made on 2017-01-03': "call limit",
            'Outside G1 call window: manual review required; do not reset': 'call window has passed',
            'Stopped: guest already responded (confirmed)': 'already answered (confirmed)',
            'Back-dated: booking 5 already has an event on 2017-01-09; replay': 'entry on 2017-01-09',
            'Blocked: call #12 is due: the call goes first (I3 > I2 > I1)': 'Call #12',
            'database is locked': 'try again',
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertIn(expected, vocab.friendly_error(message))
        self.assertEqual(vocab.friendly_error('Something new'), 'Something new')

    def test_labels(self):
        self.assertEqual(vocab.rule('I3'), 'Personal call (I3)')
        self.assertEqual(vocab.rule('I3', with_code=False), 'Personal call')
        self.assertEqual(vocab.rule('XYZ'), 'XYZ')
        self.assertEqual(vocab.action('capacity_allocation'), 'Hold rooms back')
        self.assertEqual(vocab.outcome('no_response'), 'No answer')
        for code in ss.ACTIONS:
            self.assertNotEqual(vocab.action(code), code)
        for code in ss.GUEST_OUTCOMES + ss.DATE_OUTCOMES:
            self.assertNotEqual(vocab.outcome(code), code)


class TestReplaceAction(StoreCase):
    """Edit = replace: close + new proposal in one unit of work."""

    def setUp(self):
        super().setUp()
        self.ids, _ = ss.create_actions_from_schedule(self.con, plan(940), arrival_date='2017-02-01')

    def test_reminder_replaced_by_a_call(self):
        closure, new = ss.replace_action(self.con, self.ids[0], 'phone_call', reviewer='Res lead',
                                         reason='Guest prefers a call', as_of='2017-01-03')
        old = [a for a in ss.get_actions(self.con, 940) if a['action_id'] == self.ids[0]][0]
        self.assertIn('Replaced by phone_call: Guest prefers a call', old['closed'])
        n = ss.get_action(self.con, new)
        self.assertEqual((n['action_type'], n['decision_at'], n['booking_id'], n['approval_status']),
                         ('phone_call', '2017-01-03', 940, 'auto'))

    def test_sensitive_replacement_waits_for_approval(self):
        _, new = ss.replace_action(self.con, self.ids[0], 'request_deposit', reviewer='Res lead',
                                   reason='No deposit, high risk', as_of='2017-01-03',
                                   deposit_policy_enabled=True, risk_tier='high',
                                   deposit_type='No Deposit', note='Policy 4.2')
        self.assertEqual(ss.get_action(self.con, new)['approval_status'], 'pending')

    def test_refused_replacement_keeps_the_old_action_open(self):
        with self.assertRaisesRegex(ValueError, 'I4'):
            ss.replace_action(self.con, self.ids[0], 'offer_change', reviewer='Res lead',
                              reason='Offer a change', as_of='2017-01-03')
        old = [a for a in ss.get_actions(self.con, 940) if a['action_id'] == self.ids[0]][0]
        self.assertIsNone(old['closed'])
        with self.assertRaisesRegex(ValueError, 'who changes'):
            ss.replace_action(self.con, self.ids[0], 'phone_call', reviewer='', reason='x',
                              as_of='2017-01-03')


if __name__ == '__main__':
    unittest.main()
