"""Third-review regressions: offer lifetime, cross-log ordering, dated schedule contract."""
import hashlib
import os
import shutil
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest import TestCase, mock

import pandas as pd

from dashboard import data_contract as dc, n8n_client, state_store as ss
from _store import SQLITE_ONLY, store_target  # tests/ is on sys.path under discover
from test_g2_guard import GuardCase, OK
from test_g2_n8n_client import reply
from test_g2_app import AppCase


class TestOfferLifetime(GuardCase):
    def pending_offer(self):
        call = self.call(arrival_date='2017-01-21')
        self.run_(call, as_of='2017-01-02', outcome='requested_change')
        offer = ss.create_action(self.con, 'offer_change', booking_id=22005,
                                 decision_at='2017-01-03', arrival_date='2017-01-21')
        ss.decide_action(self.con, offer, 'RM', True)
        return call, offer

    def test_confirmed_after_approval_blocks_execution_and_webhook(self):
        call, offer = self.pending_offer()
        ss.record_response(self.con, call, response='confirmed', response_at='2017-01-04')
        with self.assertRaisesRegex(ValueError, 'I4'):
            self.run_(offer, as_of='2017-01-05')
        post = reply(True)
        with self.assertRaisesRegex(ValueError, 'I4'):
            n8n_client.send_action(self.con, offer, webhook='http://unused.test',
                                  as_of=date(2017, 1, 5), outcome='no_response', precheck=OK,
                                  post=post)
        post.assert_not_called()
        self.assertIsNone(self.con.execute('SELECT 1 FROM interventions WHERE action_id = ?',
                                          (offer,)).fetchone())

    def test_valid_offer_still_executes_with_call_evidence_in_payload(self):
        _, offer = self.pending_offer()
        post = reply(True)
        result = n8n_client.send_action(self.con, offer, webhook='http://unused.test',
                                       as_of=date(2017, 1, 3), outcome='confirmed', precheck=OK,
                                       post=post)
        self.assertTrue(result['accepted'])
        body = post.call_args.kwargs['json']
        self.assertEqual((body['guest_response'], body['guest_response_action_type']),
                         ('requested_change', 'phone_call'))
        self.assertEqual(ss.booking_stop_outcome(self.con, 22005), 'confirmed')


class TestUnifiedResponseOrder(GuardCase):
    def test_same_second_late_answer_then_offer_confirmation_and_reopen(self):
        with mock.patch.object(ss, '_now', return_value='2026-10-19T16:00:00+00:00'):
            call = self.call()
            self.run_(call, as_of='2017-01-02')
            ss.record_response(self.con, call, response='requested_change', response_at='2017-01-03')
            offer = ss.create_action(self.con, 'offer_change', booking_id=22005,
                                     decision_at='2017-01-03')
            ss.decide_action(self.con, offer, 'RM', True)
            self.run_(offer, as_of='2017-01-03', outcome='confirmed')
            self.assertEqual(ss.booking_stop_outcome(self.con, 22005), 'confirmed')
            # Reverse source order: a late answer recorded AFTER that contact wins.
            ss.record_response(self.con, call, response='requested_change', response_at='2017-01-03')
            self.assertEqual(ss.booking_stop_outcome(self.con, 22005), 'requested_change')
        self.con.close()
        self.con = ss.connect(self.db)
        seen = ss.guest_responses(self.con, 22005)
        self.assertEqual([r['response'] for r in seen],
                         ['requested_change', 'confirmed', 'requested_change'])
        self.assertEqual([r['seq'] for r in seen], sorted(r['seq'] for r in seen))
        self.assertIsNone(ss.booking_stop_outcome(self.con, 22005, '2017-01-02'))

    def test_cancelled_remains_terminal_and_event_order_is_immutable(self):
        call = self.call()
        self.run_(call, as_of='2017-01-02')
        ss.record_response(self.con, call, response='cancelled', response_at='2017-01-03')
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            ss.record_response(self.con, call, response='confirmed', response_at='2017-01-03')
        for sql in ('UPDATE response_events SET event_id = event_id + 10',
                    'DELETE FROM response_events'):
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
                self.con.execute(sql)
            self.con.rollback()
        self.assertEqual(ss.booking_stop_outcome(self.con, 22005), 'cancelled')

    def test_event_sequence_rolls_back_with_contact(self):
        call = self.call()
        self.con.execute('SAVEPOINT fixture')
        self.con.execute("""INSERT INTO interventions
            (action_id, channel, as_of, executed_at, executed_by, outcome, precheck)
            VALUES (?, 'phone', '2017-01-02', 'x', 'staff', 'confirmed', '{}')""", (call,))
        self.assertEqual(ss.booking_stop_outcome(self.con, 22005), 'confirmed')
        self.con.execute('ROLLBACK TO fixture')
        self.con.execute('RELEASE fixture')
        self.assertIsNone(ss.booking_stop_outcome(self.con, 22005))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM response_events').fetchone()[0], 0)


class TestScheduleMilestones(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue, cls.schedule, _ = dc.load_f_outputs()

    def test_reversed_i1_is_refused_by_full_loader(self):
        with tempfile.TemporaryDirectory() as td:
            for key, src in dc.source_paths().items():
                dst = dc.source_paths(td)[key]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            path = dc.source_paths(td)['schedule']
            s = pd.read_csv(path)
            hit = s.booking_id.eq(22288)
            s.loc[hit, 'action_type'] = s.loc[hit, 'action_type'].map(
                {'confirm_email': 'reminder', 'reminder': 'confirm_email'})
            s.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, 'decision time'):
                dc.load_f_outputs(td)

    def test_wrong_day_or_missing_required_reminder_is_refused(self):
        for rule in ['I1', 'I2']:
            idx = self.schedule.index[self.schedule.rule.eq(rule)
                                      & self.schedule.action_type.eq('reminder')][0]
            bad = self.schedule.copy()
            bad.loc[idx, 'scheduled_at'] += timedelta(days=1)
            with self.subTest(rule=rule), self.assertRaisesRegex(ValueError, 'milestones'):
                dc.validate_schedule(bad, self.queue)
        hit = self.schedule.booking_id.eq(22288) & self.schedule.action_type.eq('reminder')
        with self.assertRaisesRegex(ValueError, 'milestones'):
            dc.validate_schedule(self.schedule[~hit], self.queue)

    def test_boundary_dates_and_no_longer_due_i1_reminder(self):
        for rule, lead, offsets in [('I1', 7, [0]), ('I1', 8, [0, 1]),
                                    ('I2', 8, [0]), ('I2', 14, [0]),
                                    ('I2', 15, [0, 1]), ('I2', 30, [0, 16]),
                                    ('I2', 31, [1, 17])]:
            q = pd.DataFrame([{'booking_id': 1, 'run_id': 'test', 'rule': rule,
                               'score_time': date(2017, 1, 1),
                               'arrival_date': date(2017, 1, 1) + timedelta(days=lead)}])
            s = pd.DataFrame([{'run_id': 'test', 'booking_id': 1, 'rule': rule,
                               'decision_at': '2017-01-01',
                               'scheduled_at': date(2017, 1, 1) + timedelta(days=offset),
                               'action_type': 'confirm_email' if rule == 'I1' and n == 1 else 'reminder',
                               'message_no': n, 'schedule_reason': 'fixture'}
                              for n, offset in enumerate(offsets, 1)])
            with self.subTest(rule=rule, lead=lead):
                self.assertEqual(len(dc.validate_schedule(s, q)), len(offsets))


@SQLITE_ONLY
class TestLegacyLog(TestCase):
    def test_schema3_is_refused_without_changing_old_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'old.sqlite'
            con = sqlite3.connect(path)
            con.executescript("CREATE TABLE store_meta (key TEXT, value TEXT);"
                              "INSERT INTO store_meta VALUES ('schema_version', '3');")
            con.close()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'keep the old log'):
                ss.connect(path)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)


class TestOfferLifetimeUI(AppCase):
    def test_ui_refuses_offer_after_confirmation(self):
        _, _, meta = dc.load_outputs()
        con = ss.connect(os.environ['G2_DB_PATH'], run_id=meta['policy_key'],
                         call_capacity=meta['call_capacity_per_day'],
                         execution_policy=meta['execution_policy'])
        try:
            call = ss.create_action(con, 'phone_call', booking_id=22719, decision_at='2017-01-01',
                                    known_from='2017-01-01',
                                    arrival_date='2017-01-21')
            ss.record_execution(con, call, as_of='2017-01-02', outcome='requested_change', precheck=OK)
            offer = ss.create_action(con, 'offer_change', booking_id=22719,
                                     decision_at='2017-01-03', arrival_date='2017-01-21')
            ss.decide_action(con, offer, 'RM', True)
            ss.record_response(con, call, response='confirmed', response_at='2017-01-04')
            self.set_today(date(2017, 1, 5))
            at = self.page(self.app(), 'execution')
            next(x for x in at.selectbox if x.label == 'Open action').set_value(offer).run()
            for label in ['Guest has not opted out of contact', 'No other open action of this type',
                          'Booking still active in PMS (not cancelled / arrived)']:
                next(x for x in at.checkbox if x.label == label).set_value(True)
            next(x for x in at.button if x.label == 'Record execution (staff)').click().run()
            self.assertFalse(at.exception, [x.value for x in at.exception])
            self.assertTrue(any('I4' in x.value for x in at.error))
            self.assertIsNone(con.execute('SELECT 1 FROM interventions WHERE action_id = ?',
                                          (offer,)).fetchone())
        finally:
            con.close()
