"""README §6.5 extended scenario: room-night ledger, occupancy / ADR / RevPAR, price sensitivity."""
import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/scenarios_capacity'
sys.path.insert(0, str(ROOT / 'src'))


@unittest.skipUnless((ROOT / 'artifacts/business_case/retrospective_evaluation_2017.csv').exists(),
                     'Run stage G1 to generate artifacts')
class ScenarioCapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import scenario_capacity as sc
        cls.sc = sc
        cls.result = sc.run(out=None)
        cls.kpi = pd.DataFrame(cls.result['scenarios']).set_index('scenario')

    def test_base_is_the_realised_inventory(self):
        self.assertTrue(self.result['base_matches_inventory_rooms_sold'])
        s0 = self.kpi.loc['S0 Actual 2017']
        self.assertAlmostEqual(s0['revpar_eur'], s0['occupancy'] * s0['adr_eur'], places=6)
        self.assertEqual(s0['delta_room_revenue_eur'], 0)

    def test_no_cell_sells_more_than_it_has(self):
        I = self.sc.build_inputs()
        d4, _ = self.sc.g1_delta(I)
        s4, _ = self.sc.cap(I['base'].add(d4.reindex(I['cells'].index, fill_value=0.0)), I['cells'])
        self.assertTrue((s4['rooms'] <= I['cells']['available_rooms'] + 1e-9).all())
        self.assertTrue((s4['rooms'] >= 0).all())

    def test_g1_ledger_agrees_with_the_notebook_05_value(self):
        g1 = self.result['g1']
        self.assertAlmostEqual(g1['prevented_cancellations_expected'], 85.56, places=2)
        s4 = self.kpi.loc['S4 G1 policy']
        self.assertGreater(s4['delta_room_nights'], 0)
        # two routes to the same expected value: ledger contribution vs notebook 05 net (within 5%)
        self.assertAlmostEqual(s4['contribution_net_eur'] / g1['g1_net_eur_notebook05'], 1.0, delta=0.05)

    def test_group_track_matches_notebook_05_counts(self):
        gt = pd.read_csv(ROOT / 'artifacts/business_case/group_track_estimate.csv', index_col=0)['value']
        g = self.result['group_track']
        self.assertEqual(g['group_bookings_flagged'], int(gt['flagged_for_review']))
        self.assertEqual(g['late_cancellations_flagged'], int(gt['late_cancellations_among_flagged']))
        self.assertAlmostEqual(g['intervention_cost_eur'], gt['review_cost_eur'])
        self.assertGreaterEqual(self.kpi.loc['S5 G1 + group early release', 'room_nights_sold'],
                                self.kpi.loc['S4 G1 policy', 'room_nights_sold'])

    def test_price_effect_sign_follows_the_elasticity(self):
        I = self.sc.build_inputs()
        led = I['base']
        rev = lambda e: self.sc.price_adjust(led, I['cells'], I['A'], e, 0.05, cut=False)['revenue'].sum()
        self.assertGreater(rev(-0.4), led['revenue'].sum())
        self.assertLess(rev(-1.5), led['revenue'].sum())
        self.assertEqual(self.sc.price_adjust(led, I['cells'], I['A'], 0.0, 0.0)['revenue'].sum(), led['revenue'].sum())

    def test_price_assumption_is_labelled(self):
        pa = self.sc.load_assumptions()['g1']['price_adjustment']
        for key in ('elasticity', 'price_step'):
            self.assertEqual(pa[key]['basis'], 'team assumption')
            self.assertEqual(len(pa[key]['sensitivity_range']), 2)
        self.assertEqual(self.result['price_adjustment']['basis'], 'team assumption')

    @unittest.skipUnless((OUT / 'summary.json').exists(), 'run src/scenario_capacity.py')
    def test_saved_outputs_match_a_fresh_run(self):
        saved = pd.read_csv(OUT / 'scenario_kpis.csv', index_col=0)
        pd.testing.assert_frame_equal(saved[self.kpi.columns], self.kpi, check_exact=False, rtol=1e-9)
        sens = pd.read_csv(OUT / 'price_sensitivity.csv')
        self.assertTrue({-0.4, -1.5}.issubset(set(sens['elasticity'])))
        self.assertIn('illustrative', json.loads((OUT / 'summary.json').read_text())['note'])


if __name__ == '__main__':
    unittest.main()
