"""Configuration of the AI email section: environment variables first, then Streamlit Cloud
Secrets (root keys, or the [g2] / [ai_email] sections). Nothing secret is printed or logged.

Keys (see .streamlit/secrets.toml.example):
  G2_AI_DB_URL               shared PostgreSQL (role g2_ai_app, sslmode=require). Falls back to
                             G2_DB_URL. Without it: local SQLite, mock only.
  G2_AI_WORKSPACE_ID         shared demo workspace (default g2-demo), bound to run/policy/date
  G2_AI_DRAFT_WEBHOOK_URL    n8n Cloud production webhook of workflow A (https)
  G2_AI_SEND_WEBHOOK_URL     n8n Cloud production webhook of workflow B (https)
  G2_AI_WEBHOOK_TOKEN        header X-G2-Webhook-Token (n8n Header Auth credential)
  G2_AI_SENDER_REF           label of the Gmail credential in workflow B (default gmail:g2-team-test-sender)
  G2_AI_REAL_EMAIL_ENABLED   "1" to allow real test emails (also needs PostgreSQL, workflow B and
                             an active recipient in the server allowlist). Default: mock only.
  G2_AI_LIVE_AI              "0" to keep n8n on fixtures (no model call). Default "1".
  G2_AI_ACCESS_CODE          optional: code required before drafting / sending (minimal gate when
                             private sharing is not available). Not an identity.
  G2_AI_READ_ONLY            "1": show history only (public read-only deployment)
  G2_AI_DRAFT_TIMEOUT_S / G2_AI_SEND_TIMEOUT_S   HTTP client timeouts (default 45 / 30)
  G2_AI_ALLOW_INSECURE_WEBHOOK  "1" only for local tests with http://localhost webhooks
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE = ROOT / 'data' / 'outputs' / 'g2_ai_email_v1.sqlite'


def setting(name: str, default: str = '') -> str:
    value = os.environ.get(name)
    if value is None:
        try:
            import streamlit as st
            secrets = st.secrets
            for scope in (secrets, secrets.get('g2', {}), secrets.get('ai_email', {})):
                if name in scope:
                    value = str(scope[name])
                    break
        except Exception:  # no secrets file / not running under Streamlit
            value = None
    return (value if value is not None else default).strip()


def _flag(name: str, default: str) -> bool:
    return setting(name, default).lower() in ('1', 'true', 'yes', 'on')


def _webhook_problem(url: str, allow_insecure: bool) -> str | None:
    if not url:
        return None
    u = urlparse(url)
    local = u.hostname in ('localhost', '127.0.0.1', '::1') or (u.hostname or '').endswith('.local')
    if allow_insecure:
        return None
    if local:
        return 'must not point to localhost: the cloud app cannot reach your computer'
    if u.scheme != 'https':
        return 'must be an https:// production webhook of n8n Cloud'
    if '/webhook-test/' in u.path:
        return 'is a Test URL; publish the workflow and use its Production URL'
    return None


@dataclass
class AiConfig:
    db_target: str
    workspace_id: str
    draft_url: str
    send_url: str
    token: str = field(repr=False)
    sender_ref: str
    real_email_enabled: bool
    live_ai: bool
    access_code: str = field(repr=False)
    read_only: bool
    draft_timeout: float
    send_timeout: float
    allow_insecure: bool
    problems: list[str] = field(default_factory=list)

    @property
    def shared_db(self) -> bool:
        return self.db_target.startswith(('postgresql://', 'postgres://'))

    @property
    def n8n_draft(self) -> bool:
        return bool(self.draft_url and self.token and self.shared_db) and not self.read_only

    @property
    def n8n_send(self) -> bool:
        return bool(self.send_url and self.token and self.shared_db) and not self.read_only

    @property
    def real_email(self) -> bool:
        return self.real_email_enabled and self.n8n_send

    def summary(self) -> list[str]:
        """Plain status lines for the UI (no secrets)."""
        return [
            f"State: {'shared PostgreSQL' if self.shared_db else 'local SQLite (offline demo, mock only)'}",
            f"Drafts: {'n8n Cloud workflow A' + (' (live AI)' if self.live_ai else ' (fixtures only)') if self.n8n_draft else 'offline preview (labelled fixtures / manual labels, n8n not called)'}",
            f"Sending: {'n8n workflow B' if self.n8n_send else 'local mock (n8n not called)'}; real test email "
            f"{'enabled' if self.real_email else 'off (mock only)'}",
        ]


def default_sqlite() -> str:
    """Local store: beside the G2 action log when G2_DB_PATH is set (tests), else data/outputs."""
    g2 = setting('G2_DB_PATH')
    return str(Path(g2).parent / DEFAULT_SQLITE.name) if g2 else str(DEFAULT_SQLITE)


def load(as_of=None) -> AiConfig:
    """as_of: the dashboard's business date; without G2_AI_WORKSPACE_ID the local workspace is
    named after it (a workspace is bound to one run / policy / business date)."""
    db = setting('G2_AI_DB_URL') or setting('G2_DB_URL')
    db = db.split('#')[0] if db else ''
    allow_insecure = _flag('G2_AI_ALLOW_INSECURE_WEBHOOK', '0')
    default_ws = f'g2-demo-{as_of:%Y%m%d}' if as_of is not None else 'g2-demo'
    cfg = AiConfig(
        db_target=db or setting('G2_AI_SQLITE_PATH', default_sqlite()),
        workspace_id=setting('G2_AI_WORKSPACE_ID', default_ws).lower(),
        draft_url=setting('G2_AI_DRAFT_WEBHOOK_URL'), send_url=setting('G2_AI_SEND_WEBHOOK_URL'),
        token=setting('G2_AI_WEBHOOK_TOKEN'), sender_ref=setting('G2_AI_SENDER_REF', 'gmail:g2-team-test-sender'),
        real_email_enabled=_flag('G2_AI_REAL_EMAIL_ENABLED', '0'), live_ai=_flag('G2_AI_LIVE_AI', '1'),
        access_code=setting('G2_AI_ACCESS_CODE'), read_only=_flag('G2_AI_READ_ONLY', '0'),
        draft_timeout=float(setting('G2_AI_DRAFT_TIMEOUT_S', '45') or 45),
        send_timeout=float(setting('G2_AI_SEND_TIMEOUT_S', '30') or 30), allow_insecure=allow_insecure)
    for name, url in (('G2_AI_DRAFT_WEBHOOK_URL', cfg.draft_url), ('G2_AI_SEND_WEBHOOK_URL', cfg.send_url)):
        p = _webhook_problem(url, allow_insecure)
        if p:
            cfg.problems.append(f'{name} {p}')
            if name.startswith('G2_AI_DRAFT'):
                cfg.draft_url = ''
            else:
                cfg.send_url = ''
    if (cfg.draft_url or cfg.send_url) and not cfg.token:
        cfg.problems.append('G2_AI_WEBHOOK_TOKEN is missing: n8n webhooks need authentication')
    if (cfg.draft_url or cfg.send_url) and not cfg.shared_db:
        cfg.problems.append('n8n Cloud needs the shared PostgreSQL store (G2_AI_DB_URL): it cannot read a '
                            'local SQLite file')
    if cfg.real_email_enabled and not cfg.n8n_send:
        cfg.problems.append('Real test email requested but workflow B / PostgreSQL / token are not all set: '
                            'staying on mock')
    return cfg
