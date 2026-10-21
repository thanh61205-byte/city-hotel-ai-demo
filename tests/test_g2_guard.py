import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from dashboard import state_store as ss
from _store import SQLITE_ONLY, store_target  # tests/ is on sys.path under discover

OK = {'consent_confirmed': True, 'no_open_conflict': True, 'booking_active': True}
I6_OK = {'risk_tier': 'high', 'deposit_type': 'No Deposit', 'note': 'deposit policy §4.2 (demo)'}
D0 ='2017-01-01'          # decision date used by the helpers
DAY = date(2017, 1, 8)     # a replay date after it
SCHED_I2 = [  # shape of artifacts/modeling/action_schedule_2017.csv rows
    {'run_id': 'r', 'booking_id': 900, 'decision_at': '2017-01-01', 'scheduled_at': '2017-01-05',
     'rule': 'I2', 'action_type': 'reminder', 'schedule_reason': 'milestone 30', 'message_no': 1},
    {'run_id': 'r', 'booking_id': 900, 'decision_at': '2017-01-01', 'scheduled_at': '2017-01-21',
     'rule': 'I2', 'action_type': 'reminder', 'schedule_reason': 'milestone 14', 'message_no': 2},
]


class GuardCase(unittest.TestCase):
    run_id = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = store_target(self.tmp.name, 'test')
        self.con = ss.connect(self.db, run_id=self.run_id)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def call(self, booking_id=22005, decision_at=D0, **kw):
        return ss.create_action(self.con, 'phone_call', booking_id=booking_id,
                                decision_at=decision_at, **kw)

    def reminder(self, booking_id=22005, decision_at=D0, **kw):
        return ss.create_action(self.con, 'reminder', booking_id=booking_id,
                                decision_at=decision_at, **kw)

    def group(self, action='resale', room='A'):
        return ss.create_action(self.con, action, stay_date='2017-03-01', room_type=room,
                                rule_id='G2', decision_at=D0)

    def run_(self, aid, as_of=DAY, outcome='no_response', precheck=OK, **kw):
        return ss.record_execution(self.con, aid, as_of=as_of, outcome=outcome,
                                   precheck=precheck, **kw)


class TestApprovalGuard(GuardCase):
    """decision_table_draft.md §6.4 plus the bypasses found in review."""

    def test_sensitive_action_cannot_execute_before_approval(self):
        aid = self.group('price_adjustment')
        self.assertEqual(ss.get_action(self.con, aid)['approval_status'], 'pending')
        with self.assertRaises(PermissionError):
            self.run_(aid, outcome='completed')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'approval'):
            self.con.execute("""INSERT INTO interventions (action_id, channel, as_of, executed_at,
                executed_by, outcome, precheck)
                VALUES (?, 'task', '2017-01-08', 'x', 'staff', 'completed', '{}')""", (aid,))

    def test_approved_passes_once(self):
        aid = self.group('capacity_allocation')
        ss.decide_action(self.con, aid, 'Revenue Manager', True)
        self.assertGreater(self.run_(aid, outcome='completed'), 0)
        with self.assertRaises(ValueError):
            self.run_(aid, outcome='completed')

    def test_rejected_stays_blocked(self):
        aid = self.group('resale')
        ss.decide_action(self.con, aid, 'Revenue Manager', False)
        with self.assertRaises(PermissionError):
            self.run_(aid, outcome='completed')

    def test_auto_with_needs_approval_impossible(self):
        for needs in (1, 0):
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'CHECK'):
                self.con.execute("""INSERT INTO action_plan (stay_date, room_type, action_type,
                    rule_id, decision_at, needs_approval, approval_status, created_at)
                    VALUES ('2017-03-01', 'A', 'resale', 'X', '2017-01-01', ?, 'auto', '2026')""",
                                 (needs,))

    def test_intervention_cannot_be_repointed_or_deleted(self):
        ok = self.call()
        self.run_(ok)
        bad = self.group('price_adjustment')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
            self.con.execute('UPDATE interventions SET action_id = ? WHERE action_id = ?', (bad, ok))
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
            self.con.execute('DELETE FROM interventions')

    def test_decision_is_final_in_database(self):
        aid = self.group('resale')
        ss.decide_action(self.con, aid, 'RM', False)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'final'):
            self.con.execute("UPDATE action_plan SET approval_status = 'approved' WHERE action_id = ?", (aid,))
        with self.assertRaises(ValueError):
            ss.decide_action(self.con, aid, 'RM', True)

    def test_pending_cannot_become_auto_or_drop_approval(self):
        aid = self.group('resale')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'final'):
            self.con.execute("""UPDATE action_plan SET needs_approval = 0, approval_status = 'auto'
                                WHERE action_id = ?""", (aid,))

    def test_approved_action_cannot_be_swapped(self):
        aid = self.group('capacity_allocation')
        ss.decide_action(self.con, aid, 'RM', True)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable'):
            self.con.execute("UPDATE action_plan SET action_type = 'overbooking' WHERE action_id = ?", (aid,))

    def test_approval_time_cannot_precede_creation(self):
        aid = self.group('resale')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'CHECK'):
            self.con.execute("""UPDATE action_plan SET approval_status = 'approved', approved_by = 'x',
                approved_at = '2000-01-01' WHERE action_id = ?""", (aid,))

    def test_reviewer_required(self):
        aid = self.group('resale')
        with self.assertRaises(ValueError):
            ss.decide_action(self.con, aid, '   ', True)


class TestActionLevelAndPolicy(GuardCase):
    def test_date_actions_need_date_and_room(self):
        with self.assertRaises(ValueError):
            ss.create_action(self.con, 'capacity_allocation', booking_id=22005, decision_at=D0)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'CHECK'):
            self.con.execute("""INSERT INTO action_plan (booking_id, action_type, rule_id,
                decision_at, needs_approval, approval_status, created_at)
                VALUES (22005, 'resale', 'X', '2017-01-01', 1, 'pending', '2026')""")

    def test_booking_actions_need_booking(self):
        with self.assertRaises(ValueError):
            ss.create_action(self.con, 'reminder', stay_date='2017-03-01', room_type='A',
                             decision_at=D0)

    def test_deposit_request_off_by_default(self):
        with self.assertRaises(PermissionError):
            ss.create_action(self.con, 'request_deposit', booking_id=22005, decision_at=D0,
                             **I6_OK)
        aid = ss.create_action(self.con, 'request_deposit', booking_id=22005, decision_at=D0,
                               deposit_policy_enabled=True, **I6_OK)
        a = ss.get_action(self.con, aid)
        self.assertEqual((a['approval_status'], a['rule_id']), ('pending', 'I6'))

    def test_deposit_request_needs_i6_conditions(self):
        """Astra R2-06: booking 32479 (low tier, Refundable) must not get an I6 request."""
        def make(**kw):
            return ss.create_action(self.con, 'request_deposit', booking_id=32479,
                                    decision_at=D0, deposit_policy_enabled=True, **kw)
        with self.assertRaisesRegex(ValueError, 'high-tier'):
            make(risk_tier='low', deposit_type='No Deposit', note='policy 4.2')
        with self.assertRaisesRegex(ValueError, 'No Deposit'):
            make(risk_tier='high', deposit_type='Refundable', note='policy 4.2')
        with self.assertRaisesRegex(ValueError, 'policy reference'):
            make(risk_tier='high', deposit_type='No Deposit', note='  ')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'CHECK'):
            self.con.execute("INSERT INTO action_plan (booking_id, action_type, rule_id, decision_at, "
                             "needs_approval, approval_status, created_at) VALUES "
                             "(1, 'request_deposit', 'I6', '2017-01-01', 1, 'pending', '2026')")

    def test_no_second_open_action_of_same_type(self):
        self.call()
        with self.assertRaisesRegex(ValueError, 'open phone_call'):
            self.call()
        self.group('resale', 'A')
        with self.assertRaisesRegex(ValueError, 'open resale'):
            self.group('resale', 'A')
        self.group('resale', 'D')  # another room type is a different target

    def test_max_two_call_attempts(self):
        self.run_(self.call(), as_of=date(2017, 1, 2))
        self.run_(self.call(decision_at='2017-01-03'), as_of=date(2017, 1, 3))
        with self.assertRaisesRegex(ValueError, 'max 2'):
            self.call(decision_at='2017-01-04')


class TestTimeline(GuardCase):
    """Astra #2: manual actions must respect the booking's known dates."""

    def test_decision_date_required(self):
        with self.assertRaisesRegex(ValueError, 'decision_at'):
            ss.create_action(self.con, 'phone_call', booking_id=1, decision_at=None)

    def test_cannot_decide_before_f_knew_the_booking(self):
        # Booking 29736: created 2017-06-03, decided by F on 2017-06-04, arrives 2017-06-06.
        with self.assertRaisesRegex(ValueError, 'Not known yet'):
            self.call(29736, decision_at='2017-01-01', known_from='2017-06-04',
                      arrival_date='2017-06-06')
        aid = self.call(29736, decision_at='2017-06-04', known_from='2017-06-04',
                        arrival_date='2017-06-06')
        with self.assertRaisesRegex(ValueError, 'before the decision date'):
            self.run_(aid, as_of=date(2017, 1, 1))
        self.run_(aid, as_of=date(2017, 6, 5))

    def test_no_contact_on_or_after_arrival(self):
        with self.assertRaisesRegex(ValueError, 'Too late'):
            self.call(decision_at='2017-06-06', arrival_date='2017-06-06')
        aid = self.call(decision_at='2017-06-04', arrival_date='2017-06-06')
        with self.assertRaisesRegex(ValueError, 'Too late'):
            self.run_(aid, as_of=date(2017, 6, 6))

    def test_date_action_window_is_the_stay_date(self):
        aid = self.group('capacity_allocation')  # stay date 2017-03-01
        ss.decide_action(self.con, aid, 'RM', True)
        with self.assertRaisesRegex(ValueError, 'Too late'):
            self.run_(aid, as_of=date(2017, 3, 1), outcome='completed')
        with self.assertRaisesRegex(ValueError, 'Not known yet'):
            ss.create_action(self.con, 'resale', stay_date='2017-03-01', room_type='B',
                             decision_at='2017-01-01', known_from='2017-02-01')


class TestSameDayRule(GuardCase):
    """Astra #3: never an automated message on the same day as a call; the call goes first."""

    def test_reminder_then_call_same_day(self):
        r = self.reminder()
        self.run_(r, as_of=date(2017, 1, 2))
        c = self.call(decision_at='2017-01-02')
        with self.assertRaisesRegex(ValueError, 'already sent today'):
            self.run_(c, as_of=date(2017, 1, 2))
        self.run_(c, as_of=date(2017, 1, 3))  # next day is fine

    def test_call_then_reminder_same_day(self):
        c = self.call()
        self.run_(c, as_of=date(2017, 1, 2))
        r = self.reminder(decision_at='2017-01-02')
        with self.assertRaisesRegex(ValueError, 'call was made'):
            self.run_(r, as_of=date(2017, 1, 2))
        self.run_(r, as_of=date(2017, 1, 3))

    def test_open_due_call_goes_first(self):
        r = self.reminder()
        self.call()
        with self.assertRaisesRegex(ValueError, 'call goes first'):
            self.run_(r, as_of=date(2017, 1, 2))

    def test_other_bookings_not_affected(self):
        self.run_(self.call(1), as_of=date(2017, 1, 2))
        self.run_(self.reminder(2), as_of=date(2017, 1, 2))


class TestExecutionChecks(GuardCase):
    def test_guest_action_needs_execution_time_checks(self):
        aid = self.call()
        for missing in ss.PRECHECKS:
            with self.assertRaisesRegex(PermissionError, missing):
                self.run_(aid, precheck={**OK, missing: False})
        self.run_(aid)

    def test_check_execution_writes_nothing(self):
        aid = self.call()
        ss.check_execution(self.con, aid, as_of=DAY, outcome='no_response', precheck=OK)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM interventions').fetchone()[0], 0)

    def test_not_due_before_scheduled_date(self):
        ids, _ = ss.create_actions_from_schedule(self.con, SCHED_I2)
        with self.assertRaisesRegex(ValueError, 'Not due'):
            self.run_(ids[0], as_of=date(2017, 1, 4))
        self.run_(ids[0], as_of=date(2017, 1, 5))

    def test_guest_response_stops_follow_ups(self):
        ids, _ = ss.create_actions_from_schedule(self.con, SCHED_I2)
        self.run_(ids[0], as_of=date(2017, 1, 5), outcome='confirmed')
        with self.assertRaisesRegex(ValueError, 'Stopped'):
            self.run_(ids[1], as_of=date(2017, 1, 21))
        with self.assertRaisesRegex(ValueError, 'stopped'):
            self.call(900, decision_at='2017-01-21')

    def test_offer_change_only_after_call_with_change_request(self):
        with self.assertRaisesRegex(ValueError, 'I4'):
            ss.create_action(self.con, 'offer_change', booking_id=22005, decision_at=D0)
        self.run_(self.call(), outcome='requested_change')
        aid = ss.create_action(self.con, 'offer_change', booking_id=22005, decision_at='2017-01-08')
        self.assertEqual(ss.get_action(self.con, aid)['approval_status'], 'pending')
        with self.assertRaisesRegex(ValueError, 'max 1'):
            ss.create_action(self.con, 'offer_change', booking_id=22005, decision_at='2017-01-08')

    def test_cancelled_booking_blocks_everything(self):
        self.run_(self.call(), outcome='cancelled')
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            self.reminder(decision_at='2017-01-08')

    def test_business_date_is_logged(self):
        aid = self.call()
        self.run_(aid, as_of=date(2017, 1, 3))
        self.assertEqual(ss.get_actions(self.con, 22005)[0]['executed_on'], '2017-01-03')


class TestScheduleImport(GuardCase):
    def test_schedule_import_is_idempotent(self):
        ids, existing = ss.create_actions_from_schedule(self.con, SCHED_I2)
        self.assertEqual((len(ids), existing), (2, 0))
        ids2, existing2 = ss.create_actions_from_schedule(self.con, SCHED_I2)
        self.assertEqual((ids2, existing2), ([], 2))
        a = ss.get_action(self.con, ids[0])
        self.assertEqual((a['rule_id'], a['approval_status'], a['scheduled_at']), ('I2', 'auto', '2017-01-05'))

    def test_schedule_rows_must_be_one_booking(self):
        rows = [SCHED_I2[0], {**SCHED_I2[1], 'booking_id': 901}]
        with self.assertRaises(ValueError):
            ss.create_actions_from_schedule(self.con, rows)

    def test_schedule_before_decision_rejected_by_db(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'CHECK'):
            ss.create_actions_from_schedule(self.con, [{**SCHED_I2[0], 'scheduled_at': '2016-12-25'}])

    def test_schedule_respects_arrival(self):
        with self.assertRaisesRegex(ValueError, 'Too late'):
            ss.create_actions_from_schedule(self.con, SCHED_I2, arrival_date='2017-01-01')

    def test_p3_turnaround_kpi(self):
        aid = self.group('resale')
        self.group('resale', 'D')
        ss.decide_action(self.con, aid, 'RM', True)
        k = ss.approval_kpis(self.con)
        self.assertEqual((k['needs_approval'], k['approved'], k['pending']), (2, 1, 1))
        self.assertIsNotNone(k['median_turnaround_h'])


class TestRunBinding(GuardCase):
    """Astra #6: a store belongs to one F run; another run's schedule is a conflict."""
    run_id = 'first_run'

    def test_store_refuses_another_run(self):
        self.con.close()
        with self.assertRaisesRegex(ValueError, 'first_run'):
            ss.connect(self.db, run_id='second_run')
        self.con = ss.connect(self.db, run_id='first_run')

    def test_other_run_schedule_is_a_conflict_not_a_skip(self):
        row = {**SCHED_I2[0], 'run_id': 'first_run', 'rule': 'I1'}
        ss.create_actions_from_schedule(self.con, [row])
        with self.assertRaisesRegex(ValueError, 'run'):
            ss.create_actions_from_schedule(self.con, [{**row, 'run_id': 'second_run', 'rule': 'I2'}])
        with self.assertRaisesRegex(ValueError, 'Conflict'):
            ss.create_actions_from_schedule(self.con, [{**row, 'rule': 'I2'}])
        a = ss.get_actions(self.con, 900)
        self.assertEqual((len(a), a[0]['source_run_id'], a[0]['rule_id']), (1, 'first_run', 'I1'))

    def test_manual_action_takes_the_store_run(self):
        aid = self.call()
        self.assertEqual(ss.get_action(self.con, aid)['source_run_id'], 'first_run')

    @SQLITE_ONLY
    def test_old_store_without_metadata_is_refused(self):
        old = Path(self.tmp.name) / 'old.sqlite'
        c = sqlite3.connect(old)
        c.execute('CREATE TABLE action_plan (action_id INTEGER PRIMARY KEY)')
        c.close()
        with self.assertRaisesRegex(ValueError, 'older G2 version'):
            ss.connect(old, run_id='first_run')


if __name__ == '__main__':
    unittest.main()
