"""Action Review page: approve / reject one recommendation, replace an action, and approve
sensitive actions one at a time; every decision is checked by state_store."""
import os
import unittest

from dashboard import state_store as ss
from test_g2_app import AppCase, AppTest


@unittest.skipUnless(AppTest, 'streamlit not installed')
class TestActionReview(AppCase):
    def open(self, booking=None):
        at = self.page(self.app(), 'action_review')
        if booking is not None:
            at.number_input(key='ar_booking').set_value(booking).run()
        return at

    def con(self, at):
        return at.session_state[f"g2_store::{os.environ['G2_DB_PATH']}"]

    def click(self, at, label):
        next(b for b in at.button if b.label == label).click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])

    def test_opens_on_the_riskiest_open_booking(self):
        at = self.open()
        self.assertIn('Action Review', [h.value for h in at.main.header])
        self.assertEqual(at.number_input(key='ar_booking').value, 22719)
        self.assertTrue(any('review status: **Awaiting review**' in m.value for m in at.markdown))
        self.assertTrue(any('#1 of 420' in m.value for m in at.markdown))

    def test_approve_needs_a_name_then_schedules(self):
        at = self.open(22719)
        self.click(at, 'Approve & schedule')
        self.assertTrue(any('Enter your name' in e.value for e in at.error))
        at.text_input(key='ar_reviewer').input('Res lead').run()
        self.click(at, 'Approve & schedule')
        self.assertTrue(any('approved: 1 contact(s) scheduled' in s.value for s in at.success))
        self.assertTrue(any('review status: **Approved**' in m.value for m in at.markdown))
        self.assertTrue(any('Ready to execute' in i.value for i in at.info))
        self.assertEqual(ss.plan_review(self.con(at), 22719)['reviewer'], 'Res lead')

    def test_reject_needs_a_reason(self):
        at = self.open(66317)
        at.text_input(key='ar_reviewer').input('Res lead').run()
        next(b for b in at.button if b.key == 'ar_reject').click().run()
        self.assertTrue(any('reason' in e.value for e in at.error))
        at.text_area(key='ar_reject_reason').input('Guest is a regular').run()
        next(b for b in at.button if b.key == 'ar_reject').click().run()
        self.assertTrue(any('rejected' in s.value for s in at.success))
        review = ss.plan_review(self.con(at), 66317)
        self.assertEqual((review['decision'], review['reason']), ('rejected', 'Guest is a regular'))
        self.assertEqual(ss.get_actions(self.con(at), 66317), [])

    def test_replace_a_call_by_a_reminder(self):
        at = self.open(22719)
        at.text_input(key='ar_reviewer').input('Res lead').run()
        self.click(at, 'Approve & schedule')
        [call] = ss.get_actions(self.con(at), 22719)
        next(s for s in at.selectbox if s.label == 'Replace with').set_value('reminder')
        next(t for t in at.text_input if t.label == 'Why').input('Guest asked for email only')
        self.click(at, 'Replace action')
        self.assertTrue(any(f"Action #{call['action_id']} replaced by" in s.value for s in at.success),
                        [e.value for e in at.error])
        acts = {a['action_type']: a for a in ss.get_actions(self.con(at), 22719)}
        self.assertIn('Replaced by reminder: Guest asked for email only', acts['phone_call']['closed'])
        self.assertEqual(acts['reminder']['decision_at'], '2017-01-01')

    def test_sensitive_actions_are_approved_one_by_one(self):
        at = self.open()
        con = self.con(at)
        aid = ss.create_action(con, 'capacity_allocation', stay_date='2017-01-08', room_type='D',
                               decision_at='2017-01-01', known_from='2017-01-01')
        at.segmented_control(key='ar_mode').set_value('approvals').run()
        self.assertEqual(next(s for s in at.selectbox if s.label == 'Open action').value, aid)
        at.text_input(key='ar_reviewer').input('Revenue manager').run()
        self.click(at, 'Approve selected action')
        self.assertTrue(any(f'Action #{aid} approved by Revenue manager' in s.value for s in at.success))
        self.assertEqual(ss.get_action(con, aid)['approval_status'], 'approved')
        self.assertTrue(any('Nothing waits for approval' in i.value for i in at.info))

    def test_link_with_query_params(self):
        at = self.app()
        at.query_params['booking'] = '66317'
        at = self.page(at, 'action_review')
        self.assertEqual(at.number_input(key='ar_booking').value, 66317)


if __name__ == '__main__':
    unittest.main()
