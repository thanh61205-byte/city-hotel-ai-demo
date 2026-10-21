"""Second review (R2): replay-date buffer, schedule validation on a changed file, F eligibility."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from dashboard.data_contract import (REPO, cancellation_buffer, load_f_outputs, source_paths,
                                     validate_schedule)

sys.path.insert(0, str(REPO / 'src'))
import f_modeling as fm  # noqa: E402


class TestBufferAsOf(unittest.TestCase):
    """R2-01: the buffer only holds scores that existed on the replay date."""

    @classmethod
    def setUpClass(cls):
        cls.queue, cls.schedule, _ = load_f_outputs(REPO)

    def cell(self, buf, arrival, room):
        row = buf[(buf['arrival_date'].astype(str) == arrival) & (buf['room_type'] == room)]
        return row.iloc[0] if len(row) else None

    def test_review_example_22_may_room_a(self):
        early = self.cell(cancellation_buffer(self.queue, '2017-02-26'), '2017-05-22', 'A')
        year = self.cell(cancellation_buffer(self.queue), '2017-05-22', 'A')
        self.assertEqual((int(early['bookings']), round(early['expected_cancellations'], 2)), (8, 2.21))
        self.assertEqual((int(year['bookings']), round(year['expected_cancellations'], 2)), (31, 11.09))

    def test_every_replay_date_uses_only_known_scores(self):
        src = self.queue[self.queue['rule'].isin(['G2', 'I5'])]
        for day in pd.date_range('2017-01-01', '2017-08-31', freq='9D').date:
            buf = cancellation_buffer(self.queue, day)
            known = src[(src['score_time'] <= day) & (src['arrival_date'] > day)]
            self.assertEqual(buf['bookings'].sum(), len(known), day)
            self.assertAlmostEqual(buf['expected_cancellations'].sum(), known['p_cancel'].sum(),
                                   delta=0.01 * max(len(buf), 1))
            if len(buf):
                self.assertTrue((buf['first_decision'] <= day).all())
                self.assertTrue((buf['arrival_date'] > day).all())


class TestScheduleValidation(unittest.TestCase):
    """R2-04: the loader re-derives the schedule shape instead of trusting the stored check."""

    @classmethod
    def setUpClass(cls):
        cls.queue, cls.schedule, _ = load_f_outputs(REPO)
        cls.raw = pd.read_csv(source_paths(REPO)['schedule'])

    def check(self, frame):
        return validate_schedule(frame, self.queue)

    def test_current_schedule_passes(self):
        self.check(self.raw)

    def test_review_fixture_reminder_turned_into_call_is_refused_by_the_full_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            for key, src in source_paths(REPO).items():
                dst = source_paths(tmp)[key]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            s = pd.read_csv(source_paths(tmp)['schedule'])
            hit = s['booking_id'].eq(22288) & s['scheduled_at'].eq('2017-01-03')
            self.assertEqual(s.loc[hit, ['rule', 'action_type']].values.tolist(), [['I1', 'reminder']])
            s.loc[hit, 'action_type'] = 'phone_call'
            s.to_csv(source_paths(tmp)['schedule'], index=False)
            with self.assertRaisesRegex(ValueError, 'not allowed for its rule'):
                load_f_outputs(tmp)

    def test_other_shape_errors(self):
        s = self.raw
        i3 = s.index[s['rule'].eq('I3')][0]
        cases = {
            'Duplicate': pd.concat([s, s.iloc[[0]]]),
            'I3 needs exactly one call': pd.concat([s, s.loc[[i3]].assign(scheduled_at='2017-08-30',
                                                                           message_no=2)]),
            'on or after arrival': s.assign(scheduled_at=s['scheduled_at'].where(
                s.index != i3, '2017-12-31')),
            'has no scheduled action': s.drop(index=i3),
            'I5 / G2': pd.concat([s, s.iloc[[0]].assign(
                booking_id=int(self.queue.loc[self.queue['rule'].eq('I5'), 'booking_id'].iloc[0]),
                rule='I5')]),
        }
        for message, frame in cases.items():
            with self.subTest(message), self.assertRaises(ValueError):
                self.check(frame.reset_index(drop=True))


class TestFEligibilityMissingStatus(unittest.TestCase):
    """R2-03: missing or null booking status is 'unknown' and not eligible, never assumed active."""

    @classmethod
    def setUpClass(cls):
        b = pd.read_csv(source_paths(REPO)['bookings'],
                        parse_dates=['booking_date', 'arrival_date', 'reservation_status_date'])
        cls.row = b[b['booking_id'].eq(22273)]  # cancelled 2016-07-20, arrives 2017-01-09

    def flags(self, frame):
        return fm.eligibility(frame, '2017-01-08')[['active_at_decision', 'eligible_known_checks',
                                                     'ineligible_reason']].iloc[0].tolist()

    def test_full_status_is_cancelled(self):
        self.assertEqual(self.flags(self.row), [False, False, 'already cancelled'])

    def test_missing_columns_are_unknown(self):
        cols = ['reservation_status', 'reservation_status_date']
        self.assertEqual(self.flags(self.row.drop(columns=cols)), ['unknown', False, 'status unknown'])
        for col in cols:
            self.assertEqual(self.flags(self.row.drop(columns=[col])), ['unknown', False, 'status unknown'])

    def test_null_values_are_unknown(self):
        self.assertEqual(self.flags(self.row.assign(reservation_status=None)),
                         ['unknown', False, 'status unknown'])
        self.assertEqual(self.flags(self.row.assign(reservation_status_date=pd.NaT)),
                         ['unknown', False, 'status unknown'])

    def test_complete_status_keeps_plain_booleans(self):
        out = fm.eligibility(self.row.assign(reservation_status='Check-Out'), '2017-01-08')
        self.assertEqual(out['active_at_decision'].dtype, bool)
        self.assertTrue(out['eligible_known_checks'].iloc[0])


if __name__ == '__main__':
    unittest.main()
