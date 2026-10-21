"""Run the n8n Code-node JavaScript with Node.js, outside n8n (no n8n server needed)."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "n8n"))
from build_workflow import OUT, workflow  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / 'n8n' / 'src'
NODE = shutil.which('node')
HARNESS = """
const fs = require('fs');
const [guardFile, dispatchFile, payload] = process.argv.slice(1);
const run = (file, item) => new Function('$input', fs.readFileSync(file, 'utf8'))(
  { first: () => ({ json: item }) })[0].json;
const guarded = run(guardFile, { body: JSON.parse(payload) });
console.log(JSON.stringify(run(dispatchFile, guarded)));
"""
BASE = {'mode': 'demo_no_guest_contact', 'as_of': '2017-01-08', 'action_id': 1,
        'booking_id': 22005, 'stay_date': None, 'room_type': None, 'action_type': 'phone_call',
        'rule_id': 'I3', 'scheduled_at': '2017-01-08', 'approval_status': 'auto',
        'needs_approval': False, 'outcome': 'no_response',
        'known_from': '2017-01-08', 'decision_at': '2017-01-08', 'arrival_date': '2017-02-01',
        'execution_policy': {'overbooking': {'enabled': False},
                             'call_schedule': {'start_day': 0, 'window_days': 7,
                                               'min_days_before_arrival': 8}},
        'already_executed': False, 'guest_response': None,
        'precheck': {'consent_confirmed': True, 'no_open_conflict': True, 'booking_active': True}}


@unittest.skipUnless(NODE, 'Node.js not installed')
class TestN8nNodes(unittest.TestCase):
    def run_flow(self, **change):
        out = subprocess.run([NODE, '-e', HARNESS, str(SRC / 'guard.js'), str(SRC / 'dispatch.js'),
                              json.dumps({**BASE, **change})],
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_auto_call_accepted_as_staff_task(self):
        r = self.run_flow()
        self.assertTrue(r['accepted'])
        self.assertEqual(r['dispatch']['type'], 'staff_task')
        self.assertFalse(r['executed'])

    def test_reminder_is_preview_only(self):
        r = self.run_flow(action_type='reminder', rule_id='I2')
        self.assertTrue(r['dispatch']['to'].startswith('NOT SENT'))

    def test_pending_or_rejected_blocked(self):
        for status in ('pending', 'rejected'):
            r = self.run_flow(action_type='resale', booking_id=None, stay_date='2017-03-01',
                              room_type='A', needs_approval=True, approval_status=status)
            self.assertFalse(r['accepted'], status)

    def test_sensitive_auto_blocked(self):
        r = self.run_flow(action_type='overbooking', booking_id=None, stay_date='2017-03-01',
                          room_type='A', approval_status='auto', needs_approval=False)
        self.assertFalse(r['accepted'])

    def test_approved_overbooking_still_blocked(self):
        r = self.run_flow(action_type='overbooking', booking_id=None, stay_date='2017-03-01',
                          room_type='A', approval_status='approved', needs_approval=True,
                          execution_policy={'overbooking': {'enabled': True}})
        self.assertFalse(r['accepted'])
        self.assertTrue(any('disabled' in x for x in r['reasons']))

    def test_call_deadlines_and_required_policy(self):
        for changes in [
            {'as_of': '2017-01-15'},
            {'as_of': '2017-01-09', 'arrival_date': '2017-01-16'},
            {'execution_policy': {}},
            {'known_from': None},
            {'arrival_date': '2017-02-30'},
            {'decision_at': '2017-01-10'},
        ]:
            with self.subTest(changes=changes):
                self.assertFalse(self.run_flow(**changes)['accepted'])

    def test_last_window_day_and_eight_day_lead_accepted(self):
        self.assertTrue(self.run_flow(as_of='2017-01-14', arrival_date='2017-01-22')['accepted'])

    def test_approved_group_action_accepted(self):
        r = self.run_flow(action_type='capacity_allocation', booking_id=None,
                          stay_date='2017-03-01', room_type='A', needs_approval=True,
                          approval_status='approved', precheck={})
        self.assertTrue(r['accepted'])
        self.assertEqual(r['dispatch']['type'], 'approved_change_notice')

    def test_missing_precheck_blocked(self):
        r = self.run_flow(precheck={'consent_confirmed': True})
        self.assertFalse(r['accepted'])
        self.assertTrue(any('no_open_conflict' in x for x in r['reasons']))

    def test_not_due_blocked(self):
        r = self.run_flow(as_of='2017-01-07')
        self.assertFalse(r['accepted'])

    def test_null_booking_id_rejected(self):
        self.assertFalse(self.run_flow(booking_id=None)['accepted'])

    def test_already_executed_or_unknown_state_blocked(self):
        self.assertFalse(self.run_flow(already_executed=True)['accepted'])
        payload_without_state = self.run_flow(already_executed=None)
        self.assertFalse(payload_without_state['accepted'])

    def test_guest_response_stops_follow_ups(self):
        r = self.run_flow(action_type='reminder', rule_id='I2', guest_response='confirmed')
        self.assertFalse(r['accepted'])
        self.assertTrue(any('responded' in x for x in r['reasons']))

    def test_approved_offer_after_change_request_accepted(self):
        r = self.run_flow(action_type='offer_change', rule_id='I4', needs_approval=True,
                          approval_status='approved', guest_response='requested_change',
                          guest_response_action_type='phone_call',
                          offer_basis_event_id=7, latest_response_event_id=7)
        self.assertTrue(r['accepted'])

    def test_offer_for_an_earlier_change_request_refused(self):
        offer = dict(action_type='offer_change', rule_id='I4', needs_approval=True,
                     approval_status='approved', guest_response='requested_change',
                     guest_response_action_type='phone_call')
        for basis, latest in [(7, 9), (None, 9), ('7', None)]:
            with self.subTest(basis=basis, latest=latest):
                r = self.run_flow(**offer, offer_basis_event_id=basis, latest_response_event_id=latest)
                self.assertFalse(r['accepted'])
                self.assertTrue(any('earlier change request' in x for x in r['reasons']))

    def test_offer_needs_current_change_request_from_call(self):
        for response, contact_type in [('confirmed', 'phone_call'), ('cancelled', 'phone_call'),
                                       (None, None), ('requested_change', None),
                                       ('requested_change', 'reminder')]:
            with self.subTest(response=response, contact_type=contact_type):
                r = self.run_flow(action_type='offer_change', rule_id='I4', needs_approval=True,
                                  approval_status='approved', guest_response=response,
                                  guest_response_action_type=contact_type)
                self.assertFalse(r['accepted'])


class TestWorkflowFile(unittest.TestCase):
    def test_json_matches_source_code(self):
        self.assertEqual(json.loads(OUT.read_text(encoding='utf-8')), workflow())


if __name__ == '__main__':
    unittest.main()
