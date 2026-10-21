"""Action Review page: find a booking and open it in a popup (why it is prioritised, what the
contact policy recommends, its G1 schedule and actions) to approve / reject / replace; and the
sensitive actions that wait for a case-by-case approval. Every decision goes through
state_store's checks."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import action_panel, common, state_store as ss, ui, vocab, workflow as wf
from dashboard.common import BLOCKED, EUR, TIER_ORDER, done_and_rerun, show_blocked, show_flash, table

ctx = common.ctx()
con, as_of, queue, schedule, by_id = ctx.con, ctx.as_of, ctx.queue, ctx.schedule, ctx.by_id
HERE = 'action_review'

st.header('Action Review')
st.caption('Find a booking and open it to look closely, approve or reject its recommendation, or '
           'change an action. Sensitive actions (change offers, deposit requests, room and price '
           'actions) are approved one at a time under Pending approvals.')
show_flash(HERE)

pending = wf.pending_approvals(con)
# A link from another page (?mode=approvals or ?booking=N) sets what opens, once; afterwards the
# page's own controls decide.
params = st.query_params
if params.get('mode') in ('approvals', 'booking'):
    st.session_state['ar_mode'] = params.get('mode')
    del st.query_params['mode']
if str(params.get('booking', '')).isdigit():
    st.session_state['ar_mode'] = 'booking'
    common.open_dialog(HERE, ('booking', int(params.get('booking'))))
    del st.query_params['booking']
st.session_state.setdefault('ar_mode', 'booking')
mode = st.segmented_control('Review', ['booking', 'approvals'], key='ar_mode',
                            format_func={'booking': 'One booking',
                                         'approvals': f'Pending approvals ({len(pending)})'}.get)


def open_actions_table(acts: pd.DataFrame) -> None:
    show = acts.assign(action_type=acts['action_type'].map(vocab.action),
                       rule_id=acts['rule_id'].map(vocab.rule),
                       approval_status=acts['approval_status'].map(vocab.approval))
    table(show[['action_id', 'action_type', 'rule_id', 'due', 'approval_status', 'execution_status']])


def booking_detail(bid: int) -> None:
    """Everything about one booking: risk, recommendation and G1 schedule, decision, actions."""
    reviewer = common.reviewer_input('ar_reviewer')
    if bid not in by_id.index:
        st.warning(f'Booking {bid} is not in the 2017 queue (not scored, or not eligible when scored).')
        return
    r = by_id.loc[bid]
    if r['track'] != 'individual model':
        st.info('A group booking: it is handled per stay date and room type on the Capacity '
                'planning page.')
        return
    if r['score_time'] > as_of:
        st.info(f"Not scored yet: the risk model scores this booking on {r['score_time'].isoformat()}.")
        return
    individual = ctx.individual
    days_left = (r['arrival_date'] - as_of).days
    review = wf.review_status(con, by_id.loc[[bid]].reset_index(), schedule)[bid]
    st.markdown(f"#### Booking {bid} &nbsp; {ui.risk_badge(r['risk_tier'])} {ui.badge(review)}")
    c = st.columns(3)
    c[0].metric('Check-in', r['arrival_date'].strftime('%d %b'), border=True,
                help=f"Arrival on {r['arrival_date'].isoformat()}.")
    c[1].metric('Days to check-in', f'{days_left:,}' if days_left >= 0 else 'arrived', border=True)
    c[2].metric('Booking value', f"€{r['gross_value']:,.0f}", border=True)
    st.caption(f"Booked {r['booking_date']} · {r['market_segment']} · {r['customer_type']} · room "
               f"{r['room_type']} · {int(r['total_nights'])} nights · deposit: {r['deposit_type']}")

    left, right = st.columns(2)
    with left.container(border=True, height='stretch'):
        st.markdown('**Risk assessment**')
        batch = individual[individual['score_time'].eq(r['score_time'])]
        rank = int((batch['expected_revenue_at_risk'] > r['expected_revenue_at_risk']).sum()) + 1
        st.markdown(f"Cancellation risk **{r['p_cancel']:.0%}**, level "
                    f"**{vocab.TIERS[r['risk_tier']].lower()}**. Revenue at risk "
                    f"{EUR.format(r['expected_revenue_at_risk'])} (risk × booking value): "
                    f"**#{rank} of {len(batch)}** bookings scored on {r['score_time'].strftime('%d %b')}.")
        st.caption("This ranking is why the booking is prioritised. The model gives no per-booking "
                   f"reasons. The model's own default level was {r['f_risk_tier']}.")
    with right.container(border=True, height='stretch'):
        st.markdown(f'**Recommendation** · review status: **{review}**')
        st.markdown(f"**{vocab.rule(r['rule'])}** · {vocab.rule_help(r['rule'])}")
        plan = schedule[schedule['booking_id'].eq(bid)]
        if plan.empty:
            st.caption({'manual_capacity_review': 'No free call slot was left within the call '
                                                  'window: decide manually (propose a call below if '
                                                  'it is still worth it).'}.get(r['schedule_status'],
                                                                                'No contact is planned.'))
        else:
            st.caption('G1 schedule')
            table(plan.assign(action_type=plan['action_type'].map(vocab.action))
                  [['scheduled_at', 'action_type', 'schedule_reason']])
        done = ss.plan_review(con, bid)
        if done:
            st.caption(f"{done['decision'].capitalize()} by {done['reviewer']} on {done['as_of']}"
                       + (f": {done['reason']}" if done['reason'] else '.'))

    # ------------------------------------------------------------ decide the recommendation
    if review == wf.AWAITING:
        blocker = wf.bulk_blocker(r.to_dict() | {'booking_id': bid}, review, as_of,
                                  ctx.manifest['call_schedule'])
        with st.container(border=True):
            st.markdown('**Decide the recommendation**')
            if blocker:
                st.warning(f'{blocker}. Approving would schedule a contact that may no longer be '
                           'possible; consider rejecting it and proposing another action below.',
                           icon=':material/warning:')
            a, b = st.columns(2)
            if a.button('Approve & schedule', type='primary', width='stretch', icon=':material/check:'):
                try:
                    ids, _ = ss.review_plan(con, bid, plan.to_dict('records'),
                                            arrival_date=r['arrival_date'], reviewer=reviewer,
                                            approve=True, as_of=as_of)
                except BLOCKED as e:
                    show_blocked(e)
                else:
                    done_and_rerun(f'Recommendation for booking {bid} approved: {len(ids)} contact(s) '
                                   'scheduled. Nothing is sent now.', where=HERE)
            with b.popover('Reject recommendation…', width='stretch', icon=':material/close:'):
                why = st.text_area('Reason', key='ar_reject_reason',
                                   placeholder='e.g. Guest already confirmed by phone')
                if st.button('Reject', key='ar_reject'):
                    try:
                        ss.review_plan(con, bid, plan.to_dict('records'),
                                       arrival_date=r['arrival_date'], reviewer=reviewer,
                                       approve=False, reason=why, as_of=as_of)
                    except BLOCKED as e:
                        show_blocked(e)
                    else:
                        done_and_rerun(f'Recommendation for booking {bid} rejected. No contact from '
                                       'the contact policy will be scheduled; you may propose '
                                       'another action below.', where=HERE)

    # ------------------------------------------------------------ current actions
    st.markdown('**Actions of this booking**')
    acts = wf.action_status(con, as_of)
    acts = acts[acts['booking_id'].eq(bid)]
    if acts.empty:
        st.caption('No action yet: approve the recommendation, or propose an action below.')
    else:
        open_actions_table(acts)
        labels = dict(zip(acts['action_id'], [f"#{i} · {vocab.action(t)} · {s}" for i, t, s in
                                              zip(acts['action_id'], acts['action_type'],
                                                  acts['execution_status'])]))
        chosen = int(st.selectbox('Open action', list(labels), format_func=labels.get,
                                  key=f'ar_action_{bid}'))
        with st.container(border=True):
            action_panel.render(chosen, where=HERE, reviewer=reviewer)
        status = acts.set_index('action_id').at[chosen, 'execution_status']
        if status in (wf.SCHEDULED, wf.DUE, wf.FAILED):
            with st.expander('Change this action (replace it)'):
                st.caption('The current action is closed without contact and the new one is '
                           'proposed for this booking today. Change offers and deposit requests '
                           'then wait for approval.')
                with st.form(f'ar_replace_{chosen}'):
                    new_type = st.selectbox('Replace with', ss.BOOKING_ACTIONS, format_func=vocab.action)
                    reason = st.text_input('Why', placeholder='e.g. Guest prefers a call')
                    policy = st.checkbox('The hotel has a written deposit policy (deposit request only)')
                    note = st.text_input('Policy clause (deposit request only)')
                    go = st.form_submit_button('Replace action')
                if go:
                    try:
                        _, new = ss.replace_action(con, chosen, new_type, reviewer=reviewer,
                                                   reason=reason, as_of=as_of,
                                                   deposit_policy_enabled=policy,
                                                   risk_tier=r['risk_tier'],
                                                   deposit_type=r['deposit_type'], note=note)
                    except BLOCKED as e:
                        show_blocked(e)
                    else:
                        done_and_rerun(f'Action #{chosen} replaced by #{new} '
                                       f'({vocab.action(new_type)}).', where=HERE)

    # ------------------------------------------------------------ propose another action
    if days_left > 0:
        with st.expander('Propose another action for this booking'):
            with st.form('ar_propose'):
                kind = st.selectbox('Action', ss.BOOKING_ACTIONS, format_func=vocab.action,
                                    index=ss.BOOKING_ACTIONS.index('phone_call'))
                policy = st.checkbox('The hotel has a written deposit policy (deposit request only)',
                                     help='A deposit request (I6) is only for a high-risk booking '
                                          'without deposit, under a written policy.')
                note = st.text_input('Policy clause (deposit request only)')
                go = st.form_submit_button('Propose action')
            if go:
                try:
                    aid = ss.create_action(con, kind, booking_id=bid, decision_at=as_of,
                                           known_from=r['score_time'], arrival_date=r['arrival_date'],
                                           source_run_id=ctx.manifest['policy_key'],
                                           deposit_policy_enabled=policy, risk_tier=r['risk_tier'],
                                           deposit_type=r['deposit_type'], note=note)
                except BLOCKED as e:
                    show_blocked(e)
                else:
                    needs = ss.get_action(con, aid)['approval_status'] == 'pending'
                    done_and_rerun(f'Action #{aid} ({vocab.action(kind)}) proposed'
                                   + (': it waits for approval under Pending approvals.'
                                      if needs else '.'), where=HERE)


@st.dialog('Booking details', width='large', on_dismiss=common.close_dialog)
def booking_dialog(bid: int) -> None:
    booking_detail(bid)


@st.dialog('Action awaiting approval', width='large', on_dismiss=common.close_dialog)
def action_dialog(aid: int) -> None:
    reviewer = common.reviewer_input('ar_reviewer')
    action_panel.render(aid, where=HERE, reviewer=reviewer)


# ================================================================ pending approvals
if mode == 'approvals':
    if not pending:
        st.info('Nothing waits for approval. Sensitive actions appear here when they are proposed: '
                'date actions on the Capacity planning page, offers and deposit requests from a '
                'booking.')
    else:
        acts = wf.action_status(con, as_of)
        acts = acts[acts['approval_status'].eq('pending')].assign(
            target=lambda d: [f'booking {int(b)}' if pd.notna(b) else f'{s} × room {r}'
                              for b, s, r in zip(d['booking_id'], d['stay_date'], d['room_type'])])
        st.caption('Decide each one on its own: approval only allows it to run on its date; it is '
                   'carried out from the Execution page.')
        page_rows = common.page_slice(acts, 'ar_pending')
        clicked = common.item_list(
            page_rows.to_dict('records'),
            [('Action', 0.8, lambda a: f"#{a['action_id']}"),
             ('For', 1.4, lambda a: a['target']),
             ('Action type', 1.4, lambda a: vocab.action(a['action_type'])),
             ('Rule', 1.4, lambda a: vocab.rule(a['rule_id'])),
             ('Due', 1.0, lambda a: str(a['due'])),
             ('Approval', 1.2, lambda a: ui.badge('Awaiting approval'))],
            key='ar_pending', id_field='action_id', button='Decide')
        common.pager(len(acts), 'ar_pending')
        if clicked is not None:
            common.open_dialog(HERE, ('action', int(clicked)))

# ================================================================ one booking: search and list
else:
    individual = ctx.individual
    found = individual[(individual['score_time'] <= as_of) & (individual['arrival_date'] >= as_of)].copy()
    acts_all = wf.action_status(con, as_of)
    found['review_status'] = found['booking_id'].map(wf.review_status(con, found, schedule))
    found['execution_status'] = (found['booking_id'].map(wf.booking_execution(acts_all))
                                 .fillna(wf.NOT_STARTED))
    with st.form('ar_filters'):
        f1, f2, f3, f4 = st.columns(4)
        search = f1.text_input('Booking reference', key='ar_search', placeholder='e.g. 22719')
        reviews = f2.multiselect('Review status', [wf.AWAITING, wf.APPROVED, wf.REJECTED, wf.MANUAL,
                                                   wf.NO_CONTACT], key='ar_review', placeholder='Any')
        execs = f3.multiselect('Execution status', [wf.NOT_STARTED, wf.SCHEDULED, wf.DUE, wf.FAILED,
                                                    wf.AWAITING_APPROVAL, wf.DONE, wf.CLOSED],
                               key='ar_exec', placeholder='Any')
        tiers = f4.multiselect('Risk level', list(TIER_ORDER), format_func=vocab.TIERS.get,
                               key='ar_tiers', placeholder='All levels')
        with st.expander('More filters'):
            g1, g2 = st.columns(2)
            rules = g1.multiselect('Recommendation', sorted(found['rule'].dropna().unique()),
                                   format_func=vocab.rule, key='ar_rule', placeholder='Any')
            if len(found):
                arrival = g2.date_input('Check-in between', value=(), key=f'ar_arrival_{as_of}',
                                        min_value=found['arrival_date'].min(),
                                        max_value=found['arrival_date'].max(), format='YYYY/MM/DD')
            else:
                arrival = ()
        st.form_submit_button('Search Booking', type='primary', icon=':material/search:')

    view = found
    if search.strip():
        view = view[view['booking_id'].astype(str).str.contains(search.strip(), regex=False)]
    if reviews:
        view = view[view['review_status'].isin(reviews)]
    if execs:
        view = view[view['execution_status'].isin(execs)]
    if tiers:
        view = view[view['risk_tier'].isin(tiers)]
    if rules:
        view = view[view['rule'].isin(rules)]
    if len(arrival) == 2:
        view = view[view['arrival_date'].between(*arrival)]
    # Riskiest first: risk level, then revenue at risk (the contact policy's order).
    view = (view.assign(_t=view['risk_tier'].map(TIER_ORDER))
            .sort_values(['_t', 'expected_revenue_at_risk'], ascending=[True, False]))
    st.markdown(f'**{len(view):,} bookings** match. Open one to see its details and decide.')
    if view.empty:
        st.info('No open booking matches this search. Change the filters and press Search Booking.')
    else:
        page_rows = common.page_slice(view, 'ar_list')
        clicked = common.item_list(
            page_rows.to_dict('records'),
            [('Booking', 0.8, lambda b: f"**{int(b['booking_id'])}**"),
             ('Check-in', 1.2, lambda b: b['arrival_date'].strftime('%d %b %Y')),
             ('Risk', 1.0, lambda b: f"{ui.risk_badge(b['risk_tier'])} {b['p_cancel']:.0%}"),
             ('Value', 0.9, lambda b: f"€{b['gross_value']:,.0f}"),
             ('Recommendation', 1.3, lambda b: vocab.rule(b['rule'], with_code=False)),
             ('Review', 1.2, lambda b: ui.badge(b['review_status'])),
             ('Execution', 1.1, lambda b: ui.badge(b['execution_status']))],
            key='ar_open', id_field='booking_id')
        common.pager(len(view), 'ar_list')
        if clicked is not None:
            common.open_dialog(HERE, ('booking', int(clicked)))

# ================================================================ the open popup
opened = common.dialog_item(HERE)
if opened is not None:
    kind, item = opened
    if kind == 'booking' and mode == 'booking':
        booking_dialog(item)
    elif kind == 'action' and mode == 'approvals':
        action_dialog(item)
