"""What the AI email section does, without Streamlit: build the context from the dashboard data,
generate a draft (n8n Cloud or the labelled offline engine), send an approved revision (n8n
workflow B or the local mock executor). The panel and the tests both use these functions.

The G2 store is only READ here (booking's actions, guard status, latest response): a draft, an
approval or a DEMO send never writes action_plan, interventions or guest_responses, so it can
neither unlock I4 nor count as a G1 contact.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from dashboard import state_store as ss
from dashboard.ai_email import client, contract as c, fixtures, store as st


def _d(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else c._iso(v)


def g2_reference(con, booking_id: int, as_of) -> tuple[list[dict], str | None]:
    """The booking's actions in the G2 log with a read-only status, and its stop outcome."""
    out = []
    for a in ss.get_actions(con, booking_id):
        if a['intervention_id'] is not None:
            status = f"executed {a['executed_on']} ({a['outcome']})"
        elif a['closed']:
            status = f"closed: {a['closed']}"
        elif a['approval_status'] in ('pending', 'rejected'):
            status = a['approval_status']
        else:
            try:
                ss.check_execution(con, a['action_id'], as_of=as_of,
                                   outcome=ss.GUEST_OUTCOMES[0] if a['action_type'] in ss.GUEST_FACING
                                   else ss.DATE_OUTCOMES[0], precheck={k: True for k in ss.PRECHECKS})
                status = 'G1 guard: can run today (after staff checks)'
            except (ValueError, PermissionError) as e:
                status = f'G1 guard: not allowed today ({e})'
        out.append({**a, 'status': status})
    return out, ss.booking_stop_outcome(con, booking_id, as_of)


def build_context(*, queue_by_id: pd.DataFrame, schedule: pd.DataFrame, con, as_of, manifest: dict,
                  workspace_id: str, booking_id: int, email_type: str, language: str,
                  guest_message: dict | None = None) -> dict:
    if booking_id not in queue_by_id.index:
        raise ValueError(f'Booking {booking_id} is not in the F / G1 queue')
    row = queue_by_id.loc[booking_id]
    actions, stop = g2_reference(con, booking_id, as_of)
    sched = schedule[schedule['booking_id'].eq(booking_id)].to_dict('records')
    key = manifest['policy_key']
    return c.build_context(
        workspace_id=workspace_id, email_type=email_type, language=language, business_as_of=as_of,
        source_run_id=key, policy_version=key.split('+')[-1],
        booking={'booking_id': int(booking_id), 'arrival_date': _d(row.get('arrival_date')),
                 'room_type_code': row.get('room_type') if isinstance(row.get('room_type'), str) else None,
                 'known_from': _d(row.get('score_time'))},
        schedule_rows=sched, g2_actions=actions, g2_stop_outcome=stop,
        guest_message=guest_message if email_type == 'guest_reply' else None)


def guest_message(text: str, *, message_business_at, source: str) -> dict:
    return {'text': text, 'message_business_at': c._iso(message_business_at), 'source': source,
            'received_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds')}


def offline_producer(manual: dict | None):
    """Fixture (labelled sample) or staff label; never a fake AI prediction."""
    def produce(context):
        if manual:
            return fixtures.manual_output(context, **manual), 'manual', None
        out = fixtures.fixture_output(context)
        if out is None:
            return None, 'fixture', ('This message is not a fixture sample. Without live AI, choose the '
                                     'intent yourself (manual label).')
        return out, 'fixture', None
    return produce


def generate(store: st.AiStore, cfg, context: dict, *, mode: str, manual: dict | None = None,
             session: str | None = None, actor: str | None = None, post_fn=None) -> tuple[dict, str | None]:
    """Create one request and get its draft. mode: live_ai | fixture | manual.
    Returns (request row re-read from the database, message for the user or None)."""
    if mode == 'live_ai' and not (cfg.n8n_draft and cfg.live_ai):
        raise PermissionError('Live AI needs n8n workflow A, the webhook token and the shared database')
    use_n8n = cfg.n8n_draft and mode in ('live_ai', 'fixture')
    req = store.create_request(context, engine='n8n' if use_n8n else 'local', requested_mode=mode,
                               demo_session_id=session, requested_by=actor)
    if not use_n8n:
        store.run_local_generation(req['request_id'], context['workspace_id'], offline_producer(manual))
        return store.get_request(req['request_id']), None
    result = client.request_draft(cfg, req['request_id'], post_fn=post_fn)
    return store.get_request(req['request_id']), result.error


def retry_draft_call(store: st.AiStore, cfg, request_id: str, post_fn=None) -> tuple[dict, str | None]:
    """'Check again' after a timeout / lost response: re-read; if n8n never claimed the request,
    post the SAME request ID again (the claim is atomic, a finished request is only re-read)."""
    store.reconcile_stale(cfg.workspace_id)
    req = store.get_request(request_id)
    if req is None or req['engine'] != 'n8n' or req['status'] != 'queued':
        return req, None
    result = client.request_draft(cfg, request_id, post_fn=post_fn)
    return store.get_request(request_id), result.error


def send(store: st.AiStore, cfg, approval_id: int, *, session: str | None = None, actor: str | None = None,
         post_fn=None) -> tuple[dict, str | None]:
    """Create (or find) the send job of an approval and hand it to workflow B, or to the local
    mock executor when B is not configured. Returns (job re-read from the database, message)."""
    approval = store.get_approval(approval_id)
    if approval is None:
        raise ValueError('Approval not found')
    if approval['delivery_mode'] == 'team_test_email' and not cfg.real_email:
        raise PermissionError('Real test email is not enabled on this deployment')
    job = store.create_send_job(approval_id, requested_by=actor, session=session)
    if job['status'] != 'queued':  # already sent, in progress or finished: only re-read
        return job, None
    if cfg.n8n_send:
        result = client.request_send(cfg, job['job_id'], post_fn=post_fn)
        return store.get_job(job['job_id']), result.error
    store.run_local_mock_send(job['job_id'], cfg.workspace_id)
    return store.get_job(job['job_id']), None


def open_store(cfg) -> st.AiStore:
    return st.AiStore(cfg.db_target)
