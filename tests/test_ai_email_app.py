"""The AI email section on the Execution & follow-up page, rendered headlessly (local offline mode:
labelled fixtures / manual labels, local mock sends; no n8n, model or email)."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

try:
    import streamlit as st
    from streamlit.testing.v1 import AppTest
except ImportError:  # streamlit not installed in this interpreter
    AppTest = None

from dashboard.data_contract import REPO

APP = REPO / 'dashboard' / 'app.py'
KEYS = ('G2_DB_PATH', 'G2_TODAY', 'G2_SEED_HISTORY', 'G2_AI_ACCESS_CODE', 'G2_AI_DB_URL', 'G2_DB_URL',
        'G2_AI_DRAFT_WEBHOOK_URL', 'G2_AI_SEND_WEBHOOK_URL', 'G2_AI_WORKSPACE_ID', 'G2_AI_SQLITE_PATH')


@unittest.skipUnless(AppTest, 'streamlit not installed')
class TestAiEmailPanel(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in KEYS}
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ.update(G2_DB_PATH=str(Path(self.tmp.name) / 'g2.sqlite'), G2_TODAY='2017-05-01',
                          G2_SEED_HISTORY='0')

    def tearDown(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        st.cache_data.clear()
        st.cache_resource.clear()
        self.tmp.cleanup()

    def page(self):
        at = AppTest.from_file(str(APP), default_timeout=180).run()
        at = at.switch_page('views/execution.py').run()
        self.ok(at)
        return at

    def ok(self, at):
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertFalse(at.error, [e.value for e in at.error])

    @staticmethod
    def button(at, key=None, label=None):
        return next(b for b in at.button if (key and b.key == key) or (label and b.label.startswith(label)))

    @staticmethod
    def text(at):
        return ' '.join(m.value for m in at.markdown) + ' ' + ' '.join(c.value for c in at.caption)

    def choose(self, at, email_type, booking=27767):
        at.number_input(key='g2_ai_booking').set_value(booking).run()
        at.radio(key='g2_ai_type').set_value(email_type).run()
        return at

    def approve(self, at, n=1):
        next(t for t in at.text_input if t.label == 'Reviewer label').input('Lan (demo)')
        self.button(at, label=f'Approve revision {n}').click().run()
        self.ok(at)
        return at

    def g2_counts(self):
        con = sqlite3.connect(os.environ['G2_DB_PATH'])
        try:
            return [con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                    for t in ('action_plan', 'interventions', 'guest_responses', 'execution_attempts')]
        finally:
            con.close()

    def test_reminder_draft_approve_and_mock_send(self):
        at = self.page()
        self.assertIn('AI email drafts (DEMO)', [h.value for h in at.subheader])
        before = self.g2_counts()
        at = self.choose(at, 'reminder')
        self.assertFalse([t for t in at.text_area if t.key == 'g2_ai_message'])  # no guest message for a reminder
        self.button(at, key='g2_ai_generate').click().run()
        self.ok(at)
        self.assertIn('[DEMO] Reminder', self.text(at))
        self.assertIn('Demo fixture — not an AI prediction', self.text(at))
        body = at.code[0].value
        self.assertIn('Booking reference: 27767', body)
        self.assertIn('Arrival date: 8 May 2017', body)
        self.approve(at)
        self.assertIn('Approved', self.text(at))
        self.button(at, label='Simulate send (mock)').click().run()
        self.ok(at)
        self.assertIn('Simulated (mock, nothing sent)', self.text(at))
        self.assertFalse([b for b in at.button if b.label.startswith('Simulate send')])  # no second send
        self.assertEqual(self.g2_counts(), before)   # no G2 action, execution or guest response written

    def test_confirmation_needs_no_message_and_switching_type_drops_the_draft(self):
        at = self.choose(self.page(), 'reminder')
        self.button(at, key='g2_ai_generate').click().run()
        self.approve(at)
        at = self.choose(at, 'booking_confirmation')
        self.ok(at)
        self.assertNotIn('[DEMO] Reminder', self.text(at))        # old draft / approval not carried over
        self.assertIn('Selection changed', self.text(at))
        self.button(at, key='g2_ai_generate').click().run()
        self.ok(at)
        self.assertIn('[DEMO] Your booking 27767', self.text(at))
        self.assertFalse([b for b in at.button if b.label.startswith('Simulate send')])  # needs its own approval
        at = self.choose(at, 'reminder')
        self.assertIn('[DEMO] Reminder', self.text(at))
        self.assertTrue([b for b in at.button if b.label.startswith('Simulate send')])

    def test_reply_requires_message_and_shows_unclear(self):
        at = self.choose(self.page(), 'guest_reply')
        self.assertTrue(any('needs the guest message' in i.value for i in at.info))
        self.assertFalse([b for b in at.button if b.key == 'g2_ai_generate'])
        at.selectbox(key='g2_ai_sample').set_value('vi-maybe-cancel').run()
        self.button(at, key='g2_ai_generate').click().run()
        self.ok(at)
        text = self.text(at)
        self.assertIn('Chưa xác nhận / cần hỏi lại', text)
        self.assertIn('tentative', text)
        self.assertIn('Có thể tôi sẽ hủy', text)

    def test_free_text_offline_uses_manual_label(self):
        at = self.choose(self.page(), 'guest_reply')
        at.text_area(key='g2_ai_message').input('Tôi muốn đổi sang tuần sau nếu được.').run()
        at.radio(key='g2_ai_mode').set_value('manual').run()
        at.selectbox(key='g2_ai_mi').set_value('change_requested').run()
        self.button(at, key='g2_ai_generate').click().run()
        self.ok(at)
        text = self.text(at)
        self.assertIn('Manual label by staff — not an AI prediction', text)
        self.assertIn('tuần sau', text)

    def test_edit_after_approval_needs_new_approval(self):
        at = self.choose(self.page(), 'booking_confirmation')
        self.button(at, key='g2_ai_generate').click().run()
        self.approve(at)
        form_area = next(t for t in at.text_area if t.label.startswith('Text'))
        form_area.input(form_area.value + '\n\nP.S. We look forward to your stay.')
        self.button(at, label='Save as new revision').click().run()
        self.ok(at)
        self.assertIn('Revision 2', self.text(at))
        self.assertFalse([b for b in at.button if b.label.startswith('Simulate send')])
        self.assertTrue([b for b in at.button if b.label.startswith('Approve revision 2')])

    def test_access_code_gate(self):
        os.environ['G2_AI_ACCESS_CODE'] = 'letmein'
        at = self.choose(self.page(), 'reminder')
        self.assertFalse([b for b in at.button if b.key == 'g2_ai_generate'])
        at.text_input(key='g2_ai_code').input('wrong').run()
        self.assertFalse([b for b in at.button if b.key == 'g2_ai_generate'])
        at.text_input(key='g2_ai_code').input('letmein').run()
        self.assertTrue([b for b in at.button if b.key == 'g2_ai_generate'])


if __name__ == '__main__':
    unittest.main()
