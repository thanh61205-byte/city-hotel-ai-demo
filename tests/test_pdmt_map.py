"""docs/pdmt_map.png is drawn from the artifacts; its claims must match the model and G2."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


@unittest.skipUnless((ROOT / 'artifacts/scenarios_capacity/scenario_kpis.csv').exists(), 'run the scenarios first')
class PdmtMapTests(unittest.TestCase):
    def test_claims_match_the_f_manifest(self):
        import draw_pdmt_map
        n = draw_pdmt_map.numbers()  # asserts price fields excluded + retrospective cohort headline
        self.assertGreater(n['lift20'], n['lift20_rule'])

    def test_wording_matches_what_the_system_does(self):
        src = (ROOT / 'src/draw_pdmt_map.py').read_text(encoding='utf-8')
        self.assertIn('ADR / price fields excluded', src)
        self.assertIn('mock dispatch: sends nothing', src)
        self.assertIn('retrospective 2017 arrival cohort', src)
        self.assertNotIn('executes approved actions', src)
        self.assertNotIn('deposit, ADR, history', src)


if __name__ == '__main__':
    unittest.main()
