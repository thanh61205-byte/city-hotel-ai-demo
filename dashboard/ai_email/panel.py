"""The "AI email drafts (DEMO)" section of the Execution & follow-up page.

Choose a booking and an email type -> see the source data -> generate a draft (n8n Cloud, or a
labelled offline preview) -> read / edit -> approve a revision for mock or the team's test inbox
-> send -> follow the result in the shared log. The model is called only when the button is
clicked; reruns and widget changes never call it. A draft made for another selection (email
type, booking, language, guest message) is never shown as the current draft.
"""
from __future__ import annotations

import hmac
import uuid

import pandas as pd
import streamlit as st

from dashboard import common
from dashboard.ai_email import config, contract as c, dates, fixtures, service
from dashboard.ai_email import store as ai_store

TYPE_HELP = {
    'reminder': 'Pre-arrival reminder from the booking and its existing G1 reminder. No guest message.',
    'booking_confirmation': 'Restates the booking details the hotel holds. No guest message; it does not '
                            'record that the guest confirmed anything.',
    'guest_reply': 'Classifies a guest message (sample or staff transcription) and drafts a reply.',
}
JOB_LABEL = {'queued': 'Queued (not sent)', 'claimed': 'Claimed (not sent yet)',
             'dispatching': 'Dispatching (may have been sent)', 'simulated': 'Simulated (mock, nothing sent)',
             'provider_accepted': 'Accepted by Gmail', 'failed': 'Failed (nothing sent)',
             'unknown': 'Unknown — may have been sent: reconcile, do not resend'}


def _store(cfg) -> ai_store.AiStore:
    key = f'g2_ai_store::{cfg.db_target}'
    s = st.session_state.get(key)
    if s is not None and s.pg and s.con.closed:
        s = None
    if s is None:
        s = service.open_store(cfg)
        st.session_state[key] = s
    return s


def _session_id() -> str:
    return st.session_state.setdefault('g2_ai_session', uuid.uuid4().hex[:12])


def _unlocked(cfg) -> bool:
    if cfg.read_only:
        return False
    if not cfg.access_code:
        return True
    if st.session_state.get('g2_ai_unlocked'):
        return True
    code = st.text_input('Access code for drafting and sending', type='password', key='g2_ai_code',
                         help='Set by the team in the app secrets. Not a login: it only keeps anonymous '
                              'visitors from calling the AI or sending email.')
    if code and hmac.compare_digest(code.encode(), cfg.access_code.encode()):
        st.session_state['g2_ai_unlocked'] = True
        return True
    if code:
        st.error('Wrong access code.')
    return False


def render(default_booking: int | None = None) -> None:
    ctx = common.ctx()
    st.subheader('AI email drafts (DEMO)')
    st.caption('Draft a reminder, a booking confirmation or a reply to a guest message; staff read, edit '
               'and approve before a DEMO test email goes to the team\'s test inbox (or a mock). Nothing '
               'here changes a booking, records a guest response or counts as a G1 contact. One shared '
               'demo workspace: everyone with access to this app sees the same drafts and log.')
    cfg = config.load(as_of=ctx.as_of)
    try:
        store = _store(cfg)
        store.ensure_workspace(cfg.workspace_id, source_run_id=ctx.manifest['policy_key'],
                               policy_version=ctx.manifest['policy_key'].split('+')[-1],
                               business_as_of=ctx.as_of)
    except Exception as exc:  # DB missing / unreachable: the rest of the page still works
        st.warning(f'AI email store not available: {exc}', icon=':material/cloud_off:')
        return
    status_row(cfg)
    with st.expander('Connection status', expanded=bool(cfg.problems)):
        for line in cfg.summary():
            st.markdown(f'- {line}')
        for p in cfg.problems:
            st.warning(p)
    can_act = _unlocked(cfg)
    if cfg.read_only:
        st.info('Read-only deployment: drafting and sending are disabled.', icon=':material/visibility:')

    with st.container(border=True):
        draft_inputs(ctx, cfg, store, can_act, default_booking)
    log_section(store, cfg, can_act)


def status_row(cfg) -> None:
    """Where state lives, how drafts are made and where sends go, as badges (green: live)."""
    items = [
        ('State', 'Shared PostgreSQL' if cfg.shared_db else 'Local SQLite (offline)', cfg.shared_db),
        ('Drafts', ('AI via n8n' if cfg.live_ai else 'n8n fixtures') if cfg.n8n_draft
         else 'Offline preview', bool(cfg.n8n_draft and cfg.live_ai)),
        ('Sending', "Team's test inbox" if cfg.real_email else 'Mock only', bool(cfg.real_email)),
    ]
    line = ' &nbsp; '.join(f"{k}: :{'green' if on else 'gray'}-badge[{v}]" for k, v, on in items)
    if cfg.problems:
        line += f' &nbsp; :orange-badge[{len(cfg.problems)} setting(s) to check]'
    st.markdown(line)


def draft_inputs(ctx, cfg, store, can_act: bool, default_booking: int | None) -> None:
    st.markdown('**1 · Booking and email**')
    left, mid, right = st.columns([1, 1.6, 1])
    known = ctx.queue[ctx.queue['score_time'].le(ctx.as_of) & ctx.queue['arrival_date'].gt(ctx.as_of)]
    start = default_booking if default_booking in set(known['booking_id']) else int(known['booking_id'].iloc[0])
    booking_id = int(left.number_input('Booking', min_value=1, step=1, value=int(start), key='g2_ai_booking',
                                       help='A booking F scored by today whose arrival is still ahead.'))
    email_type = mid.radio('Email type', c.EMAIL_TYPES, format_func=c.EMAIL_TYPE_LABELS.get, horizontal=True,
                           key='g2_ai_type')
    language = right.selectbox('Language', c.LANGUAGES, key='g2_ai_lang',
                               format_func={'en': 'English', 'vi': 'Tiếng Việt'}.get)
    st.caption(TYPE_HELP[email_type])

    message = None
    manual = None
    if email_type == 'guest_reply':
        samples = fixtures.samples()
        options = ['__own__'] + [s['id'] for s in samples]
        pick = st.selectbox('Guest message', options, key='g2_ai_sample',
                            format_func=lambda i: 'Type or paste a message…' if i == '__own__'
                            else next(s['text'] for s in samples if s['id'] == i))
        if pick == '__own__':
            text = st.text_area('Message text', key='g2_ai_message', max_chars=c.MAX_GUEST_MESSAGE,
                                placeholder='What the guest wrote (sample) or what staff noted from the guest')
            source = st.radio('Source', c.MESSAGE_SOURCES, horizontal=True, key='g2_ai_msg_source',
                              format_func={'sample_input': 'Sample input',
                                           'staff_transcription': 'Staff transcription'}.get)
        else:
            text = next(s['text'] for s in samples if s['id'] == pick)
            source = 'sample_input'
        received = st.date_input('Received on (business date, Europe/Lisbon)', value=ctx.as_of,
                                 max_value=ctx.as_of, key='g2_ai_msg_date', format='YYYY/MM/DD')
        if not (text or '').strip():
            st.info('Reply to guest needs the guest message: choose a sample or type one.')
            return
        message = service.guest_message(text, message_business_at=received, source=source)

    try:
        context = service.build_context(queue_by_id=ctx.by_id, schedule=ctx.schedule, con=ctx.con,
                                        as_of=ctx.as_of, manifest=ctx.manifest, workspace_id=cfg.workspace_id,
                                        booking_id=booking_id, email_type=email_type, language=language,
                                        guest_message=message)
    except ValueError as exc:
        st.error(str(exc))
        return
    show_sources(context)
    h = c.context_hash(context)
    current = st.session_state.setdefault('g2_ai_by_context', {})

    st.markdown('**3 · Draft, review and send**')
    if not can_act and not cfg.read_only:
        st.caption('Enter the access code above to draft and send.')
    if can_act:
        modes = []
        if cfg.n8n_draft and cfg.live_ai:
            modes.append('live_ai')
        modes.append('fixture')
        fixture_ok = email_type != 'guest_reply' or fixtures.find_sample(message['text']) is not None
        if email_type == 'guest_reply' and not (cfg.n8n_draft and cfg.live_ai):
            modes.append('manual')
        mode = st.radio('Draft with', modes, horizontal=True, key='g2_ai_mode',
                        format_func={'live_ai': 'AI via n8n', 'fixture': 'Demo fixture (not AI)',
                                     'manual': 'Manual label (not AI)'}.get)
        if mode == 'fixture' and not fixture_ok:
            st.info('This message is not one of the fixture samples. Use AI via n8n, or label it manually.')
        if mode == 'manual':
            a, b = st.columns(2)
            intent = a.selectbox('Intent (your label)', c.INTENTS, format_func=c.INTENT_LABELS.get, key='g2_ai_mi')
            cert = b.selectbox('Certainty', c.CERTAINTIES, key='g2_ai_mc')
            manual = {'intent': intent, 'certainty': cert}
        offline = not cfg.n8n_draft or mode == 'manual'
        label = ('Generate draft with n8n' if not offline else 'Offline preview (n8n not called)')
        if mode != 'live_ai' and offline:
            st.caption('Offline preview: labelled fixture or manual label; no model is called and no n8n '
                       'workflow runs.')
        if st.button(label, type='primary', key='g2_ai_generate',
                     disabled=mode == 'fixture' and not fixture_ok):
            try:
                with st.spinner('Drafting… (n8n Cloud may take a few seconds to wake up)'):
                    req, msg = service.generate(store, cfg, context, mode=mode, manual=manual,
                                                session=st.session_state.get('g2_ai_session') or _session_id(),
                                                actor=st.session_state.get('g2_reviewer_saved') or None)
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                current[h] = req['request_id']
                if msg:
                    st.session_state['g2_ai_flash'] = msg
                st.rerun()
    msg = st.session_state.pop('g2_ai_flash', None)
    if msg:
        st.warning(msg)
    rid = current.get(h)
    if rid is None:
        others = [r for k, r in current.items() if k != h]
        if others:
            st.caption('Selection changed (type, booking, language or message): earlier drafts are not used '
                       'for it. Open them from the shared history below if needed.')
        return
    req = store.get_request(rid)
    if req is not None:
        draft_area(store, cfg, req, can_act)


def show_sources(context: dict) -> None:
    """Step 2: exactly what the draft is built from (read only), one tab per source."""
    b = context['booking']
    st.markdown('**2 · Source data used for this draft**')
    booking_tab, log_tab, policy_tab = st.tabs(['Booking & G1 schedule', 'Action log', 'Policy facts'])
    with booking_tab:
        st.markdown(f"**Business date** {context['business_as_of']} ({context['hotel_timezone']}) · "
                    f"**Booking** {b['booking_id']} · **Arrival** {b['arrival_date']} · "
                    f"**Room type code** {b['room_type_code'] or '— (not in the source)'} · {b['scenario']}")
        if context['schedule']:
            st.dataframe(pd.DataFrame(context['schedule'])[['scheduled_at', 'rule', 'action_type', 'schedule_reason',
                                                             'source_id']], hide_index=True, width='stretch',
                         alt='G1 schedule of this booking')
        else:
            st.caption('G1 has no schedule for this booking.')
    with log_tab:
        if context['g2_actions']:
            st.caption('Its actions in the G2 log (read only; a draft never runs or unlocks them).')
            st.dataframe(pd.DataFrame(context['g2_actions'])[['action_id', 'action_type', 'rule_id', 'scheduled_at',
                                                               'status']], hide_index=True, width='stretch',
                         alt='Actions of this booking in the G2 log')
        else:
            st.caption('No action of this booking in the G2 log yet.')
        if context.get('g2_stop_outcome'):
            st.caption(f"Latest guest response in the G2 log: {context['g2_stop_outcome']}")
    with policy_tab:
        for s in context['sources']:
            if s['kind'] == 'policy':
                st.markdown(f"- `{s['source_id']}` {s['text']}")


def analysis_block(req: dict, rev: dict) -> None:
    ctx = req['context_json']
    st.markdown(f"**{c.ANALYSIS_MODE_LABELS.get(req['analysis_mode'], req['analysis_mode'])}**"
                + (f" · model {req['model']}" if req.get('model') else '')
                + (f" · prompt {req['prompt_version']}" if req.get('prompt_version') else ''))
    if req['email_type'] != 'guest_reply':
        return
    a = rev['analysis_json'] or {}
    gm = ctx['guest_message']
    st.markdown(f"**Guest message** ({gm['source'].replace('_', ' ')}, received {gm['message_business_at']}):")
    st.markdown(f"> {gm['text']}")
    st.markdown(f"**Intent:** {c.INTENT_LABELS.get(a.get('intent'), a.get('intent'))} · "
                f"**certainty:** {a.get('certainty')} · "
                f"**needs clarification:** {'yes' if a.get('requires_clarification') else 'no'}")
    if a.get('intent') == 'cancellation_requested':
        st.caption('A cancellation REQUEST: the booking is not cancelled and no guest response is recorded. '
                   'Staff verify it through the normal process.')
    if a.get('evidence_quotes'):
        st.markdown('**Evidence:** ' + ' · '.join(f'“{q}”' for q in a['evidence_quotes']))
    if a.get('requested_changes'):
        st.dataframe(pd.DataFrame(a['requested_changes']), hide_index=True, width='stretch')
    rel = dates.relative_mentions(gm['text'], pd.Timestamp(gm['message_business_at']).date())
    for r in rel:
        readings = ', '.join(r['readings']) or 'no single date'
        st.caption(f"“{r['text']}” from {gm['message_business_at']}: {readings} — not one date, so the draft "
                   'asks the guest.' if not r['unique'] else f"“{r['text']}” = {readings}")


def draft_area(store, cfg, req: dict, can_act: bool, scope: str = 'cur') -> None:
    st.markdown(f"#### Draft · {c.EMAIL_TYPE_LABELS[req['email_type']]} · booking {req['booking_id']}")
    st.caption(f"Request {req['request_id'][:8]} · {req['engine']} · {req['status']}")
    if req['status'] in ('queued', 'processing'):
        st.info('Waiting for n8n. The result is stored in the database as soon as it exists; checking again '
                'never calls the model a second time.', icon=':material/hourglass_top:')
        if st.button('Check again', key=f"g2_ai_check_{scope}_{req['request_id']}"):
            _, msg = service.retry_draft_call(store, cfg, req['request_id'])
            if msg:
                st.session_state['g2_ai_flash'] = msg
            st.rerun()
        return
    if req['status'] == 'failed':
        st.error(f"No draft: {req['error'] or 'failed'}")
        if req.get('validation_errors'):
            st.caption('; '.join(req['validation_errors']))
        st.caption('Choose "Demo fixture" or "Manual label" and generate again (a new request), or fix the '
                   'n8n / credit problem and regenerate.')
        return
    rev, errors = store.ensure_first_revision(req['request_id'], session=st.session_state.get('g2_ai_session'))
    if rev is None:
        st.error('The stored output failed the server-side check: ' + '; '.join(errors))
        return
    analysis_block(req, rev)
    if rev['missing_information']:
        st.markdown('**Missing information** (does not block sending a question):')
        for m in rev['missing_information']:
            st.markdown(f'- {m}')
    if rev['send_blockers']:
        st.error('Cannot be approved or sent until fixed:\n' +
                 '\n'.join(f"- {b['message']}" for b in rev['send_blockers']), icon=':material/block:')
    st.markdown(f"**Revision {rev['revision_no']}** ({rev['source'].replace('_', ' ')}) · "
                f"content hash `{rev['content_hash'][:12]}`")
    st.markdown(f"**Subject:** {rev['rendered_subject']}")
    st.code(rev['rendered_body'], language=None, wrap_lines=True)
    if not can_act:
        return

    with st.expander('Edit wording (creates a new revision; approvals of older revisions are withdrawn)'):
        with st.form(f"g2_ai_edit_{scope}_{rev['revision_id']}"):
            subj = st.text_input('Subject (the [DEMO] prefix is added automatically)', value=rev['editable_subject'],
                                 max_chars=c.MAX_SUBJECT)
            body = st.text_area('Text (the DEMO lines and the booking-details block are added automatically)',
                                value=rev['editable_body'], height=220, max_chars=c.MAX_BODY,
                                help='Allowed placeholders: ' + ', '.join(f'[[{p}]]' for p in
                                                                          ('booking.booking_id',
                                                                           'booking.arrival_date',
                                                                           'booking.room_type_code',
                                                                           'reminder.scheduled_at',
                                                                           'hotel.name')))
            save = st.form_submit_button('Save as new revision')
        if save:
            try:
                store.create_revision(req['request_id'], base_revision_id=rev['revision_id'], subject=subj,
                                      body=body, actor=st.session_state.get('g2_reviewer_saved') or None,
                                      session=st.session_state.get('g2_ai_session'))
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.rerun()

    approvals_block(store, cfg, rev, scope)


def approvals_block(store, cfg, rev: dict, scope: str) -> None:
    active = [a for a in store.approvals(rev['revision_id']) if a['revoked_at'] is None]
    if not rev['send_blockers']:
        with st.form(f"g2_ai_approve_{scope}_{rev['revision_id']}"):
            reviewer = st.text_input('Reviewer label', value=st.session_state.get('g2_reviewer_saved', ''),
                                     help='A demo label stored with the approval; not a verified identity.')
            modes = ['mock'] + (['team_test_email'] if cfg.real_email else [])
            mode = st.radio('Delivery', modes, horizontal=True,
                            format_func={'mock': 'Mock (nothing sent)',
                                         'team_test_email': "Team's test inbox (real DEMO email)"}.get)
            recipients = store.recipients() if cfg.real_email else []
            rid = st.selectbox('Test inbox (server allowlist)', [r['recipient_id'] for r in recipients],
                               format_func=lambda i: next(f"{r['recipient_id']} — {r['address']}"
                                                          for r in recipients if r['recipient_id'] == i),
                               disabled=not recipients) if cfg.real_email else None
            go = st.form_submit_button(f"Approve revision {rev['revision_no']}")
        if go:
            try:
                store.approve(rev['revision_id'], reviewer=reviewer, delivery_mode=mode,
                              recipient_id=rid if mode == 'team_test_email' else c.MOCK_RECIPIENT_ID,
                              sender_ref=cfg.sender_ref if mode == 'team_test_email' else 'mock',
                              session=st.session_state.get('g2_ai_session'))
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.session_state['g2_reviewer_saved'] = reviewer.strip()
                st.rerun()
    for a in active:
        approval_row(store, cfg, a, scope)


def approval_row(store, cfg, a: dict, scope: str) -> None:
    jobs = store.jobs_for_approval(a['approval_id'])
    with st.container(border=True):
        target = 'mock (nothing sent)' if a['delivery_mode'] == 'mock' else a['recipient_address']
        st.markdown(f"**Approved** by {a['reviewer_label']} for {target} · expires {a['expires_at'][:16]} UTC")
        left, right = st.columns(2)
        job = jobs[-1] if jobs else None
        if job:
            st.markdown(f"Send status: **{JOB_LABEL[job['status']]}**"
                        + (f" · provider ID `{job['provider_message_id']}`" if job['provider_message_id'] else '')
                        + (f" · receipt checked by {job['receipt_verified_by']}" if job['receipt_verified_by'] else ''))
            if job['detail']:
                st.caption(job['detail'])
        send_label = 'Simulate send (mock)' if a['delivery_mode'] == 'mock' else 'Send DEMO test email'
        if job is None or job['status'] == 'queued':
            if left.button(send_label, key=f"g2_ai_send_{scope}_{a['approval_id']}", type='primary'):
                try:
                    with st.spinner('Sending…'):
                        _, msg = service.send(store, cfg, a['approval_id'],
                                              session=st.session_state.get('g2_ai_session'),
                                              actor=st.session_state.get('g2_reviewer_saved') or None)
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    if msg:
                        st.session_state['g2_ai_flash'] = msg
                    st.rerun()
        elif job['status'] in ('claimed', 'dispatching'):
            if left.button('Check status', key=f"g2_ai_status_{scope}_{a['approval_id']}"):
                store.reconcile_stale(cfg.workspace_id)
                st.rerun()
        elif job['status'] == 'failed':
            if left.button('Retry this send (same job, re-checked)', key=f"g2_ai_retry_{scope}_{a['approval_id']}"):
                try:
                    store.retry_failed_job(job['job_id'], actor=st.session_state.get('g2_reviewer_saved') or 'staff')
                    service.send(store, cfg, a['approval_id'], session=st.session_state.get('g2_ai_session'))
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        if job and job['status'] in ('provider_accepted', 'unknown') and not job['receipt_verified_by']:
            who = right.text_input('Checked the test inbox (name)', key=f"g2_ai_rcpt_{scope}_{job['job_id']}")
            if right.button('Mark email received', key=f"g2_ai_rcptb_{scope}_{job['job_id']}"):
                try:
                    store.mark_receipt_verified(job['job_id'], tester=who)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        if job is None or job['status'] in ('queued', 'failed'):
            if right.button('Withdraw approval', key=f"g2_ai_revoke_{scope}_{a['approval_id']}"):
                try:
                    store.revoke(a['approval_id'], actor=st.session_state.get('g2_reviewer_saved') or 'staff',
                                 reason='withdrawn by staff')
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()


def log_section(store, cfg, can_act: bool) -> None:
    with st.expander('Shared history: drafts and DEMO sends'):
        store.reconcile_stale(cfg.workspace_id)
        reqs = store.list_requests(cfg.workspace_id)
        if not reqs:
            st.caption('No draft yet.')
            return
        df = pd.DataFrame(reqs)
        df['email_type'] = df['email_type'].map(c.EMAIL_TYPE_LABELS)
        st.dataframe(df[['created_at', 'email_type', 'booking_id', 'language', 'engine', 'analysis_mode', 'status',
                         'revisions', 'request_id']], hide_index=True, width='stretch')
        pick = st.selectbox('Open a draft', [r['request_id'] for r in reqs], key='g2_ai_hist',
                            format_func=lambda i: next(f"{r['created_at'][:16]} · {c.EMAIL_TYPE_LABELS[r['email_type']]}"
                                                       f" · booking {r['booking_id']} · {r['status']}"
                                                       for r in reqs if r['request_id'] == i))
        if st.toggle('Show this draft', key='g2_ai_hist_show'):
            draft_area(store, cfg, store.get_request(pick), can_act, scope='hist')
        sends = store.send_log(cfg.workspace_id)
        if sends:
            st.markdown('**DEMO sends** (test log only: not interventions, not guest contacts)')
            sl = pd.DataFrame(sends)
            sl['status'] = sl['status'].map(JOB_LABEL)
            st.dataframe(sl[['created_at', 'email_type', 'booking_id', 'revision_no', 'delivery_mode', 'status',
                             'reviewer_label', 'recipient_address', 'provider_message_id', 'detail',
                             'receipt_verified_by']], hide_index=True, width='stretch')
