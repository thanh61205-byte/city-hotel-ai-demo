"""Support pages: Capacity planning, Performance (operations vs outcomes of arrived bookings),
Model monitoring, Data scope."""
import os
import unittest
from datetime import date

import pandas as pd

from dashboard import state_store as ss
from dashboard.data_contract import REPO, load_outputs, source_paths
from test_g2_app import AppCase, AppTest

OK = {'consent_confirmed': True, 'no_open_conflict': True, 'booking_active': True}


@unittest.skipUnless(AppTest, 'streamlit not installed')
class TestSupportPages(AppCase):
    def con(self, at):
        return at.session_state[f"g2_store::{os.environ['G2_DB_PATH']}"]

    def test_capacity_planning_counts_and_proposes(self):
        at = self.page(self.app(), 'buffer')
        self.assertFalse(at.exception, [e.value for e in at.exception])
        cards = {m.label: m.value for m in at.metric}
        self.assertEqual(cards['Bookings counted'], '5')  # next 30 days: 4 on 8 Jan + 1 on 15 Jan
        self.assertTrue(any(m.value.startswith('**Dates to watch:**') for m in at.markdown))
        next(b for b in at.button if b.label == 'Propose action (goes to approval)').click().run()
        self.assertTrue(any('(Hold rooms back) proposed' in s.value for s in at.success))
        [a] = ss.get_actions(self.con(at))
        self.assertEqual(a['approval_status'], 'pending')

    def test_performance_keeps_outcomes_to_arrived_bookings(self):
        at = self.app()
        con = self.con(at)
        _, schedule, _ = load_outputs()
        rows = schedule[schedule['booking_id'].eq(22719)].to_dict('records')
        [call], _ = ss.review_plan(con, 22719, rows, arrival_date='2017-01-21', reviewer='Res lead',
                                   approve=True, as_of='2017-01-01')
        ss.record_execution(con, call, as_of='2017-01-01', outcome='no_response', precheck=OK)
        at = self.page(at, 'performance')
        cards = {m.label: m.value for m in at.metric}
        self.assertEqual((cards['Approved'], cards['Contacts done'], cards['Done when due']),
                         ('1', '1', '100.0%'))
        self.assertTrue(any('No scored booking has reached its arrival date' in i.value for i in at.info))
        self.assertFalse(any('P3' in c.value or 'Approval time' in c.value for c in at.caption))
        # Same log, six weeks later: outcomes of the bookings that have arrived meanwhile.
        self.set_today(date(2017, 2, 15))
        at = self.page(self.app(), 'performance')
        self.assertFalse(at.exception, [e.value for e in at.exception])
        cards = {m.label: m.value for m in at.metric}
        self.assertNotEqual(cards.get('Arrived bookings', '0'), '0')
        self.assertTrue(any('not the effect of the contacts' in w.value for w in at.warning))

    def test_model_monitoring_states_its_limits(self):
        at = self.page(self.app(), 'model_revenue')
        self.assertTrue(any(e.label == 'How to read these figures' for e in at.expander))
        self.assertTrue(any('No scored booking has reached its arrival date' in i.value for i in at.info))

    def test_data_scope_shows_checks_and_no_upload(self):
        at = self.page(self.app(), 'data_scope')
        self.assertFalse(at.exception, [e.value for e in at.exception])
        n = len(pd.read_csv(source_paths(REPO)['consistency']))
        self.assertTrue(any(f'{n} of {n} passed' in m.value for m in at.markdown))
        self.assertTrue(any('not available in this dashboard' in m.value for m in at.markdown))


if __name__ == '__main__':
    unittest.main()
