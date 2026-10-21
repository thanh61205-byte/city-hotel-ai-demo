"""Astra #1: every local check runs before the n8n webhook is called."""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from dashboard import n8n_client, state_store as ss
from _store import SQLITE_ONLY, store_target  # tests/ is on sys.path under discover

OK = {'consent_confirmed': True, 'no_open_conflict': True, 'booking_active': True}
URL = 'http://localhost:5678/webhook-test/g2-hotel-approval-demo'
SCHED = [
    {'run_id': 'r', 'booking_id': 900, 'decision_at': '2017-01-01', 'scheduled_at': '2017-01-05',
     'rule': 'I2', 'action_type': 'reminder', 'message_no': 1},
    {'run_id': 'r', 'booking_id': 900, 'decision_at': '2017-01-01', 'scheduled_at': '2017-01-21',
     'rule': 'I2', 'action_type': 'reminder', 'message_no': 2},
]


def reply(accepted: bool):
    resp = mock.Mock()
    resp.json.return_value = {'accepted': accepted, 'status': 'DEMO_ACCEPTED_NO_ACTION'
                              if accepted else 'BLOCKED_BY_GUARD'}
    return mock.Mock(return_value=resp)


class TestSendAction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = ss.connect(store_target(self.tmp.name, 't'), run_id='r')
        self.ids, _ = ss.create_actions_from_schedule(self.con, SCHED)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def send(self, aid, as_of, post, outcome='no_response', precheck=OK):
        return n8n_client.send_action(self.con, aid, webhook=URL, as_of=as_of, outcome=outcome,
                                      precheck=precheck, post=post)

    def test_accepted_is_logged_as_n8n(self):
        post = reply(True)
        body = self.send(self.ids[0], date(2017, 1, 5), post)
        post.assert_called_once()
        payload = post.call_args.kwargs['json']
        self.assertEqual((payload['already_executed'], payload['guest_response']), (False, None))
        self.assertEqual(ss.get_actions(self.con, 900)[-1]['executed_by'], 'n8n')
        self.assertIn('intervention_id', body)

    def test_not_called_after_guest_responded(self):
        self.send(self.ids[0], date(2017, 1, 5), reply(True), outcome='confirmed')
        post = reply(True)
        with self.assertRaisesRegex(ValueError, 'Stopped'):
            self.send(self.ids[1], date(2017, 1, 21), post)
        post.assert_not_called()

    def test_not_called_for_an_executed_action(self):
        self.send(self.ids[0], date(2017, 1, 5), reply(True))
        post = reply(True)
        with self.assertRaisesRegex(ValueError, 'already been executed'):
            self.send(self.ids[0], date(2017, 1, 6), post)
        post.assert_not_called()

    def test_not_called_when_pending_not_due_or_unchecked(self):
        pending = ss.create_action(self.con, 'resale', stay_date='2017-03-01', room_type='A',
                                   decision_at='2017-01-01')
        post = reply(True)
        with self.assertRaises(PermissionError):
            self.send(pending, date(2017, 1, 5), post, outcome='completed')
        with self.assertRaisesRegex(ValueError, 'Not due'):
            self.send(self.ids[0], date(2017, 1, 4), post)
        with self.assertRaises(PermissionError):
            self.send(self.ids[0], date(2017, 1, 5), post, precheck={})
        post.assert_not_called()

    def test_refused_by_n8n_is_not_logged(self):
        self.send(self.ids[0], date(2017, 1, 5), reply(False))
        self.assertIsNone(ss.get_actions(self.con, 900)[-1]['intervention_id'])


if __name__ == '__main__':
    unittest.main()
