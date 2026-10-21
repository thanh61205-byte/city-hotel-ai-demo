"""G2 runs on the G1 policy and schedule (dashboard/g1_contract.py, data_contract.load_outputs)."""
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from dashboard import state_store as ss
from dashboard.data_contract import REPO, load_f_outputs, load_outputs, source_paths
from dashboard.g1_contract import g1_paths
from _store import SQLITE_ONLY, store_target  # tests/ is on sys.path under discover

OK = {'consent_confirmed': True, 'no_open_conflict': True, 'booking_active': True}


class TestG2OnG1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue, cls.schedule, cls.meta = load_outputs(REPO)
        cls.policy = json.loads(g1_paths(REPO)['policy'].read_text(encoding='utf-8'))
        cls.g1q = pd.read_csv(g1_paths(REPO)['queue'])

    def test_policy_and_run_are_bound(self):
        self.assertEqual(self.policy['stage_f_run'], self.meta['run_id'])
        self.assertTrue(self.meta['policy_key'].startswith(self.meta['run_id'] + '+g1-'))
        self.assertEqual(set(self.schedule['run_id']), {self.meta['policy_key']})
        self.assertEqual(self.meta['tier_high_share'], self.policy['policy']['high_share'])
        self.assertEqual(self.meta['tier_contact_share'], self.policy['policy']['contact_share'])

    def test_individual_rows_take_g1_tier_rule_status(self):
        ind = self.queue[self.queue['track'].eq('individual model')].set_index('booking_id')
        g = self.g1q.set_index('booking_id').loc[ind.index]
        self.assertTrue((ind['risk_tier'] == g['tier_g1']).all())
        self.assertTrue((ind['rule'] == g['rule_g1']).all())
        self.assertTrue((ind['schedule_status'] == g['schedule_status']).all())
        self.assertIn('f_risk_tier', ind.columns)  # F's default kept for comparison
        self.assertGreater((ind['risk_tier'] != ind['f_risk_tier']).sum(), 0)

    def test_group_rows_stay_f(self):
        _, _, _ = load_f_outputs(REPO)
        grp = self.queue[self.queue['track'].eq('group rule')]
        self.assertEqual(len(grp), 1054)
        self.assertTrue(grp['rule'].eq('G2').all() and grp['risk_tier'].isna().all())

    def test_schedule_is_g1_capacity_limited(self):
        calls = self.schedule[self.schedule['action_type'].eq('phone_call')]
        self.assertLessEqual(calls.groupby('scheduled_at').size().max(), self.meta['call_capacity_per_day'])
        self.assertGreater((calls['scheduled_at'] > calls['decision_at']).sum(), 0)
        manual = set(self.queue.loc[self.queue['schedule_status'].eq('manual_capacity_review'), 'booking_id'])
        self.assertEqual(len(manual), self.meta['manual_capacity_review'])
        self.assertFalse(manual & set(self.schedule['booking_id']))
        i5 = set(self.queue.loc[self.queue['rule'].eq('I5'), 'booking_id'])
        self.assertFalse(i5 & set(self.schedule['booking_id']))

    def test_g1_schedule_imports_into_the_store(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            con = ss.connect(store_target(tmp, 's'), run_id=self.meta['policy_key'],
                             call_capacity=self.meta['call_capacity_per_day'],
                             execution_policy=self.meta['execution_policy'])
            late = self.schedule[self.schedule['action_type'].eq('phone_call')
                                 & (self.schedule['scheduled_at'] > self.schedule['decision_at'])].iloc[0]
            bid = int(late['booking_id'])
            arrival = self.queue.set_index('booking_id').at[bid, 'arrival_date']
            ids, _ = ss.create_actions_from_schedule(
                con, self.schedule[self.schedule['booking_id'].eq(bid)].to_dict('records'),
                arrival_date=arrival)
            with self.assertRaisesRegex(ValueError, 'Not due'):
                ss.record_execution(con, ids[0], as_of=late['decision_at'], outcome='no_response', precheck=OK)
            ss.record_execution(con, ids[0], as_of=late['scheduled_at'], outcome='no_response', precheck=OK)
            con.close()


class TestG1Tampering(unittest.TestCase):
    """Every check of g1_contract refuses a handover that does not follow F + the policy."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = Path(self.tmp.name)
        for paths in (source_paths, g1_paths):
            for key, src in paths(REPO).items():
                dst = paths(self.repo)[key]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        self.p = g1_paths(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, key, fn):
        df = pd.read_csv(self.p[key])
        fn(df)
        df.to_csv(self.p[key], index=False)

    def refused(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            load_outputs(self.repo)

    def test_unchanged_copy_loads(self):
        load_outputs(self.repo)

    def test_policy_for_another_f_run(self):
        pol = json.loads(self.p['policy'].read_text(encoding='utf-8'))
        pol['stage_f_run'] = 'f-v3.1-old-run'
        self.p['policy'].write_text(json.dumps(pol), encoding='utf-8')
        self.refused('re-run notebook 05')

    def test_overbooking_enabled(self):
        pol = json.loads(self.p['policy'].read_text(encoding='utf-8'))
        pol['overbooking']['enabled'] = True
        self.p['policy'].write_text(json.dumps(pol), encoding='utf-8')
        self.refused('overbooking')

    def test_tier_changed(self):
        def flip(df):
            i = df.index[df['tier_g1'].eq('low')][0]
            df.loc[i, 'tier_g1'] = 'medium'
        self.edit('queue', flip)
        self.refused('tiers cannot be re-derived')

    def test_score_changed(self):
        self.edit('queue', lambda df: df.__setitem__('p_cancel', df['p_cancel'] * 0.99))
        self.refused('p_cancel differs from F')

    def test_old_schema_outcome_columns(self):
        self.edit('queue', lambda df: df.__setitem__('expected_net_eur', 0.0))
        self.refused('Outcome-dependent')

    def test_call_outside_window_or_over_capacity(self):
        def late(df):
            i = df.index[df['action_type'].eq('phone_call')][0]
            df.loc[i, 'scheduled_at'] = (pd.Timestamp(df.loc[i, 'decision_at'])
                                         + pd.Timedelta(days=9)).date().isoformat()
        self.edit('schedule', late)
        self.refused('window|arrival')

    def test_too_many_calls_on_a_day(self):
        pol = json.loads(self.p['policy'].read_text(encoding='utf-8'))
        pol['call_capacity_per_day'] = 1
        self.p['policy'].write_text(json.dumps(pol), encoding='utf-8')
        self.refused('more than 1 calls')

    def test_manual_review_booking_given_a_call(self):
        q = pd.read_csv(self.p['queue'])
        manual = q.loc[q['schedule_status'].eq('manual_capacity_review')].iloc[0]
        def add(df):
            df.loc[len(df)] = {'booking_id': manual['booking_id'], 'decision_at': manual['decision_at'],
                               'scheduled_at': manual['decision_at'], 'rule': 'I3',
                               'action_type': 'phone_call', 'schedule_reason': 'x', 'message_no': 1}
        self.edit('schedule', add)
        self.refused('manual-review')

    def test_reminder_moved(self):
        def move(df):
            i = df.index[df['rule'].eq('I2')][0]
            df.loc[i, 'scheduled_at'] = (pd.Timestamp(df.loc[i, 'scheduled_at'])
                                         + pd.Timedelta(days=1)).date().isoformat()
        self.edit('schedule', move)
        self.refused('milestones')


class TestDailyCallCapacity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = store_target(self.tmp.name, 'c')
        self.con = ss.connect(self.db, run_id='r', call_capacity=2)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def call(self, bid):
        return ss.create_action(self.con, 'phone_call', booking_id=bid, decision_at='2017-01-01')

    def test_third_call_of_the_day_refused(self):
        for bid in (1, 2):
            ss.record_execution(self.con, self.call(bid), as_of=date(2017, 1, 2), outcome='no_response', precheck=OK)
        third = self.call(3)
        with self.assertRaisesRegex(ValueError, 'capacity'):
            ss.record_execution(self.con, third, as_of=date(2017, 1, 2), outcome='no_response', precheck=OK)
        ss.record_execution(self.con, third, as_of=date(2017, 1, 3), outcome='no_response', precheck=OK)
        self.assertEqual(ss.calls_made(self.con, '2017-01-02'), 2)

    def test_capacity_is_fixed_per_store(self):
        self.con.close()
        with self.assertRaisesRegex(ValueError, 'calls/day'):
            ss.connect(self.db, run_id='r', call_capacity=5)
        self.con = ss.connect(self.db, run_id='r', call_capacity=2)


if __name__ == '__main__':
    unittest.main()
