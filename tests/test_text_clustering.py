"""README §6.3 text clustering: outputs, determinism, and that the reason text never feeds the model."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/text_clustering'
sys.path.insert(0, str(ROOT / 'src'))


class TextClusteringTests(unittest.TestCase):
    def test_reason_text_is_never_a_model_feature(self):
        groups = json.loads((ROOT / 'config/column_groups.json').read_text())
        used = json.dumps({k: v for k, v in groups.items() if 'exclu' not in k})
        for col in ('cancellation_reason_text', 'cancellation_reason_category', 'syn_cancellation_reason_text',
                    'syn_cancellation_reason_category'):
            self.assertNotIn(f'"{col}"', used)

    def test_small_grid_is_deterministic_and_consistent(self):
        import text_clustering as tc
        old = tc.K_GRID
        tc.K_GRID = range(5, 7)
        try:
            with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
                s1, s2 = tc.run(out=Path(a)), tc.run(out=Path(b))
                self.assertEqual(s1, s2)
                c = pd.read_csv(Path(a) / 'clusters.csv')
                self.assertEqual(c['size'].sum(), s1['texts'])
                self.assertEqual(len(c), s1['k_selected'])
                self.assertTrue(c['label'].notna().all() and c['message_angle'].notna().all())
                self.assertTrue((c.loc[c['purity'] < tc.MIXED_PURITY, 'label'] == 'mixed / unspecified').all())
                mix = pd.read_csv(Path(a) / 'reason_mix_by_segment.csv', index_col=0)
                self.assertTrue(((mix.sum(axis=1) - 1).abs() < 1e-3).all())
        finally:
            tc.K_GRID = old

    @unittest.skipUnless((OUT / 'summary.json').exists(), 'run src/text_clustering.py')
    def test_saved_run(self):
        s = json.loads((OUT / 'summary.json').read_text())
        self.assertIn('Synthetic', s['note'])
        self.assertIn(s['k_selected'], s['k_grid'])
        k = pd.read_csv(OUT / 'k_selection.csv')
        self.assertEqual(int(k.loc[k['silhouette_sample'].idxmax(), 'k']), s['k_selected'])
        self.assertGreater(s['label_agreement_with_generator'], 0.8)


if __name__ == '__main__':
    unittest.main()
