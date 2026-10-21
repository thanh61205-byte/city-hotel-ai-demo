"""The PostgreSQL store DDL in sql/ is the one the backend generates (no hand-edited drift)."""
import unittest
from pathlib import Path

from dashboard.pg_backend import pg_schema_sql

SQL = Path(__file__).resolve().parents[1] / 'sql' / 'g2_state_store_postgres.sql'


class TestPgSchemaFile(unittest.TestCase):
    def test_sql_file_matches_generator(self):
        self.assertEqual(SQL.read_text(encoding='utf-8'), pg_schema_sql(),
                         'Run: python -m dashboard.pg_backend')

    def test_same_guards_as_sqlite(self):
        from dashboard.state_store import SCHEMA
        ddl = pg_schema_sql()
        for message in ('Blocked: action lacks required approval', 'Blocked: action was closed without contact',
                        'A response needs an executed contact of this booking on or before it',
                        'An executed action cannot be closed', 'Intervention log is append-only',
                        'Response log is append-only', 'Closure log is append-only',
                        'Response event order is append-only',
                        'Approval decision is final (only pending -> approved/rejected)',
                        'Action identity is immutable', 'Plan review log is append-only',
                        'Execution attempt log is append-only',
                        'Blocked: the G1 plan of this booking was rejected at review'):
            with self.subTest(message=message):
                self.assertIn(message, SCHEMA)
                self.assertIn(message, ddl)


if __name__ == '__main__':
    unittest.main()
