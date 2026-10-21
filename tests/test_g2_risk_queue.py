"""Risk Queue page: filters, "select all eligible", and batch approval with a confirmation step
(through state_store.review_plans, so nothing is approved twice); batch eligibility rules."""
import os
import tempfile
import unittest
from datetime import date

import pandas as pd

from dashboard import state_store as ss, workflow as wf
from dashboard.data_contract import REPO, load_outputs
from _store import store_target  # tests/ is on sys.path under discover
from test_g2_app import AppCase, AppTest

OK = {'consent_confirmed': True, 'no_open_conflict': True, 'booking_active': True}
POLICY = {'window_days': 7, 'min_days_before_arrival': 8}


class TestBatchRules(unittest.TestCase):
    def row(self, rule='I2', score='2017-01-01', arrival='2017-02-01'):
        return {'rule': rule, 'score_time': date.fromisoformat(score),
                'arrival_date': date.fromisoformat(arrival)}

    def test_only_awaiting_routine_plans_that_can_still_run(self):
        d = date(2017, 1, 2)
        self.assertIsNone(wf.bulk_blocker(self.row(), wf.AWAITING, d, POLICY))
        for review, reason in ((wf.APPROVED, 'Already approved'), (wf.REJECTED, 'Rejected'),
                               (wf.MANUAL, 'No call slot'), (wf.NO_CONTACT, 'No contact')):
            self.assertIn(reason, wf.bulk_blocker(self.row(), review, d, POLICY))
        self.assertIn('Arrives today', wf.bulk_blocker(self.row(arrival='2017-01-02'), wf.AWAITING, d, POLICY))
        self.assertIn('Call window over', wf.bulk_blocker(self.row('I3'), wf.AWAITING, date(2017, 1, 8), POLICY))
        self.assertIsNone(wf.bulk_blocker(self.row('I3'), wf.AWAITING, date(2017, 1, 7), POLICY))
        self.assertIn('Too close', wf.bulk_blocker(self.row('I3', arrival='2017-01-09'), wf.AWAITING, d, POLICY))
        self.assertIsNone(wf.bulk_blocker(self.row('I2', arrival='2017-01-09'), wf.AWAITING, d, POLICY))


class TestExecutionStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue, cls.schedule, cls.meta = load_outputs(REPO)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.con = ss.connect(store_target(self.tmp.name, 'rq'), run_id=self.meta['policy_key'],
                              call_capacity=self.meta['call_capacity_per_day'],
                              execution_policy=self.meta['execution_policy'])

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def approve(self, bid):
        rows = self.schedule[self.schedule['booking_id'].eq(bid)].to_dict('records')
        arrival = self.queue.set_index('booking_id').at[bid, 'arrival_date']
        return ss.review_plan(self.con, bid, rows, arrival_date=arrival, reviewer='Res lead',
                              approve=True, as_of=date(2017, 1, 1))[0]

    def test_statuses_follow_the_log(self):
        [call] = self.approve(22719)               # I3 call due 2017-01-01
        d0 = date(2017, 1, 1)
        st = wf.action_status(self.con, d0).set_index('action_id')['execution_status']
        self.assertEqual(st[call], wf.DUE)
        self.assertEqual(wf.booking_execution(wf.action_status(self.con, d0))[22719], wf.DUE)
        ss.record_execution(self.con, call, as_of=d0, outcome='no_response', precheck=OK)
        acts = wf.action_status(self.con, d0)
        self.assertEqual(acts.set_index('action_id').at[call, 'execution_status'], wf.COMPLETED)
        self.assertEqual(wf.booking_execution(acts)[22719], wf.DONE)

    def test_scheduled_failed_and_closed(self):
        ids = self.approve(64565)  # a booking with later contacts
        d0 = date(2017, 1, 1)
        acts = wf.action_status(self.con, d0).set_index('action_id')
        self.assertTrue(set(acts.loc[ids, 'execution_status']) <= {wf.DUE, wf.SCHEDULED})
        later = [i for i in ids if acts.at[i, 'execution_status'] == wf.SCHEDULED]
        if later:
            with ss._unit(self.con):
                ss._log_attempt(self.con, later[0], '2017-01-01', 'error', 'n8n down')
            self.assertEqual(wf.action_status(self.con, d0).set_index('action_id').at[later[0], 'execution_status'],
                             wf.FAILED)
        ss.close_action(self.con, ids[0], reviewer='Res lead', reason='test', as_of=d0)
        self.assertEqual(wf.action_status(self.con, d0).set_index('action_id').at[ids[0], 'execution_status'],
                         wf.CLOSED)


@unittest.skipUnless(AppTest, 'streamlit not installed')
class TestRiskQueuePage(AppCase):
    def open(self):
        return self.page(self.app(), 'risk_queue')

    def test_filters_update_the_count(self):
        at = self.open()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue(any(m.value.startswith('**420 bookings**') for m in at.markdown))
        at.text_input(key='rq_search').input('22719').run()
        self.assertTrue(any(m.value.startswith('**1 bookings**') for m in at.markdown))
        at.text_input(key='rq_search').input('').run()
        at.multiselect(key='rq_tiers').select('high').run()
        self.assertTrue(any(m.value.startswith('**63 bookings**') for m in at.markdown))

    def test_batch_approval_needs_confirmation_and_never_repeats(self):
        at = self.open()
        at.text_input(key='rq_search').input('22719').run()
        at.checkbox(key='rq_all').check().run()
        approve = next(b for b in at.button if b.label.startswith('Approve 1 recommendations'))
        approve.click().run()
        self.assertTrue(any('reviewer' in e.value for e in at.error))  # no name yet
        at.text_input(key='g2_reviewer').input('Res lead').run()
        next(b for b in at.button if b.label.startswith('Approve 1 recommendations')).click().run()
        self.assertTrue(any('This decision is final' in w.value for w in at.warning))
        con = at.session_state[f"g2_store::{os.environ['G2_DB_PATH']}"]
        self.assertEqual(ss.plan_reviews(con), [])  # nothing before the confirmation
        next(b for b in at.button if b.label == 'Confirm approval').click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue(any('1 recommendations approved, 1 contacts scheduled' in s.value
                            for s in at.success), [s.value for s in at.success])
        [review] = ss.plan_reviews(con)
        self.assertEqual((review['booking_id'], review['reviewer'], review['as_of']),
                         (22719, 'Res lead', '2017-01-01'))
        # Approved now: the booking is no longer eligible, so a second batch has nothing to approve.
        self.assertTrue(any('0 can be approved in a batch' in m.value for m in at.markdown))
        self.assertFalse(any(b.label.startswith('Approve 1') for b in at.button))

    def test_cancel_leaves_nothing(self):
        at = self.open()
        at.text_input(key='rq_search').input('22719').run()
        at.checkbox(key='rq_all').check().run()
        at.text_input(key='g2_reviewer').input('Res lead').run()
        next(b for b in at.button if b.label.startswith('Approve 1')).click().run()
        next(b for b in at.button if b.label == 'Cancel').click().run()
        self.assertFalse(any(b.label == 'Confirm approval' for b in at.button))
        con = at.session_state[f"g2_store::{os.environ['G2_DB_PATH']}"]
        self.assertEqual(ss.plan_reviews(con), [])


if __name__ == '__main__':
    unittest.main()
