"""Where the store tests keep their G2 state: a SQLite file, or PostgreSQL when G2_TEST_PG_DSN is set.

    set G2_TEST_PG_DSN=postgresql://user@127.0.0.1:5432/postgres
    python -m unittest discover -s tests -p "test_g2_*.py"

runs the same store tests against PostgreSQL (one fresh schema per store). Tests that are
about SQLite files themselves are skipped there (SQLITE_ONLY).
"""
import os
import unittest
import uuid
from pathlib import Path

PG_DSN = os.environ.get('G2_TEST_PG_DSN', '').strip()
SQLITE_ONLY = unittest.skipIf(bool(PG_DSN), 'about SQLite files; not run against PostgreSQL')


def store_target(tmpdir, name: str = 'store'):
    """A new, empty store location for one test."""
    if PG_DSN:
        return f"{PG_DSN}#t_{uuid.uuid4().hex[:16]}"
    return Path(tmpdir) / f'{name}.sqlite'
