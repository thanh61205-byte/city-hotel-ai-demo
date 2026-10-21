"""Streamlit Community Cloud packaging for the AI email section (no network)."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

from dashboard.ai_email import config
from dashboard.data_contract import source_paths
from dashboard.g1_contract import g1_paths

ROOT = Path(__file__).resolve().parents[1]


def packages(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.split('#')[0].strip()
        if line:
            out.add(re.split(r'[<>=\[ ]', line)[0].lower())
    return out


class TestPackaging(unittest.TestCase):
    def test_cloud_requirements_match_g2_requirements(self):
        self.assertEqual(packages(ROOT / 'dashboard' / 'requirements.txt'), packages(ROOT / 'requirements-g2.txt'))
        self.assertNotIn('xgboost', packages(ROOT / 'dashboard' / 'requirements.txt'))

    def test_cloud_app_does_not_import_the_training_stack(self):
        code = ("import sys; sys.argv=['x']; import dashboard.ai_email.panel, dashboard.views; "
                "bad=[m for m in ('sklearn','xgboost','optuna') if m in sys.modules]; print(bad)")
        out = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), '[]', out.stderr)

    def test_secrets_template_lists_every_key_and_holds_no_secret(self):
        text = (ROOT / '.streamlit' / 'secrets.toml.example').read_text(encoding='utf-8')
        keys = set(re.findall(r'^\s*#?\s*(G2_[A-Z0-9_]+)\s*=', text, re.M))
        used = set(re.findall(r"setting\('(G2_[A-Z0-9_]+)'", (ROOT / 'dashboard' / 'ai_email' / 'config.py')
                              .read_text(encoding='utf-8')))
        self.assertLessEqual(used - {'G2_DB_PATH', 'G2_AI_SQLITE_PATH', 'G2_AI_ALLOW_INSECURE_WEBHOOK'}, keys)
        for value in re.findall(r'=\s*"([^"]*)"', text):
            self.assertFalse(re.search(r'sk-|ghp_|AIza|xox', value), value)
        self.assertIn('.streamlit/secrets.toml', (ROOT / '.gitignore').read_text(encoding='utf-8'))
        self.assertFalse((ROOT / '.streamlit' / 'secrets.toml').exists())

    def test_theme_at_repo_root_and_loader_files_shipped(self):
        self.assertTrue((ROOT / '.streamlit' / 'config.toml').is_file())
        for p in list(source_paths(ROOT).values()) + list(g1_paths(ROOT).values()):
            if p.name != 'retrospective_evaluation_2017.csv':
                self.assertTrue(p.exists(), p)
                self.assertLess(p.stat().st_size, 100 * 1024 * 1024, f'{p} is too large for GitHub')

    def test_cloud_defaults_are_safe(self):
        cfg = config.AiConfig(db_target='x.sqlite', workspace_id='w', draft_url='', send_url='', token='',
                              sender_ref='s', real_email_enabled=True, live_ai=True, access_code='',
                              read_only=False, draft_timeout=45, send_timeout=30, allow_insecure=False)
        self.assertFalse(cfg.real_email)   # never real email without workflow B + PostgreSQL + token
        self.assertFalse(cfg.n8n_draft)


if __name__ == '__main__':
    unittest.main()
