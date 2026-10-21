"""Live check against a running n8n with the imported, published demo workflow.

Skipped unless G2_TEST_N8N_URL is set (production webhook URL, see n8n/README_VI.md):
    G2_TEST_N8N_URL=http://127.0.0.1:5690/webhook/g2-hotel-approval-demo python -m unittest tests.test_g2_n8n_live
"""
import os
import tempfile
import unittest
from datetime import date

from dashboard import n8n_client, state_store as ss
from _store import store_target  # tests/ is on sys.path under discover

URL = os.environ.get('G2_TEST_N8N_URL', '').strip()
OK = {'consent_confirmed': True, 'no_open_conflict': True, 'booking_active': True}
SCHED = [{'run_id': 'live', 'booking_id': 901, 'decision_at': '2017-01-01', 'scheduled_at': '2017-01-05',
          'rule': 'I2', 'action_type': 'reminder', 'message_no': 1}]


@unittest.skipUnless(URL, 'set G2_TEST_N8N_URL to a running n8n webhook')
class TestLiveN8n(unittest.TestCase):
    def setUp(self):
        import requests
        self.requests = requests
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.con = ss.connect(store_target(self.tmp.name, 'live'), run_id='live')

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def post_raw(self, action_id, as_of, **override):
        """Bypass the local checks on purpose: n8n's own guard must refuse."""
        payload = n8n_client.build_payload(self.con, ss.get_action(self.con, action_id), as_of=as_of,
                                           outcome='no_response', precheck=OK)
        payload.update(override)
        r = self.requests.post(URL, json=payload, timeout=10)
        r.raise_for_status()
        return r.json()

    def test_due_reminder_is_accepted_and_logged(self):
        aid = ss.create_actions_from_schedule(self.con, SCHED)[0][0]
        body = n8n_client.send_action(self.con, aid, webhook=URL, as_of=date(2017, 1, 5),
                                      outcome='no_response', precheck=OK)
        self.assertIs(body['accepted'], True, body)
        row = self.con.execute('SELECT executed_by FROM interventions WHERE action_id = ?', (aid,)).fetchone()
        self.assertEqual(row[0], 'n8n')

    def test_guard_refuses_what_the_store_would_block(self):
        aid = ss.create_actions_from_schedule(self.con, SCHED)[0][0]
        body = self.post_raw(aid, date(2017, 1, 4))
        self.assertIs(body['accepted'], False)
        self.assertTrue(any('not due yet' in r for r in body['reasons']), body['reasons'])
        pending = ss.create_action(self.con, 'price_adjustment', stay_date='2017-03-01', room_type='A',
                                   rule_id='G2', decision_at='2017-01-01')
        body = self.post_raw(pending, date(2017, 1, 2))
        self.assertIs(body['accepted'], False)
        self.assertTrue(any('not approved' in r for r in body['reasons']), body['reasons'])
        body = self.post_raw(pending, date(2017, 1, 2), action_type='overbooking', approval_status='approved')
        self.assertIn('G3 overbooking is disabled by policy', body['reasons'])
        self.assertIsNone(self.con.execute('SELECT 1 FROM interventions').fetchone())


if __name__ == '__main__':
    unittest.main()
