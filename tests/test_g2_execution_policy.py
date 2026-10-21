"""Regressions for the F -> G1 -> G2 review: policy must hold at execution too."""
import copy
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock

from dashboard import n8n_client, state_store as ss
from dashboard.data_contract import load_outputs
from _store import SQLITE_ONLY, store_target  # tests/ is on sys.path under discover

OK = dict.fromkeys(ss.PRECHECKS, True)


class TestExecutionPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue, cls.schedule, cls.meta = load_outputs()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = store_target(self.tmp.name, 'policy')
        self.con = ss.connect(self.path, run_id=self.meta['policy_key'],
                              call_capacity=self.meta['call_capacity_per_day'],
                              execution_policy=self.meta['execution_policy'])

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def imported_call(self, bid):
        rows = self.schedule.loc[self.schedule.booking_id.eq(bid)].to_dict('records')
        arrival = self.queue.set_index('booking_id').at[bid, 'arrival_date']
        ids, _ = ss.create_actions_from_schedule(self.con, rows, arrival_date=arrival)
        return ids[0]

    def assert_blocked_without_dispatch(self, aid, day, reason):
        with self.assertRaisesRegex((ValueError, PermissionError), reason):
            ss.record_execution(self.con, aid, as_of=day, outcome='no_response', precheck=OK)
        post = Mock()
        with self.assertRaisesRegex((ValueError, PermissionError), reason):
            n8n_client.send_action(self.con, aid, webhook='https://invalid.test',
                                  as_of=day, outcome='no_response', precheck=OK, post=post)
        post.assert_not_called()
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM interventions').fetchone()[0], 0)

    def test_actual_g1_call_past_window_is_blocked_for_staff_and_n8n(self):
        self.assert_blocked_without_dispatch(self.imported_call(22719), date(2017, 1, 9), 'window')

    def test_actual_g1_call_with_only_seven_days_to_arrival_is_blocked(self):
        self.assert_blocked_without_dispatch(self.imported_call(64565), date(2017, 1, 6), '8 days')

    def test_window_boundary_and_lead_boundary_are_inclusive(self):
        # Last allowed offset is 6 (Jan 7); minimum arrival lead is exactly 8.
        aid = ss.create_action(self.con, 'phone_call', booking_id=1, decision_at='2017-01-01',
                               known_from='2017-01-01', arrival_date='2017-01-15')
        ss.record_execution(self.con, aid, as_of='2017-01-07', outcome='no_response', precheck=OK)

    def test_day_seven_offset_is_expired(self):
        self.assert_blocked_without_dispatch(self.imported_call(22719), date(2017, 1, 8), 'window')

    def test_manual_creation_cannot_restart_original_window(self):
        with self.assertRaisesRegex(ValueError, 'window'):
            ss.create_action(self.con, 'phone_call', booking_id=22719, decision_at='2017-01-09',
                             known_from='2017-01-01', arrival_date='2017-01-21')
        with self.assertRaisesRegex(ValueError, 'original F score'):
            ss.create_action(self.con, 'phone_call', booking_id=22719, decision_at='2017-01-09',
                             arrival_date='2017-01-21')

    def test_manual_call_keeps_original_anchor_at_execution(self):
        aid = ss.create_action(self.con, 'phone_call', booking_id=22719, decision_at='2017-01-05',
                               known_from='2017-01-01', arrival_date='2017-01-21')
        self.assert_blocked_without_dispatch(aid, date(2017, 1, 8), 'window')

    def test_overbooking_cannot_be_created(self):
        with self.assertRaisesRegex(PermissionError, 'disabled'):
            ss.create_action(self.con, 'overbooking', stay_date='2017-03-01', room_type='A',
                             decision_at='2017-01-01')

    def test_direct_approved_overbooking_is_refused_by_the_database(self):
        # An external writer cannot insert an approved G3 action (so nothing can execute one).
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'G3 overbooking is disabled by policy'):
            self.con.execute("""INSERT INTO action_plan
                (stay_date, room_type, action_type, rule_id, decision_at, needs_approval,
                 approval_status, approved_by, approved_at, created_at)
                VALUES ('2017-03-01','A','overbooking','G3','2017-01-01',1,
                        'approved','RM','2026-01-01','2026-01-01')""")
        self.con.rollback()
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM action_plan").fetchone()[0], 0)

    def test_policy_persists_and_cannot_be_changed(self):
        self.con.close()
        self.con = ss.connect(self.path, run_id=self.meta['policy_key'])
        self.assertEqual(ss.execution_policy(self.con),
                         ss.normalize_execution_policy(self.meta['execution_policy']))
        changed = copy.deepcopy(self.meta['execution_policy'])
        changed['call_schedule']['window_days'] = 14
        with self.assertRaisesRegex(ValueError, 'fixed per store'):
            ss.connect(self.path, execution_policy=changed)
        self.assert_blocked_without_dispatch(self.imported_call(22719), date(2017, 1, 9), 'window')

    def test_new_g1_store_requires_policy(self):
        with self.assertRaisesRegex(ValueError, 'requires its execution policy'):
            ss.connect(store_target(self.tmp.name, 'missing'), run_id=self.meta['policy_key'])

    def test_payload_carries_stored_policy_and_original_dates(self):
        aid = self.imported_call(22719)
        response = Mock()
        response.json.return_value = {'accepted': True}
        post = Mock(return_value=response)
        result = n8n_client.send_action(self.con, aid, webhook='https://invalid.test',
                                       as_of=date(2017, 1, 1), outcome='no_response',
                                       precheck=OK, post=post)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['execution_policy'], ss.execution_policy(self.con))
        self.assertEqual(payload['known_from'], '2017-01-01')
        self.assertEqual(payload['arrival_date'], '2017-01-21')
        self.assertEqual(payload['source_run_id'], self.meta['policy_key'])
        self.assertIn('intervention_id', result)
