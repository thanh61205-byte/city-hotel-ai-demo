"""Second review (R2): replay history, late responses and schedule rule mapping in the store."""
import sqlite3
import unittest
from datetime import date

from dashboard import state_store as ss
from test_g2_guard import D0, OK, SCHED_I2, GuardCase  # tests/ is on sys.path under discover


class TestReplayHistory(GuardCase):
    """R2-02: history is read as of the date checked; replay moves forward per booking."""

    def test_backdated_offer_after_future_change_request_is_refused(self):
        # Booking 22719: scored 2017-01-01, arrives 2017-01-21. The call on 20/01 asks to change.
        c = self.call(22719, arrival_date='2017-01-21')
        self.run_(c, as_of=date(2017, 1, 20), outcome='requested_change')
        with self.assertRaisesRegex(ValueError, 'Back-dated'):
            ss.create_action(self.con, 'offer_change', booking_id=22719, decision_at='2017-01-10',
                             arrival_date='2017-01-21')
        aid = ss.create_action(self.con, 'offer_change', booking_id=22719, decision_at='2017-01-20',
                               arrival_date='2017-01-21')
        self.assertEqual(ss.get_action(self.con, aid)['rule_id'], 'I4')

    def test_history_is_read_as_of_the_date(self):
        c = self.call()
        self.run_(c, as_of=date(2017, 1, 20), outcome='confirmed')
        self.assertIsNone(ss.booking_stop_outcome(self.con, 22005, '2017-01-10'))
        self.assertEqual(ss.booking_stop_outcome(self.con, 22005, '2017-01-20'), 'confirmed')

    def test_other_bookings_keep_their_own_timeline(self):
        self.run_(self.call(), as_of=date(2017, 1, 20))
        r = ss.create_action(self.con, 'reminder', booking_id=1, decision_at=D0)
        self.run_(r, as_of=date(2017, 1, 15))

    def test_execution_cannot_be_backdated(self):
        ids, _ = ss.create_actions_from_schedule(self.con, SCHED_I2)
        self.run_(ids[1], as_of=date(2017, 1, 21))
        with self.assertRaisesRegex(ValueError, 'Back-dated'):
            self.run_(ids[0], as_of=date(2017, 1, 5))

    def test_date_cell_timeline_moves_forward(self):
        a = self.group('capacity_allocation')
        ss.decide_action(self.con, a, 'RM', True)
        self.run_(a, as_of=date(2017, 2, 1), outcome='completed')
        with self.assertRaisesRegex(ValueError, 'Back-dated'):
            ss.create_action(self.con, 'resale', stay_date='2017-03-01', room_type='A',
                             decision_at='2017-01-15')

    def test_offer_needs_latest_response_to_be_a_call_change_request(self):
        c = self.call()
        self.run_(c, as_of=date(2017, 1, 2), outcome='requested_change')
        ss.record_response(self.con, c, response='confirmed', response_at='2017-01-03')
        with self.assertRaisesRegex(ValueError, 'I4'):
            ss.create_action(self.con, 'offer_change', booking_id=22005, decision_at='2017-01-03')

    def test_change_request_from_a_reminder_does_not_open_i4(self):
        r = self.reminder()
        self.run_(r, as_of=date(2017, 1, 2), outcome='requested_change')
        with self.assertRaisesRegex(ValueError, 'I4'):
            ss.create_action(self.con, 'offer_change', booking_id=22005, decision_at='2017-01-02')


class TestLateResponse(GuardCase):
    """R2-05: a guest who answers after the contact stops the remaining follow-ups."""

    def test_late_confirmation_stops_next_reminder(self):
        ids, _ = ss.create_actions_from_schedule(self.con, SCHED_I2)
        self.run_(ids[0], as_of=date(2017, 1, 5))  # no_response when the reminder went out
        ss.record_response(self.con, ids[0], response='confirmed', response_at='2017-01-06')
        with self.assertRaisesRegex(ValueError, 'Stopped'):
            ss.check_execution(self.con, ids[1], as_of='2017-01-21', outcome='no_response',
                               precheck=OK)
        self.assertEqual(ss.get_actions(self.con, 900)[-1]['late_response'], 'confirmed 2017-01-06')

    def test_response_needs_an_executed_contact_before_it(self):
        ids, _ = ss.create_actions_from_schedule(self.con, SCHED_I2)
        with self.assertRaisesRegex(ValueError, 'not been executed'):
            ss.record_response(self.con, ids[0], response='confirmed', response_at='2017-01-06')
        self.run_(ids[0], as_of=date(2017, 1, 5))
        with self.assertRaisesRegex(ValueError, 'precede'):
            ss.record_response(self.con, ids[0], response='confirmed', response_at='2017-01-04')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'executed contact'):
            self.con.execute("INSERT INTO guest_responses (booking_id, action_id, response, "
                             "response_at, recorded_at) VALUES (900, ?, 'confirmed', '2017-01-04', 'x')",
                             (ids[0],))

    def test_response_only_for_guest_contacts(self):
        a = self.group('capacity_allocation')
        ss.decide_action(self.con, a, 'RM', True)
        self.run_(a, as_of=date(2017, 2, 1), outcome='completed')
        with self.assertRaisesRegex(ValueError, 'guest contact'):
            ss.record_response(self.con, a, response='confirmed', response_at='2017-02-02')

    def test_response_log_is_append_only(self):
        c = self.call()
        self.run_(c, as_of=date(2017, 1, 2))
        ss.record_response(self.con, c, response='requested_change', response_at='2017-01-03')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
            self.con.execute("UPDATE guest_responses SET response = 'confirmed'")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
            self.con.execute('DELETE FROM guest_responses')

    def test_late_change_request_after_call_allows_offer(self):
        c = self.call()
        self.run_(c, as_of=date(2017, 1, 2))
        ss.record_response(self.con, c, response='requested_change', response_at='2017-01-04')
        aid = ss.create_action(self.con, 'offer_change', booking_id=22005, decision_at='2017-01-04')
        self.assertEqual(ss.get_action(self.con, aid)['approval_status'], 'pending')

    def test_late_cancellation_stops_everything(self):
        c = self.call()
        self.run_(c, as_of=date(2017, 1, 2))
        ss.record_response(self.con, c, response='cancelled', response_at='2017-01-03')
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            ss.record_response(self.con, c, response='confirmed', response_at='2017-01-04')


class TestScheduleRuleMapping(GuardCase):
    """R2-04: a schedule row whose action does not belong to its rule is refused."""

    def test_wrong_action_for_rule_refused(self):
        bad = {**SCHED_I2[0], 'rule': 'I1', 'action_type': 'phone_call'}
        with self.assertRaisesRegex(ValueError, 'does not schedule'):
            ss.create_actions_from_schedule(self.con, [bad])
        with self.assertRaisesRegex(ValueError, 'does not schedule'):
            ss.create_actions_from_schedule(self.con, [{**SCHED_I2[0], 'rule': 'I5'}])
        self.assertEqual(ss.get_actions(self.con), [])


if __name__ == '__main__':
    unittest.main()
