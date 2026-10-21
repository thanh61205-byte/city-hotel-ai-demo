"""Hand one action to the n8n demo workflow (n8n/g2_approval_demo_workflow.json).

Order matters: every execution check of the local store runs BEFORE the webhook is called,
so a blocked action (pending, already executed, guest already responded, not due, too late,
same-day conflict, missing checks) never reaches n8n. Checks, the webhook call and the log are
one unit of work under the store's write lock (state_store.execute_action): while n8n is
called no other session can execute or close this action or take the last call slot. The log
is written only if n8n accepts. `idempotency_key` names the action for n8n-side de-duplication. The payload carries the trusted local state
(guest response so far, executed or not) for n8n's own guard; a production workflow must read
that state from PostgreSQL by action_id instead of trusting a payload.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from dashboard import state_store as ss


def build_payload(con: sqlite3.Connection, action: dict, *, as_of: date, outcome: str,
                  precheck: dict) -> dict:
    executed = con.execute('SELECT 1 FROM interventions WHERE action_id = ?',
                           (action['action_id'],)).fetchone() is not None
    response = (ss.booking_stop_outcome(con, action['booking_id'], as_of)
                if action['booking_id'] is not None else None)
    history = (ss.guest_responses(con, action['booking_id'], as_of)
               if action['booking_id'] is not None else [])
    return {  # no guest PII, no secrets
        'mode': 'demo_no_guest_contact', 'as_of': as_of.isoformat(),
        **{k: action[k] for k in ('action_id', 'booking_id', 'stay_date', 'room_type',
                                  'action_type', 'rule_id', 'scheduled_at', 'approval_status',
                                  'decision_at', 'known_from', 'arrival_date', 'source_run_id')},
        'execution_policy': ss.execution_policy(con),
        'needs_approval': bool(action['needs_approval']),
        'already_executed': executed, 'guest_response': response,
        'guest_response_action_type': history[-1]['action_type'] if history else None,
        # I4: the change request the offer was approved for vs the latest one now.
        'offer_basis_event_id': action.get('basis_event_id'),
        'latest_response_event_id': history[-1]['seq'] if history else None,
        'precheck': dict(precheck), 'outcome': outcome,
        'idempotency_key': f"{ss.store_run_id(con)}:{action['action_id']}",
    }


def send_action(con: sqlite3.Connection, action_id: int, *, webhook: str, as_of: date,
                outcome: str, precheck: dict, post=None) -> dict:
    """Pre-check, POST, then log if accepted, in one unit of work. Returns n8n's reply
    (+ intervention_id).

    Raises ValueError / PermissionError (blocked locally: nothing is sent, no attempt logged)
    or the HTTP / network error (no execution; logged as a failed attempt, so it can be retried).
    A refusal by n8n's guard is returned (accepted False) and logged as a refused attempt.
    """
    if post is None:
        import requests
        post = requests.post
    sent = False

    def dispatch(action):
        nonlocal sent
        sent = True  # every local check passed: from here on it is an automation attempt
        resp = post(webhook, json=build_payload(con, action, as_of=as_of, outcome=outcome,
                                                precheck=precheck), timeout=10)
        resp.raise_for_status()
        return resp.json()

    try:
        return ss.execute_action(con, action_id, as_of=as_of, outcome=outcome, precheck=precheck,
                                 executed_by='n8n', dispatch=dispatch)
    except Exception as exc:
        # After the hand-over: network / HTTP errors and an unreadable reply (ValueError) are
        # automation failures; a database refusal while logging is not.
        if sent and not isinstance(exc, sqlite3.Error):
            ss.log_attempt_error(con, action_id, as_of=as_of, detail=f'{type(exc).__name__}: {exc}')
        raise
