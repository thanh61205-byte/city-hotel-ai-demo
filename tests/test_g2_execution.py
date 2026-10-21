"""Execution & follow-up page: due actions with whether they can run, recording a result,
statuses that only change with the log, failed automation shown for retry, booking history."""
import os
import unittest

from dashboard import state_store as ss
from dashboard.data_contract import load_outputs
from test_g2_app import AppCase, AppTest

D0 = '2017-01-01'


@unittest.skipUnless(AppTest, 'streamlit not installed')
class TestExecutionPage(AppCase):
    def setUp(self):
        super().setUp()
        _, self.schedule, _ = load_outputs()

    def open_with(self, *bookings):
        at = self.app()
        con = at.session_state[f"g2_store::{os.environ['G2_DB_PATH']}"]
        q = load_outputs()[0].set_index('booking_id')
        for bid in bookings:
            ss.review_plan(con, bid, self.schedule[self.schedule['booking_id'].eq(bid)].to_dict('records'),
                           arrival_date=q.at[bid, 'arrival_date'], reviewer='Res lead', approve=True,
                           as_of=D0)
        return self.page(at, 'execution'), con

    def test_due_call_is_ready_and_recording_changes_its_status(self):
        at, con = self.open_with(22719)
        cards = {m.label: m.value for m in at.metric}
        self.assertEqual((cards['Due now'], cards['Failed'], cards['Calls left']), ('1', '0', '20'))
        [call] = ss.get_actions(con, 22719)
        self.assertEqual(at.selectbox(key='ex_open').value, call['action_id'])
        for label in ['Guest has not opted out of contact', 'No other open action of this type',
                      'Booking still active in PMS (not cancelled / arrived)']:
            next(x for x in at.checkbox if x.label == label).set_value(True)
        next(b for b in at.button if b.label == 'Record execution (staff)').click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue(any('execution #' in s.value and 'recorded' in s.value for s in at.success))
        self.assertEqual({m.label: m.value for m in at.metric}['Due now'], '0')
        self.assertTrue(any('No action matches' in i.value for i in at.info))  # Due / Failed only
        at.multiselect(key='ex_status').select('Completed').run()
        self.assertTrue(any(m.value.startswith('**1 actions**') for m in at.markdown))
        at.text_input(key='ex_search').input('22719').run()
        self.assertEqual(at.number_input(key='ex_history_22719').value, 22719)  # follows the search
        self.assertTrue(any('Booking history' == h.value for h in at.subheader))

    def test_failed_automation_is_listed_with_retry(self):
        os.environ['G2_N8N_WEBHOOK_URL'] = 'http://127.0.0.1:9/never-called'
        try:
            at, con = self.open_with(22719)
            [call] = ss.get_actions(con, 22719)
            with ss._unit(con):
                ss._log_attempt(con, call['action_id'], D0, 'error', 'ConnectionError: n8n down')
            at.run()
            self.assertEqual({m.label: m.value for m in at.metric}['Failed'], '1')
            self.assertTrue(any(b.label == 'Retry via n8n' for b in at.button))
            self.assertIsNone(ss.get_actions(con, 22719)[0]['intervention_id'])  # not done
        finally:
            os.environ.pop('G2_N8N_WEBHOOK_URL')

    def test_filters_by_booking_and_type(self):
        at, _ = self.open_with(22719, 66317)
        at.multiselect(key='ex_status').set_value([]).run()
        total = next(m.value for m in at.markdown if m.value.endswith('actions** match.'))
        at.text_input(key='ex_search').input('66317').run()
        self.assertNotEqual(total, next(m.value for m in at.markdown if 'actions** match' in m.value))
        self.assertTrue(all('66317' in o for o in at.selectbox(key='ex_open').options))


if __name__ == '__main__':
    unittest.main()
