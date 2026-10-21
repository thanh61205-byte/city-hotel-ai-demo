"""Calls from the Streamlit server (Python) to the n8n Cloud production webhooks.

Only IDs are sent: workflow A gets {request_id, workspace_id}, workflow B {job_id, workspace_id}.
The context, the approved email and the recipient are read by n8n from the database, so a
forged payload cannot change what is drafted or sent. The database is the source of truth: after
the call (answered, timed out or failed) the caller re-reads the request / job by ID. Nothing is
retried automatically; a "check again" re-posts the SAME ID, which the atomic claim makes safe.
"""
from __future__ import annotations

from dataclasses import dataclass

HEADER = 'X-G2-Webhook-Token'


@dataclass
class CallResult:
    ok: bool                 # n8n answered with HTTP 2xx and JSON
    body: dict | None
    error: str | None        # plain-language problem, no secret
    timed_out: bool = False


def post(url: str, token: str, payload: dict, timeout: float, post_fn=None) -> CallResult:
    if post_fn is None:
        import requests
        post_fn = requests.post
    try:
        resp = post_fn(url, json=payload, headers={HEADER: token, 'Content-Type': 'application/json'},
                       timeout=timeout)
    except Exception as exc:
        name = type(exc).__name__
        if 'Timeout' in name:
            return CallResult(False, None, f'n8n did not answer within {timeout:.0f} s. The workflow may still '
                                           'finish: check again (reads the database, does not call the model '
                                           'again).', timed_out=True)
        return CallResult(False, None, f'n8n Cloud could not be reached ({name}). Check that the workflow '
                                       'is published, the production URL is right and the trial / quota '
                                       'is still active. Nothing was sent.')
    status = getattr(resp, 'status_code', 200)
    if status in (401, 403):
        return CallResult(False, None, 'n8n refused the webhook authentication (check G2_AI_WEBHOOK_TOKEN '
                                       'and the Header Auth credential).')
    if status == 404:
        return CallResult(False, None, 'Webhook not found: publish the workflow in n8n and use its '
                                       'Production URL.')
    try:
        body = resp.json()
    except ValueError:
        body = None
    if status >= 400:
        return CallResult(False, body if isinstance(body, dict) else None,
                          f'n8n answered HTTP {status}.' + (f" {body.get('reasons') or body.get('reason') or ''}"
                                                             if isinstance(body, dict) else ''))
    if not isinstance(body, dict):
        return CallResult(False, None, 'n8n answered without a JSON object.')
    return CallResult(True, body, None)


def request_draft(cfg, request_id: str, post_fn=None) -> CallResult:
    return post(cfg.draft_url, cfg.token, {'request_id': str(request_id), 'workspace_id': cfg.workspace_id},
                cfg.draft_timeout, post_fn)


def request_send(cfg, job_id: str, post_fn=None) -> CallResult:
    return post(cfg.send_url, cfg.token, {'job_id': str(job_id), 'workspace_id': cfg.workspace_id},
                cfg.send_timeout, post_fn)
