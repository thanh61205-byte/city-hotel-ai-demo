"""Prepared history (dashboard/seed_history.py): what the team did before today, written through
the store's service with every guard, consistent with the real outcomes and the same on a rebuild."""
import os
import tempfile
import unittest
from collections import Counter
from datetime import date

from dashboard import model_view as mv, seed_history as sh, state_store as ss, workflow as wf
from dashboard.data_contract import REPO, load_outputs
from _store import store_target  # tests/ is on sys.path under discover
from test_g2_app import AppCase, AppTest

TODAY = date(2017, 1, 16)  # Monday: batches of 1 and 8 Jan handled; of 15 Jan's, only the calls


class TestHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue, cls.schedule, cls.meta = load_outputs(REPO)
        cls.outcome = mv.outcomes(REPO)
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.con, cls.counts = cls.build('a')

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    @classmethod
    def open(cls, name):
        return ss.connect(store_target(cls.tmp.name, name), run_id=cls.meta['policy_key'],
                          call_capacity=cls.meta['call_capacity_per_day'],
                          execution_policy=cls.meta['execution_policy'])

    @classmethod
    def build(cls, name):
        con = cls.open(name)
        return con, sh.build(con, cls.queue, cls.schedule, cls.meta['policy_key'], TODAY, cls.outcome)

    def contacts(self, con):
        return sorted(tuple(r) for r in con.execute("""
            SELECT a.booking_id, a.action_type, i.as_of, i.outcome, i.executed_by
            FROM interventions i JOIN action_plan a USING (action_id)
            WHERE a.booking_id IS NOT NULL""").fetchall())

    def test_status_and_counts(self):
        self.assertEqual(sh.status(self.con), 'done:2017-01-15')
        self.assertFalse(sh.needs_history(self.con))
        self.assertGreater(self.counts['calls'], 0)
        self.assertGreater(self.counts['messages'], self.counts['calls'])
        self.assertEqual(self.counts['pending'], 1)

    def test_batches_approved_on_their_day_yesterdays_calls_only(self):
        reviews = ss.plan_reviews(self.con)
        self.assertEqual({r['as_of'] for r in reviews}, {'2017-01-01', '2017-01-08', '2017-01-15'})
        self.assertEqual({r['reviewer'] for r in reviews}, {sh.RESERVATIONS})
        self.assertEqual({r['decision'] for r in reviews}, {'approved'})
        self.assertEqual({r['rule_id'] for r in reviews if r['as_of'] == '2017-01-15'}, {'I3'})
        latest = self.queue[self.queue['score_time'].eq(date(2017, 1, 15))  # bookings with a G1 plan
                            & self.queue['booking_id'].isin(self.schedule['booking_id'])]
        status = wf.review_status(self.con, latest, self.schedule)
        self.assertTrue((status[latest.loc[latest['rule'].eq('I3'), 'booking_id']] == wf.APPROVED).all())
        self.assertTrue((status[latest.loc[latest['rule'].ne('I3'), 'booking_id']] == wf.AWAITING).all())

    def test_calls_due_today_fit_the_daily_capacity(self):
        acts = wf.action_status(self.con, TODAY)
        due_calls = acts[acts['execution_status'].eq(wf.DUE) & acts['action_type'].eq('phone_call')]
        self.assertLessEqual(len(due_calls), self.meta['call_capacity_per_day'])
        self.assertTrue((due_calls['due'] == TODAY.isoformat()).all())  # nothing overdue

    def test_everything_due_before_today_was_done_or_closed(self):
        acts = wf.action_status(self.con, TODAY)
        before = acts[acts['due'] < TODAY.isoformat()]
        self.assertEqual(set(before['execution_status']) - {wf.SENT, wf.COMPLETED, wf.CLOSED},
                         {wf.AWAITING_APPROVAL})  # only yesterday's room proposal waits
        self.assertTrue((acts['executed_on'].dropna() < TODAY.isoformat()).all())
        due = acts[acts['execution_status'].eq(wf.DUE)]
        from dashboard import action_panel
        self.assertTrue(all(action_panel.readiness(self.con, r, TODAY) is None
                            for r in due.to_dict('records')))

    def test_calls_by_staff_within_capacity_messages_by_automation(self):
        rows = self.contacts(self.con)
        calls = Counter(day for _, kind, day, _, _ in rows if kind == 'phone_call')
        self.assertLessEqual(max(calls.values()), self.meta['call_capacity_per_day'])
        self.assertEqual({who for _, kind, _, _, who in rows if kind == 'phone_call'}, {'staff'})
        self.assertEqual({who for _, kind, _, _, who in rows if kind in ss.AUTO_MESSAGES}, {'n8n'})
        attempts = ss.execution_attempts(self.con)
        self.assertEqual({a['result'] for a in attempts}, {'accepted'})
        self.assertEqual(len(attempts), sum(kind in ss.AUTO_MESSAGES for _, kind, *_ in rows))

    def test_answers_never_contradict_the_outcome(self):
        cancelled = set(self.outcome[self.outcome.eq(1)].index)
        confirmed = {b for b, _, _, out, _ in self.contacts(self.con) if out == 'confirmed'}
        confirmed |= {r[0] for r in self.con.execute(
            "SELECT booking_id FROM guest_responses WHERE response = 'confirmed'").fetchall()}
        self.assertTrue(confirmed)
        self.assertFalse(confirmed & cancelled)
        said_cancel = {b for b, _, _, out, _ in self.contacts(self.con) if out == 'cancelled'}
        self.assertTrue(said_cancel and said_cancel <= cancelled)

    def test_rebuild_gives_the_same_history(self):
        con, counts = self.build('b')
        try:
            self.assertEqual(counts, self.counts)
            self.assertEqual(self.contacts(con), self.contacts(self.con))
        finally:
            con.close()

    def test_room_actions(self):
        rows = self.con.execute("""SELECT a.decision_at, a.approval_status, a.approved_by, i.as_of
            FROM action_plan a LEFT JOIN interventions i USING (action_id)
            WHERE a.action_type = 'capacity_allocation' ORDER BY a.decision_at""").fetchall()
        self.assertEqual([tuple(r) for r in rows], [
            ('2017-01-01', 'approved', sh.REVENUE_MANAGER, '2017-01-01'),
            ('2017-01-08', 'approved', sh.REVENUE_MANAGER, '2017-01-08'),
            ('2017-01-15', 'pending', None, None)])

    def test_a_used_log_is_never_touched(self):
        con = self.open('used')
        try:
            ss.create_action(con, 'capacity_allocation', stay_date='2017-01-08', room_type='D',
                             decision_at='2017-01-01', known_from='2017-01-01')
            self.assertIsNone(sh.build(con, self.queue, self.schedule, self.meta['policy_key'], TODAY,
                                       self.outcome))
            self.assertIsNone(sh.status(con))
            self.assertEqual(len(ss.get_actions(con)), 1)
        finally:
            con.close()


@unittest.skipUnless(AppTest, 'streamlit not installed')
class TestHistoryInApp(AppCase):
    def test_first_open_builds_history_once(self):
        self.set_today(TODAY)
        at = self.app()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertFalse(at.error, [e.value for e in at.error])
        con = at.session_state[f"g2_store::{os.environ['G2_DB_PATH']}"]
        self.assertEqual(sh.status(con), 'done:2017-01-15')
        n = len(ss.get_actions(con))
        at.run()  # a rerun does not build again
        self.assertEqual(len(ss.get_actions(con)), n)
        cards = {m.label: m.value for m in at.metric}
        self.assertNotEqual(cards['Contacts made'], '0')
        self.assertNotEqual(cards['Awaiting a decision'], '0')  # yesterday's batch


if __name__ == '__main__':
    unittest.main()
