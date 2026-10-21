"""G2 reads the stage-F v3.1 outputs straight from artifacts/modeling/ (dashboard/data_contract.py)."""
import json
import tempfile
import unittest
from pathlib import Path

from dashboard.data_contract import (POST_OUTCOME, REPO, cancellation_buffer, load_f_outputs,
                                     source_fingerprint, source_paths, validate_queue)


class TestG2ReadsF31(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue, cls.schedule, cls.meta = load_f_outputs(REPO)
        cls.manifest = json.loads(source_paths(REPO)['manifest'].read_text(encoding='utf-8'))

    def test_single_run_from_the_f_manifest(self):
        self.assertEqual(self.meta['run_id'], self.manifest['run_id'])
        self.assertEqual(self.meta['selected_model'], self.manifest['selected_model'])
        self.assertEqual(set(self.queue['run_id']), {self.manifest['run_id']})
        self.assertEqual(set(self.schedule['run_id']), {self.manifest['run_id']})

    def test_row_counts_match_f(self):
        self.assertEqual(len(self.queue), 13156)
        self.assertEqual((self.queue['track'] == 'individual model').sum(), 12102)
        self.assertEqual((self.queue['track'] == 'group rule').sum(), 1054)
        self.assertEqual(len(self.schedule), 22164)

    def test_no_post_outcome_fields(self):
        self.assertFalse(POST_OUTCOME.intersection(self.queue.columns))
        self.assertFalse(POST_OUTCOME.intersection(self.schedule.columns))

    def test_tiers_respect_weekly_capacity_ceilings(self):
        ind = self.queue[self.queue['track'] == 'individual model']
        for week, b in ind.groupby('score_time'):
            n = len(b)
            high = (b['risk_tier'] == 'high').sum()
            medium = (b['risk_tier'] == 'medium').sum()
            self.assertLessEqual(high, int(n * self.meta['tier_high_share']), week)
            self.assertLessEqual(high + medium, int(n * self.meta['tier_medium_share']), week)

    def test_rules_follow_decision_table(self):
        q = self.queue
        ind = q[q['track'] == 'individual model']
        self.assertTrue((ind.loc[ind['rule'] == 'I5', 'days_to_arrival'] <= 7).all())
        self.assertTrue((ind.loc[ind['rule'].isin(['I2', 'I3']), 'days_to_arrival'] >= 8).all())
        self.assertFalse(ind['needs_approval'].astype(bool).any())
        self.assertTrue((q.loc[q['track'] == 'group rule', 'rule'] == 'G2').all())
        no_action = set(q.loc[q['rule'].isin(['I5', 'G2']), 'booking_id'])
        self.assertFalse(no_action & set(self.schedule['booking_id']))

    def test_schedule_limits(self):
        s = self.schedule
        self.assertTrue((s['scheduled_at'] >= s['decision_at']).all())
        self.assertLessEqual(s[s['action_type'] == 'reminder'].groupby('booking_id').size().max(), 2)
        self.assertEqual(set(s.loc[s['rule'] == 'I3', 'action_type']), {'phone_call'})

    def test_validator_rejects_leaked_outcome_column(self):
        with self.assertRaisesRegex(ValueError, 'Post-outcome'):
            validate_queue(self.queue.assign(is_canceled=0))

    def test_failed_f_consistency_check_stops_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = source_paths(REPO), source_paths(tmp)
            for key, path in dst.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            dst['manifest'].write_bytes(src['manifest'].read_bytes())
            dst['consistency'].write_text('check,passed\nsingle run_id in every output,False\n',
                                          encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'failed checks'):
                load_f_outputs(tmp)

    def test_cancellation_buffer_holds_group_and_i5(self):
        """Astra #5: the decision table sends G2 and I5 bookings to the buffer."""
        buf = cancellation_buffer(self.queue)
        src = self.queue[self.queue['rule'].isin(['G2', 'I5'])]
        self.assertEqual(buf['bookings'].sum(), len(src))
        self.assertEqual(buf['bookings_G2'].sum(), 1054)
        self.assertEqual(buf['bookings_I5'].sum(), (self.queue['rule'] == 'I5').sum())
        self.assertGreater(buf['bookings_I5'].sum(), 0)
        self.assertAlmostEqual(buf['expected_cancellations'].sum(), src['p_cancel'].sum(), delta=0.01 * len(buf))
        self.assertAlmostEqual(buf['expected_cancellations_I5'].sum(),
                               self.queue.loc[self.queue['rule'] == 'I5', 'p_cancel'].sum(), delta=0.05)
        self.assertFalse(buf.duplicated(['arrival_date', 'room_type']).any())

    def test_fingerprint_changes_when_f_rewrites_a_file(self):
        """Astra #4: the dashboard cache key follows the files on disk."""
        with tempfile.TemporaryDirectory() as tmp:
            for path in source_paths(tmp).values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('a', encoding='utf-8')
            before = source_fingerprint(tmp)
            self.assertEqual(before, source_fingerprint(tmp))
            source_paths(tmp)['manifest'].write_text('ab', encoding='utf-8')
            self.assertNotEqual(before, source_fingerprint(tmp))

if __name__ == '__main__':
    unittest.main()
