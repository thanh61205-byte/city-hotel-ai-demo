"""One-off operations for the hosted PostgreSQL (run from a team computer, not by the cloud app).

Every secret comes from environment variables (never from the command line or the chat):
  G2_AI_OWNER_DB_URL        owner / admin connection (schema + roles), sslmode=require
  G2_AI_APP_DB_PASSWORD     password chosen for role g2_ai_app   (--create-roles)
  G2_AI_N8N_DB_PASSWORD     password chosen for role g2_ai_n8n   (--create-roles)
  G2_AI_TEST_RECIPIENTS     team test inboxes: "team=inbox@example.com,lan=lan@example.com"
  G2_DB_URL, G2_TODAY       for --seed-g2-history (same values as the Streamlit secrets)

  python -m dashboard.ai_email.bootstrap --apply-schema --create-roles --recipients --check
  python -m dashboard.ai_email.bootstrap --seed-g2-history      # once before the demo
  python -m dashboard.ai_email.bootstrap --backup output/backup  # before the n8n trial ends

Idempotent: re-running changes nothing that exists. Nothing is ever dropped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

from dashboard.ai_email import store as st

ROOT = Path(__file__).resolve().parents[2]
ROLES_SQL = ROOT / 'sql' / 'g2_ai_email_roles_postgres.sql'


def _env(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        sys.exit(f'{name} is not set (set it in your shell; do not paste secrets into chat or Git)')
    return value


def create_roles(owner: str) -> None:
    import psycopg
    from psycopg import sql
    pw = {'g2_ai_app': _env('G2_AI_APP_DB_PASSWORD'), 'g2_ai_n8n': _env('G2_AI_N8N_DB_PASSWORD')}
    with psycopg.connect(owner, autocommit=True) as con:
        for role, password in pw.items():
            if con.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', (role,)).fetchone() is None:
                con.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),
                                                                             sql.Literal(password)))
                print(f'created role {role}')
        con.execute(ROLES_SQL.read_text(encoding='utf-8').split('-- @grants', 1)[1])
    print('grants applied (g2_ai_app: app tables; g2_ai_n8n: workflow functions only)')


def recipients(owner: str) -> None:
    pairs = []
    for item in _env('G2_AI_TEST_RECIPIENTS').split(','):
        rid, _, address = item.partition('=')
        if not rid.strip() or '@' not in address:
            sys.exit(f'Bad recipient entry {item!r}: use id=address')
        pairs.append((rid.strip().lower(), address.strip()))
    s = st.AiStore(owner)
    try:
        st.upsert_test_recipients(s, pairs)
        print('active test inboxes:', ', '.join(f"{r['recipient_id']}={r['address']}" for r in s.recipients()))
    finally:
        s.close()


def check(url: str) -> None:
    import psycopg
    with psycopg.connect(url.split('#')[0], autocommit=True) as con:
        # Client side (libpq): what matters is that THIS connection is encrypted. pg_stat_ssl on
        # the server would report the hop behind a proxy (e.g. Neon's) and say "off".
        tls = bool(con.pgconn.ssl_in_use)
        ver = con.execute("SELECT value FROM g2_ai_v1.meta WHERE key = 'ai_schema_version'").fetchone()
        print(f"connected as {con.info.user} to {con.info.host}:{con.info.port}/{con.info.dbname}; "
              f"TLS {'on' if tls else 'OFF'}; AI schema {ver[0] if ver else 'missing'}")
        if not tls:
            print('WARNING: the connection is not encrypted; add sslmode=require')


def seed_g2_history() -> None:
    """Build the G2 history up to yesterday once, in the same PostgreSQL schema the app opens."""
    from dashboard import model_view as mv, seed_history, state_store as ss
    from dashboard.data_contract import load_outputs
    url = _env('G2_DB_URL').split('#')[0]
    today = date.fromisoformat(os.environ.get('G2_TODAY', '2017-05-01'))
    queue, schedule, manifest = load_outputs(ROOT)
    key = hashlib.sha256(f"{manifest['policy_key']}_{today:%Y%m%d}".encode()).hexdigest()[:10]
    target = f'{url}#g2_{key}_v{ss.SCHEMA_VERSION}'  # = app.py
    con = ss.connect(target, run_id=manifest['policy_key'], call_capacity=manifest['call_capacity_per_day'],
                     execution_policy=manifest['execution_policy'])
    try:
        if not seed_history.needs_history(con):
            print(f'G2 history already present ({seed_history.status(con)}): nothing written')
            return
        outcome = mv.outcomes(ROOT)
        counts = seed_history.build(con, queue, schedule, manifest['policy_key'], today, outcome,
                                    progress=lambda share, text: print(f'\r{share:5.0%} {text}', end=''))
        print(f'\nG2 history written: {counts}')
    finally:
        con.close()


def backup(url: str, out: Path) -> None:
    """JSON Lines export of every AI table (state survives the n8n trial / provider change)."""
    import psycopg
    from psycopg.rows import dict_row
    out.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(url.split('#')[0], row_factory=dict_row) as con:
        for table in ('workspaces', 'test_recipients', 'generation_requests', 'draft_revisions', 'approvals',
                      'send_jobs', 'events'):
            rows = con.execute(f'SELECT * FROM g2_ai_v1.{table} ORDER BY 1').fetchall()
            path = out / f'g2_ai_v1.{table}.jsonl'
            path.write_text(''.join(json.dumps(r, default=str, ensure_ascii=False) + '\n' for r in rows),
                            encoding='utf-8')
            print(f'{path}: {len(rows)} rows')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Bootstrap the hosted PostgreSQL for AI email drafts')
    ap.add_argument('--apply-schema', action='store_true')
    ap.add_argument('--create-roles', action='store_true')
    ap.add_argument('--recipients', action='store_true', help='set the test inbox allowlist from G2_AI_TEST_RECIPIENTS')
    ap.add_argument('--check', action='store_true', help='TLS + schema check with G2_AI_DB_URL (app role)')
    ap.add_argument('--seed-g2-history', action='store_true')
    ap.add_argument('--backup', metavar='DIR')
    a = ap.parse_args(argv)
    if a.apply_schema or a.create_roles or a.recipients:
        owner = _env('G2_AI_OWNER_DB_URL').split('#')[0]
        if a.apply_schema:
            st.apply_pg_schema(owner)
            print('AI schema g2_ai_v1 applied')
        if a.create_roles:
            create_roles(owner)
        if a.recipients:
            recipients(owner)
    if a.check:
        check(os.environ.get('G2_AI_DB_URL') or _env('G2_AI_OWNER_DB_URL'))
    if a.seed_g2_history:
        seed_g2_history()
    if a.backup:
        backup(os.environ.get('G2_AI_OWNER_DB_URL') or _env('G2_AI_DB_URL'), Path(a.backup))
    if not any(vars(a).values()):
        ap.print_help()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
