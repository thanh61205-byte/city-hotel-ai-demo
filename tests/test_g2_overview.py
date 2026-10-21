"""Overview page and dashboard/workflow.py: review status per booking, due and done counts,
look-back counts, recent activity on the business timeline."""
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
D0 = date(2017, 1, 1)


class TestWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue, cls.schedule, cls.meta = load_outputs(REPO)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.con = ss.connect(store_target(self.tmp.name, 'ov'), run_id=self.meta['policy_key'],
                              call_capacity=self.meta['call_capacity_per_day'],
                              execution_policy=self.meta['execution_policy'])

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def rows(self, bid):
        return self.schedule[self.schedule['booking_id'].eq(bid)].to_dict('records')

    def arrival(self, bid):
        return self.queue.set_index('booking_id').at[bid, 'arrival_date']

    def test_open_bookings_are_known_and_not_arrived(self):
        open_ = wf.open_bookings(self.queue, D0)
        self.assertTrue((open_['score_time'] <= D0).all() and (open_['arrival_date'] > D0).all())
        self.assertEqual(len(open_[open_['track'].eq('individual model')]), 420)

    def test_review_status_follows_the_log(self):
        open_ = wf.open_bookings(self.queue, D0)
        status = wf.review_status(self.con, open_, self.schedule)
        ind = open_[open_['track'].eq('individual model')]
        self.assertEqual(set(status[ind['booking_id']]), {wf.AWAITING})  # week 1: no manual, no I5
        self.assertTrue((status[open_.loc[open_['track'].ne('individual model'), 'booking_id']]
                         == wf.GROUP).all())
        approve, reject, legacy = ind['booking_id'].iloc[:3].tolist()
        ss.review_plan(self.con, approve, self.rows(approve), arrival_date=self.arrival(approve),
                       reviewer='Res lead', approve=True, as_of=D0)
        ss.review_plan(self.con, reject, self.rows(reject), arrival_date=self.arrival(reject),
                       reviewer='Res lead', approve=False, reason='Guest is a regular', as_of=D0)
        ss.create_actions_from_schedule(self.con, self.rows(legacy), arrival_date=self.arrival(legacy))
        status = wf.review_status(self.con, open_, self.schedule)
        self.assertEqual((status[approve], status[reject], status[legacy]),
                         (wf.APPROVED, wf.REJECTED, wf.APPROVED))

    def test_manual_and_no_contact_bookings(self):
        q = self.queue
        manual = q[q['schedule_status'].eq('manual_capacity_review')].head(1)
        i5 = q[q['rule'].eq('I5')].head(1)
        status = wf.review_status(self.con, pd.concat([manual, i5]), self.schedule)
        self.assertEqual(list(status), [wf.MANUAL, wf.NO_CONTACT])

    def test_due_done_and_recent_activity(self):
        bid = 22719  # I3 call on 2017-01-01
        ss.review_plan(self.con, bid, self.rows(bid), arrival_date=self.arrival(bid),
                       reviewer='Res lead', approve=True, as_of=D0)
        due = wf.due_between(self.con, D0, date(2017, 1, 7))
        self.assertEqual(due['booking_id'].tolist(), [bid])
        [aid] = due['action_id'].tolist()
        ss.record_execution(self.con, aid, as_of=D0, outcome='no_response', precheck=OK)
        self.assertEqual(wf.done_between(self.con, D0, D0), 1)
        self.assertTrue(wf.due_between(self.con, D0, date(2017, 1, 7)).empty)
        recent = wf.recent_activity(self.con, D0)
        self.assertEqual(list(recent['what']), ['Call guest recorded: No answer',
                                                'Recommendation approved: Personal call (I3)'])
        self.assertEqual(set(recent['target']), {f'booking {bid}'})
        self.assertTrue(wf.recent_activity(self.con, date(2016, 12, 31)).empty)  # nothing before it

    def test_look_back_counts(self):
        bid = 22719  # I3 call on 2017-01-01
        [call], _ = ss.review_plan(self.con, bid, self.rows(bid), arrival_date=self.arrival(bid),
                                   reviewer='Res lead', approve=True, as_of=D0)
        ss.record_execution(self.con, call, as_of=date(2017, 1, 2), outcome='confirmed', precheck=OK)
        d1, d2 = date(2017, 1, 1), date(2017, 1, 2)
        self.assertEqual((wf.approved_between(self.con, d1, d1), wf.approved_between(self.con, d2, d2)), (1, 0))
        self.assertEqual((wf.contacts_between(self.con, d1, d1), wf.contacts_between(self.con, d2, d2)), (0, 1))
        self.assertEqual(wf.confirmed_between(self.con, d1, d2), 1)

    def test_automation_send_is_one_line_of_activity(self):
        bid = int(self.schedule.loc[self.schedule['action_type'].eq('confirm_email'), 'booking_id'].iloc[0])
        created, _ = ss.review_plan(self.con, bid, self.rows(bid), arrival_date=self.arrival(bid),
                                    reviewer='Res lead', approve=True, as_of=D0)
        email = next(a for a in created if ss.get_action(self.con, a)['action_type'] == 'confirm_email')
        ss.execute_action(self.con, email, as_of=D0, outcome='no_response', precheck=OK,
                          executed_by='n8n', dispatch=lambda a: {'accepted': True})
        what = list(wf.recent_activity(self.con, D0, booking_id=bid)['what'])
        self.assertEqual(what, ['Confirmation email sent', 'Recommendation approved: Confirmation email (I1)'])


@unittest.skipUnless(AppTest, 'streamlit not installed')
class TestOverviewPage(AppCase):
    def test_default_page_counts_and_links(self):
        at = self.app()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertIn('Overview', [h.value for h in at.main.header])
        cards = {m.label: m.value for m in at.metric}
        self.assertEqual(cards['Awaiting a decision'], '420')
        self.assertEqual((cards['New bookings scored'], cards['Contacts made']), ('0', '0'))  # 31/12
        self.assertTrue(any('Nothing recorded yet' in i.value for i in at.info))
        con = at.session_state[f"g2_store::{os.environ['G2_DB_PATH']}"]
        _, schedule, _ = load_outputs(REPO)
        rows = schedule[schedule['booking_id'].eq(22719)].to_dict('records')
        ss.review_plan(con, 22719, rows, arrival_date='2017-01-21', reviewer='Res lead',
                       approve=True, as_of=D0)
        at.run()
        self.assertEqual({m.label: m.value for m in at.metric}['Awaiting a decision'], '419')
        self.assertEqual({m.label: m.value for m in at.metric}['Due today'], '1')

    def test_look_back_after_the_first_week(self):
        self.set_today('2017-01-02', history=False)
        at = self.app()
        self.assertTrue(any(s.value.startswith('Yesterday (Sun 01 Jan)') for s in at.subheader))
        cards = {m.label: m.value for m in at.metric}
        self.assertEqual(cards['New bookings scored'], '434')
        self.assertEqual(cards['High risk'], '63')
        at.segmented_control(key='overview_period').set_value('week').run()
        self.assertTrue(any(s.value.startswith('Last 7 days (26 Dec to 01 Jan)') for s in at.subheader))

    def test_guide_can_be_hidden_and_shown_again(self):
        at = self.app()
        self.assertTrue(any('How to use this dashboard' in m.value for m in at.markdown))
        next(b for b in at.button if b.key == 'g2_hide_guide').click().run()
        self.assertFalse(any('How to use this dashboard' in m.value for m in at.markdown))
        next(b for b in at.sidebar.button if b.label == 'Show the guide').click().run()
        self.assertTrue(any('How to use this dashboard' in m.value for m in at.markdown))


if __name__ == '__main__':
    unittest.main()
