"""Render the Streamlit app headlessly (streamlit.testing)."""
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

try:
    import streamlit as st
    from streamlit.testing.v1 import AppTest
except ImportError:  # streamlit not installed in this interpreter
    AppTest = None

from dashboard.data_contract import REPO, load_outputs, source_paths
from dashboard.g1_contract import g1_paths, policy_id
from dashboard import state_store as ss
from dashboard.state_store import SCHEMA_VERSION

APP = REPO / 'dashboard' / 'app.py'


D0 = '2017-01-01'  # the first scoring day: a new log has no history before it


@unittest.skipUnless(AppTest, 'streamlit not installed')
class AppCase(unittest.TestCase):
    """The app on a fixed day (G2_TODAY, set before the app starts), 2017-01-01 unless a test
    chooses another with set_today()."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ['G2_DB_PATH'] = str(Path(self.tmp.name) / 'smoke.sqlite')
        self.set_today(D0)

    @staticmethod
    def set_today(day, history: bool = True):
        """Today for the next app run; history=False starts a new log empty (no history)."""
        os.environ['G2_TODAY'] = str(day)
        os.environ['G2_SEED_HISTORY'] = '1' if history else '0'

    def tearDown(self):
        for key in ('G2_DB_PATH', 'G2_REPO', 'G2_TODAY', 'G2_SEED_HISTORY'):
            os.environ.pop(key, None)
        st.cache_data.clear()
        st.cache_resource.clear()
        self.tmp.cleanup()

    def app(self):
        return AppTest.from_file(str(APP), default_timeout=120).run()

    @staticmethod
    def page(at, name: str):
        """Open dashboard/views/<name>.py (st.navigation page) and run it."""
        return at.switch_page(f'views/{name}.py').run()


PAGES = ('overview', 'risk_queue', 'action_review', 'execution', 'buffer', 'performance',
         'model_revenue', 'data_scope')


class TestAppSmoke(AppCase):
    def test_each_session_has_its_own_connection(self):
        # Final review 20/10: a shared connection let one session's commit end another's transaction.
        first, second = self.app(), self.app()
        key = f"g2_store::{os.environ['G2_DB_PATH']}"
        self.assertIsNot(first.session_state[key], second.session_state[key])
        for at in (first, second):
            self.assertFalse(at.exception, [x.value for x in at.exception])

    def test_manual_and_group_forms_use_g1_policy_key(self):
        at = self.page(self.app(), 'action_review')  # opens on booking 22719
        # Use a message so this test isolates the form's run identity from I3 eligibility.
        next(s for s in at.selectbox if s.label == 'Action').set_value('confirm_email')
        next(b for b in at.button if b.label == 'Propose action').click().run()
        self.assertFalse(at.exception)
        self.assertFalse(at.error, [e.value for e in at.error])
        self.assertTrue(any('(Confirmation email) proposed' in s.value for s in at.success))
        at = self.page(at, 'buffer')
        group = next(s for s in at.selectbox if s.label == 'Action')
        self.assertEqual(group.value, 'capacity_allocation')
        self.assertNotIn('Overbooking (disabled)', group.options)
        next(b for b in at.button if b.label == 'Propose action (goes to approval)').click().run()
        self.assertFalse(at.exception)
        self.assertFalse(at.error, [e.value for e in at.error])
        with sqlite3.connect(os.environ['G2_DB_PATH']) as con:
            run = con.execute("SELECT value FROM store_meta WHERE key='run_id'").fetchone()[0]
            rows = con.execute('SELECT source_run_id, approval_status FROM action_plan').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r[0] == run and '+g1-' in run for r in rows))
        self.assertIn((run, 'pending'), rows)

    def test_renders_without_exception(self):
        at = self.app()
        for name in PAGES:  # daily work, planning & analysis (retrospective model included), data
            at = self.page(at, name)
            self.assertFalse(at.exception, (name, [e.value for e in at.exception]))
            self.assertFalse(at.error, (name, [e.value for e in at.error]))

    def test_today_is_fixed_and_shown(self):
        at = self.app()
        self.assertFalse(at.sidebar.date_input)  # no date picker: today is set at start
        band = next(m.value for m in at.markdown if 'aria-label="Today' in m.value)
        self.assertIn('Sun 01 Jan 2017', band)
        self.assertNotIn('replay', band.lower())
        self.set_today('2017-13-01')
        self.assertTrue(any('YYYY-MM-DD' in e.value for e in self.app().error))
        self.set_today('2018-01-01')
        self.assertTrue(any('outside the scored period' in e.value for e in self.app().error))

    def test_default_today_is_1_may_2017(self):
        os.environ.pop('G2_TODAY')
        os.environ['G2_SEED_HISTORY'] = '0'  # the history itself: test_g2_seed_history.py
        at = self.app()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        band = next(m.value for m in at.markdown if 'aria-label="Today' in m.value)
        self.assertIn('Mon 01 May 2017', band)
        self.assertIn('Sun 30 Apr, 441 bookings', band)

    def test_execution_waits_for_review_then_lists_due_calls(self):
        at = self.app()  # Overview is the default page
        self.assertTrue(any(h.value == 'Overview' for h in at.main.header))
        at = self.page(at, 'execution')
        self.assertTrue(any('still awaiting review' in m.value for m in at.markdown))
        self.assertTrue(any('Nothing to carry out yet' in i.value for i in at.info))
        con = at.session_state[f"g2_store::{os.environ['G2_DB_PATH']}"]
        _, schedule, _ = load_outputs()
        rows = schedule[schedule['booking_id'].eq(22719)].to_dict('records')
        ss.review_plan(con, 22719, rows, arrival_date='2017-01-21', reviewer='Res lead',
                       approve=True, as_of='2017-01-01')
        at.run()
        opened = next(s for s in at.selectbox if s.label == 'Open action')
        self.assertEqual(len(opened.options), 1)
        self.assertIn('booking 22719', opened.options[0])
        self.assertTrue(any('Ready to execute' in i.value for i in at.info), [i.value for i in at.info])


class TestAppFollowsF(AppCase):
    """Astra #4 / #6: a rewritten F / G1 output is reloaded; a new run gets its own state DB."""

    def setUp(self):
        super().setUp()
        os.environ.pop('G2_DB_PATH')  # use the default per-run DB name, inside the fixture
        self.repo = Path(self.tmp.name) / 'repo'
        for paths in (source_paths, g1_paths):
            for key, src in paths(REPO).items():
                dst = paths(self.repo)[key]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        shutil.copytree(REPO / 'dashboard', self.repo / 'dashboard',
                        ignore=shutil.ignore_patterns('__pycache__'))
        os.environ['G2_REPO'] = str(self.repo)
        self.app_file = self.repo / 'dashboard' / 'app.py'

    def app(self):
        return AppTest.from_file(str(self.app_file), default_timeout=120).run()

    def test_rewritten_outputs_are_revalidated(self):
        at = self.app()
        self.assertFalse(at.error, [e.value for e in at.error])
        consistency = source_paths(self.repo)['consistency']
        consistency.write_text('check,passed\nsingle run_id in every output,False\n',
                               encoding='utf-8')
        at.run()
        self.assertTrue(any('failed checks' in e.value for e in at.error),
                        [e.value for e in at.error])

    def test_new_run_opens_a_new_state_db(self):
        self.app()
        outputs = self.repo / 'data' / 'outputs'
        first = sorted(p.name for p in outputs.glob('g2_demo_state_*.sqlite'))
        self.assertEqual(len(first), 1)
        # Simulate notebooks 04 + 05 writing a new run: same rows, new run_id everywhere.
        paths = source_paths(self.repo)
        old = json.loads(paths['manifest'].read_text(encoding='utf-8'))['run_id']
        files = [paths['manifest'], paths['queue'], paths['schedule'], g1_paths(self.repo)['policy']]
        for path in files:
            text = path.read_text(encoding='utf-8')
            path.write_text(text.replace(old, 'f-v3.2-test_run'), encoding='utf-8')
        at = self.app()
        self.assertFalse(at.error, [e.value for e in at.error])
        names = sorted(p.name for p in outputs.glob('g2_demo_state_*.sqlite'))
        key = f"f-v3.2-test_run+{policy_id(g1_paths(self.repo)['policy'])}"
        self.assertEqual(names, sorted(first + [f'g2_demo_state_{key}_20170101_v{SCHEMA_VERSION}.sqlite']))

    def test_each_today_has_its_own_log(self):
        self.app()
        self.set_today('2017-01-03', history=False)
        at = self.app()
        self.assertFalse(at.error, [e.value for e in at.error])
        names = sorted(p.name for p in (self.repo / 'data' / 'outputs').glob('g2_demo_state_*.sqlite'))
        self.assertEqual([n.split('_')[-2] for n in names], ['20170101', '20170103'])

    def test_new_f_run_without_g1_rerun_is_refused(self):
        paths = source_paths(self.repo)
        old = json.loads(paths['manifest'].read_text(encoding='utf-8'))['run_id']
        for key in ('manifest', 'queue', 'schedule'):
            text = paths[key].read_text(encoding='utf-8')
            paths[key].write_text(text.replace(old, 'f-v3.2-test_run'), encoding='utf-8')
        at = self.app()
        self.assertTrue(any('re-run notebook 05' in e.value for e in at.error), [e.value for e in at.error])

    def test_schema_upgrade_uses_new_file_and_preserves_old_log(self):
        run = json.loads(source_paths(self.repo)['manifest'].read_text(encoding='utf-8'))['run_id']
        key = f"{run}+{policy_id(g1_paths(self.repo)['policy'])}"
        outputs = self.repo / 'data' / 'outputs'
        outputs.mkdir(parents=True, exist_ok=True)
        old = outputs / f'g2_demo_state_{run}.sqlite'
        con = sqlite3.connect(old)
        con.executescript("CREATE TABLE store_meta (key TEXT, value TEXT);"
                          "INSERT INTO store_meta VALUES ('schema_version', '3');")
        con.close()
        before = old.read_bytes()
        at = self.app()
        self.assertFalse(at.error, [e.value for e in at.error])
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(old.read_bytes(), before)
        self.assertTrue((outputs / f'g2_demo_state_{key}_20170101_v{SCHEMA_VERSION}.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
