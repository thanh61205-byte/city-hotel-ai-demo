"""One action, everything staff can do with it: approve / reject, see whether it can run today,
execute (staff or n8n), record the guest's response, close without contact. Shared by the Action Review and Execution & follow-up pages; every write goes
through state_store.
"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from dashboard import common, n8n_client, state_store as ss, vocab
from dashboard.common import BLOCKED, done_and_rerun, show_blocked


def readiness(con, action: dict, as_of) -> str | None:
    """None when the action can run on as_of once staff confirm their checks, else the reason.
    Read only: every execution check except the staff confirmations."""
    guest = action['action_type'] in ss.GUEST_FACING
    try:
        ss.check_execution(con, action['action_id'], as_of=as_of,
                           outcome=ss.GUEST_OUTCOMES[0] if guest else ss.DATE_OUTCOMES[0],
                           precheck={k: True for k in ss.PRECHECKS})
    except BLOCKED as e:
        return str(e)
    return None


def render(chosen: int, where: str = 'actions', reviewer: str | None = None) -> None:
    """`where`: the page drawing the panel, which shows the confirmations after a rerun.
    reviewer: the name entered on the page; without it the panel asks for one."""
    ctx = common.ctx()
    con, as_of = ctx.con, ctx.as_of
    a = ss.get_action(con, chosen)
    target = (f"booking {a['booking_id']}" if a['booking_id'] is not None
              else f"{a['stay_date']} × room {a['room_type']}")
    st.markdown(f"**#{a['action_id']} · {vocab.action(a['action_type'])}** for {target}  \n"
                f"Recommendation: {vocab.rule(a['rule_id'])} · decided {a['decision_at']} · due "
                f"{a['scheduled_at'] or a['decision_at']} · approval: "
                f"**{vocab.approval(a['approval_status'])}**",
                help=vocab.rule_help(a['rule_id']) or None)

    if a['approval_status'] == 'pending':
        if reviewer is None:
            reviewer = st.text_input('Reviewer name', placeholder='Revenue manager')
        left, right = st.columns(2)
        for col, label, approve in ((left, 'Approve', True), (right, 'Reject', False)):
            if col.button(f'{label} selected action'):
                try:
                    ss.decide_action(con, chosen, reviewer, approve)
                except ValueError as e:
                    show_blocked(e)
                else:
                    done_and_rerun(f"Action #{chosen} {'approved' if approve else 'rejected'} "
                                   f'by {reviewer}.', where=where)

    st.markdown('**Execute** — today ' + as_of.isoformat())
    guest = a['action_type'] in ss.GUEST_FACING
    # Every check except the staff confirmations below, without writing anything, so
    # the user sees whether and when this action can run before trying.
    blocked = readiness(con, a, as_of)
    if blocked:
        hint = ' Approve or reject it above.' if a['approval_status'] == 'pending' else ''
        st.warning(f'Cannot be done today: {vocab.friendly_error(blocked)}{hint}',
                   icon=':material/block:')
    else:
        st.info('Ready to execute today.'
                + (' Confirm the three staff checks, pick the outcome, then record it.'
                   if guest else ' Pick the outcome, then record it.'),
                icon=':material/check_circle:')
    webhook = os.environ.get('G2_N8N_WEBHOOK_URL', '')
    attempts = ss.execution_attempts(con, chosen)
    failed = bool(attempts) and attempts[-1]['result'] in ('refused', 'error')
    if attempts:
        with st.expander(f'Automation attempts ({len(attempts)})', expanded=failed):
            st.dataframe(pd.DataFrame(attempts).assign(
                result=lambda d: d['result'].map(vocab.ATTEMPTS))[['as_of', 'result', 'detail']]
                .rename(columns={'as_of': 'Date', 'result': 'Result', 'detail': 'Detail'}),
                hide_index=True, width='stretch')
            if failed:
                st.caption('Nothing was recorded as done. Fix the cause (n8n running, guard reason) '
                           'and retry; the same checks run again before anything is sent.')
    with st.form(f'execute_{chosen}'):
        pre = {}
        if guest:
            st.caption('Execution-time checks (decision table §2.4 / §3): not in the data, '
                       'so staff must confirm them.')
            pre['consent_confirmed'] = st.checkbox('Guest has not opted out of contact')
            pre['no_open_conflict'] = st.checkbox('No other open action of this type')
            pre['booking_active'] = st.checkbox('Booking still active in PMS (not cancelled / arrived)')
        outcome = st.selectbox('Outcome', ss.GUEST_OUTCOMES if guest else ss.DATE_OUTCOMES,
                               format_func=vocab.outcome)
        b1, b2 = st.columns(2)
        run_local = b1.form_submit_button('Record execution (staff)')
        run_n8n = b2.form_submit_button('Retry via n8n' if failed else 'Send to automation (n8n)',
                                        disabled=not webhook)
    if not webhook:
        st.caption('Automation (n8n) is not connected (set G2_N8N_WEBHOOK_URL before launching). '
                   'Everything else works without it.')
    if run_local:
        try:
            log_id = ss.record_execution(con, chosen, as_of=as_of, outcome=outcome,
                                         precheck=pre)
        except BLOCKED as e:
            show_blocked(e)
        else:
            done_and_rerun(f'Action #{chosen}: execution #{log_id} recorded '
                           f'({vocab.outcome(outcome)}).', where=where)
    if run_n8n and webhook:
        body = None
        try:
            # All local checks run first; a blocked action is never sent to n8n.
            body = n8n_client.send_action(con, chosen, webhook=webhook, as_of=as_of,
                                          outcome=outcome, precheck=pre)
        except BLOCKED as e:
            show_blocked(e, prefix='Not sent to automation: ')
        except Exception as e:  # network / JSON errors: logged as a failed attempt (retry later)
            st.error(f'Automation (n8n) could not be reached: {e}. Saved as a failed attempt; '
                     'nothing was recorded as done. Try again when n8n is running.')
        if body is not None:
            if body.get('accepted') is True:
                done_and_rerun(f"Action #{chosen}: n8n accepted; execution "
                               f"#{body['intervention_id']} logged by n8n.", body, where=where)
            st.warning('Automation (n8n) refused the action: saved as a refused attempt; nothing '
                       'was recorded as done.')
            st.json(body)
    done = con.execute('SELECT as_of FROM interventions WHERE action_id = ?',
                       (chosen,)).fetchone()
    if guest and done:
        st.markdown(f"**Guest response after the contact** (contact logged {done['as_of']}; "
                    f"response dated today, {as_of.isoformat()})")
        with st.form(f'response_{chosen}'):
            resp = st.selectbox('Response', ss.RESPONSES, format_func=vocab.outcome)
            add_resp = st.form_submit_button('Record guest response')
        if add_resp:
            try:
                rid = ss.record_response(con, chosen, response=resp, response_at=as_of)
            except BLOCKED as e:
                show_blocked(e)
            else:
                done_and_rerun(f'Response #{rid} ({vocab.outcome(resp)}) recorded: later follow-ups of '
                               'this booking stop.', where=where)
    closed = con.execute('SELECT 1 FROM action_closures WHERE action_id = ?', (chosen,)).fetchone()
    if not done and not closed and a['approval_status'] in ('auto', 'approved'):
        with st.expander('Close without contact (e.g. the G1 call window has passed)'):
            st.caption('Use when the action can no longer run as planned. The closure is logged with '
                       'who, why and the date; the action never runs afterwards, no longer '
                       'blocks reminders and is not counted as a call attempt.')
            with st.form(f'close_{chosen}'):
                who = st.text_input('Closed by', value=reviewer or '',
                                    placeholder='Reservations lead')
                why = st.text_input('Reason', placeholder='G1 call window passed; manual review')
                do_close = st.form_submit_button('Close action without contact')
            if do_close:
                try:
                    ss.close_action(con, chosen, reviewer=who, reason=why, as_of=as_of)
                except BLOCKED as e:
                    show_blocked(e)
                else:
                    done_and_rerun(f'Action #{chosen} closed without contact.', where=where)
