"""Model monitoring (dashboard/model_view.py and its page): figures computed on bookings that
arrived before today only, never on later arrivals."""
import unittest
from datetime import date

import numpy as np
import pandas as pd

from dashboard import data_contract as dc, model_view as mv
from test_g2_app import AppCase, AppTest  # tests/ is on sys.path under discover

REPO = dc.REPO
TODAY = date(2017, 5, 1)


class TestMetrics(unittest.TestCase):
    def test_auc_and_average_precision_on_small_cases(self):
        y = np.array([1, 0, 1, 0])
        self.assertEqual(mv.roc_auc(y, np.array([0.9, 0.8, 0.7, 0.1])), 0.75)
        self.assertEqual(mv.roc_auc(y, np.array([0.5, 0.5, 0.5, 0.5])), 0.5)  # ties count half
        # Ranked 1, 0, 1, 0: precision 1 at recall 0.5, 2/3 at recall 1.
        self.assertAlmostEqual(mv.pr_auc(y, np.array([0.9, 0.8, 0.7, 0.1])), 0.5 + 0.5 * 2 / 3)
        self.assertTrue(np.isnan(mv.roc_auc(np.array([1, 1]), np.array([0.2, 0.3]))))

    def test_top_share_and_deciles(self):
        df = pd.DataFrame({'booking_id': range(10), 'p_cancel': np.linspace(0.9, 0.0, 10),
                           'is_canceled': [1, 1, 0, 0, 1, 0, 0, 0, 0, 0], 'gross_value': 100.0})
        top = mv.top_share_capture(df, 0.2)
        self.assertEqual((top['bookings'], top['precision'], round(top['recall'], 3)), (2, 1.0, 0.667))
        self.assertAlmostEqual(top['lift'], 1.0 / 0.3)
        dec = mv.deciles(df)
        self.assertEqual(list(dec['risk_decile']), list(range(1, 11)))
        self.assertEqual(list(dec['cancel_rate'][:2]), [1.0, 1.0])


class TestArrivedOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue = dc.load_outputs()[0]
        cls.outcome = mv.outcomes(REPO)

    def test_only_individual_bookings_arrived_before_today(self):
        arrived = mv.arrived(self.queue, self.outcome, TODAY)
        self.assertEqual(len(arrived), 4315)
        self.assertTrue((arrived['arrival_date'] < TODAY).all())
        self.assertTrue(arrived['track'].eq('individual model').all())
        self.assertTrue(arrived['is_canceled'].isin([0, 1]).all())
        yesterday = mv.arrived(self.queue, self.outcome, TODAY, start=date(2017, 4, 30))
        self.assertTrue(arrived['arrival_date'].max() == yesterday['arrival_date'].min() == date(2017, 4, 30))

    def test_figures_follow_today(self):
        early = mv.metrics(mv.arrived(self.queue, self.outcome, date(2017, 3, 1)))
        later = mv.metrics(mv.arrived(self.queue, self.outcome, TODAY))
        self.assertLess(early['bookings'], later['bookings'])
        for m in (early, later):
            self.assertTrue(0.5 < m['roc_auc'] < 1 and 0 < m['brier'] < 0.25)

    def test_operational_queue_still_has_no_outcome(self):
        self.assertFalse(dc.POST_OUTCOME & set(self.queue.columns))


@unittest.skipUnless(AppTest, 'streamlit not installed')
class TestModelMonitoringPage(AppCase):
    def test_page_renders_on_arrived_bookings(self):
        self.set_today(TODAY, history=False)
        at = self.page(self.app(), 'model_revenue')
        self.assertFalse(at.exception, [x.value for x in at.exception])
        self.assertIn('Model monitoring', [h.value for h in at.main.header])
        cards = {m.label: m.value for m in at.metric}
        self.assertEqual(cards.get('Arrived bookings'), '4,315')
        self.assertIn('ROC-AUC', cards)
        self.assertFalse(any('Model check' in m.value for m in at.markdown))  # no model-vs-rule table
        at.multiselect(key='mv_seg').select('Online TA').run()
        self.assertFalse(at.exception, [x.value for x in at.exception])
        self.assertNotEqual({m.label: m.value for m in at.metric}['Arrived bookings'], '4,315')


if __name__ == '__main__':
    unittest.main()
