"""Risk Queue page: the approval queue. Search the open bookings whose recommendation awaits
review, pick them per recommendation, and approve routine recommendations in a batch. Sensitive
actions are never approved here (one by one in Action Review).

Approval goes through state_store.review_plans: one unit of work per booking, a final decision
per booking, so a second click or a second session never creates duplicates."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from dashboard import common, state_store as ss, vocab, workflow as wf
from dashboard.common import EUR, LABELS, P_CANCEL, TIER_ORDER, show_flash, table

ctx = common.ctx()
con, as_of, queue, schedule, manifest = ctx.con, ctx.as_of, ctx.queue, ctx.schedule, ctx.manifest
call_policy = manifest['call_schedule']
# One tab per recommendation of the contact policy (G1).
TABS = {'All recommendations': None, 'Confirmation email': 'I1', 'Reminder plan': 'I2',
        'Personal call': 'I3'}

st.header('Risk Queue')
st.caption('Recommendations awaiting review for bookings scored by today that have not arrived '
           'yet. Search, pick the bookings to handle, then approve their recommended contacts in '
           'one go. Open a single booking in Action Review to look closer or change its action.')
show_flash('risk_queue')

# ---------------------------------------------------------------- data as of today
base = queue[(queue['track'].eq('individual model')) & (queue['score_time'] <= as_of)
             & (queue['arrival_date'] >= as_of)].copy()
groups = int(((queue['track'] != 'individual model') & (queue['score_time'] <= as_of)
              & (queue['arrival_date'] >= as_of)).sum())
actions = wf.action_status(con, as_of)
base['review_status'] = base['booking_id'].map(wf.review_status(con, base, schedule))
base['execution_status'] = base['booking_id'].map(wf.booking_execution(actions)).fillna(wf.NOT_STARTED)
base['days_left'] = [(d - as_of).days for d in base['arrival_date']]
base['recommended'] = base['rule'].map(lambda r: vocab.rule(r, with_code=False))
base['blocked_reason'] = [wf.bulk_blocker(r, r['review_status'], as_of, call_policy)
                          for r in base.to_dict('records')]
base['eligible'] = base['blocked_reason'].isna()
if base.empty:
    st.info('No open booking is known today.')
    st.stop()
queue_rows = base[base['review_status'].eq(wf.AWAITING)]

# ---------------------------------------------------------------- search (applied on submit)
with st.form('rq_filters'):
    f1, f2, f3, f4 = st.columns(4)
    search = f1.text_input('Booking reference', placeholder='e.g. 22719', key='rq_search',
                           help='Search by booking number. Guest names are not in the dataset.')
    window = f2.selectbox('Check-in', ['Any time', 'Today', 'Within 3 days', 'Within 7 days'],
                          key='rq_window')
    tiers = f3.multiselect('Risk level', list(TIER_ORDER), format_func=vocab.TIERS.get,
                           key='rq_tiers', placeholder='All levels')
    sort = f4.selectbox('Sort by', ['Priority', 'Cancellation risk', 'Days to check-in',
                                    'Booking value'], key='rq_sort',
                        help='Priority: risk level, then revenue at risk, the order the contact '
                             'policy works in.')
    with st.expander('More filters'):
        g1, g2, g3 = st.columns(3)
        execs = g1.multiselect('Execution status', [wf.NOT_STARTED, wf.SCHEDULED, wf.DUE, wf.FAILED,
                                                    wf.AWAITING_APPROVAL, wf.DONE, wf.CLOSED],
                               key='rq_exec', placeholder='Any')
        segments = g2.multiselect('Market segment', sorted(base['market_segment'].dropna().unique()),
                                  key='rq_segment', placeholder='Any')
        rooms = g3.multiselect('Room type', sorted(base['room_type'].dropna().unique()),
                               key='rq_room', placeholder='Any')
        h1, h2 = st.columns(2)
        lo, hi = base['arrival_date'].min(), base['arrival_date'].max()
        arrival = h1.date_input('Check-in between', value=(), min_value=lo, max_value=hi,
                                key=f'rq_arrival_{as_of}', format='YYYY/MM/DD')
        blo, bhi = queue['booking_date'].min(), queue['booking_date'].max()
        booked = h2.date_input('Booked between', value=(), min_value=blo, max_value=bhi,
                               key='rq_booked', format='YYYY/MM/DD')
    st.form_submit_button('Search Booking', type='primary', icon=':material/search:')

view = queue_rows
if search.strip():
    view = view[view['booking_id'].astype(str).str.contains(search.strip(), regex=False)]
if window != 'Any time':
    days = {'Today': 0, 'Within 3 days': 3, 'Within 7 days': 7}[window]
    view = view[view['days_left'] <= days]
if tiers:
    view = view[view['risk_tier'].isin(tiers)]
if execs:
    view = view[view['execution_status'].isin(execs)]
if segments:
    view = view[view['market_segment'].isin(segments)]
if rooms:
    view = view[view['room_type'].isin(rooms)]
if len(arrival) == 2:
    view = view[view['arrival_date'].between(*arrival)]
if len(booked) == 2:
    view = view[view['booking_date'].between(*booked)]
by, ascending = {'Priority': (['_t', 'expected_revenue_at_risk'], [True, False]),
                 'Cancellation risk': (['p_cancel'], [False]),
                 'Days to check-in': (['days_left'], [True]),
                 'Booking value': (['gross_value'], [False])}[sort]
view = (view.assign(_t=view['risk_tier'].map(TIER_ORDER))
        .sort_values(by, ascending=ascending).reset_index(drop=True))

others = len(base) - len(queue_rows)
st.markdown(f'**{len(view):,} bookings** awaiting review match · {int(view["eligible"].sum()):,} '
            'can be approved in a batch')
st.caption(f'{others:,} open bookings are not in this queue (already decided, no call slot, or no '
           'contact planned): find them in Action Review.'
           + (f' {groups:,} group bookings are handled in Capacity planning.' if groups else ''))

# ---------------------------------------------------------------- extra calls with free capacity
# Free call slots per day over the next call window: the daily capacity minus the calls already in
# the log for that day (made, due or planned; closed and rejected ones free their slot) and the
# G1 calls planned that day for bookings still awaiting review.
window, min_lead = call_policy['window_days'], call_policy['min_days_before_arrival']
days = [as_of + timedelta(days=i) for i in range(window)]
log_calls = actions[actions['action_type'].eq('phone_call')
                    & ~actions['execution_status'].isin([wf.CLOSED, wf.REJECTED_ACTION])]
used = {d: 0 for d in days}
for due, done_on in zip(log_calls['due'], log_calls['executed_on']):
    day = date.fromisoformat(done_on if isinstance(done_on, str) else due)
    day = max(day, as_of) if not isinstance(done_on, str) else day  # an overdue call is made today
    if day in used:
        used[day] += 1
review_of = base.set_index('booking_id')['review_status']
planned = schedule[schedule['action_type'].eq('phone_call') & schedule['scheduled_at'].isin(days)]
for day, bid in zip(planned['scheduled_at'], planned['booking_id']):
    if review_of.get(bid) == wf.AWAITING:
        used[day] += 1
free = {d: max(ctx.capacity - used[d], 0) for d in days}

# Who could use them: bookings without a call yet (no call slot left by G1, then reminder plans and
# confirmation emails), riskiest first, each on the first day with a free slot that is inside its
# G1 call window and far enough before arrival. Rejected recommendations are left alone.
has_call = set(log_calls['booking_id'].dropna().astype(int))
pool = base[base['review_status'].isin([wf.AWAITING, wf.APPROVED, wf.MANUAL])
            & (base['rule'].ne('I3') | base['review_status'].eq(wf.MANUAL))
            & ~base['booking_id'].isin(has_call)]
pool = (pool.assign(_m=pool['review_status'].ne(wf.MANUAL), _t=pool['risk_tier'].map(TIER_ORDER))
        .sort_values(['_m', '_t', 'expected_revenue_at_risk'], ascending=[True, True, False]))
left = dict(free)
picks = []
for r in pool.itertuples():
    for day in days:
        if (left[day] and (day - r.score_time).days < window
                and (r.arrival_date - day).days >= min_lead):
            left[day] -= 1
            picks.append({'booking_id': int(r.booking_id), 'call_on': day})
            break
    if len(picks) >= 20 or not any(left.values()):
        break
extra = (pd.DataFrame(picks, columns=['booking_id', 'call_on'])
         .merge(base, on='booking_id', how='left'))
n_free = sum(free.values())
with st.expander(f'Extra calls with free call capacity ({n_free:,} free slots in the next {window} days)',
                 icon=':material/call:'):
    st.caption('Bookings that get no call from the contact policy, riskiest first, each with the first '
               'day that has a free call slot inside its call window. A proposed call goes to the '
               'action log on that day like any other call (no approval needed); the recommendation '
               'itself is still reviewed as usual.')
    st.markdown('Free slots: ' + ' · '.join(f"{d:%a %d %b} **{free[d]}**" for d in days))
    if not n_free:
        st.info('No call slot is free in the next days.')
    elif extra.empty:
        st.info('No booking without a call can still be called in a free slot (call window over or '
                'too close to arrival).')
    else:
        table(extra.assign(risk_tier=extra['risk_tier'].map(vocab.TIERS))
              [['booking_id', 'call_on', 'arrival_date', 'p_cancel', 'risk_tier',
                'expected_revenue_at_risk', 'recommended', 'review_status']],
              {'expected_revenue_at_risk': EUR}, height=min(40 + 35 * len(extra), 320))
        when = dict(zip(extra['booking_id'], extra['call_on']))
        with st.form('rq_extra_form'):
            chosen = st.multiselect('Propose a call for', list(when), key='rq_extra',
                                    format_func=lambda b: f'{b} · call on {when[b]:%a %d %b}',
                                    placeholder='Choose bookings')
            go = st.form_submit_button('Propose calls', icon=':material/call:')
        if go and chosen:
            made, refused = [], []
            for b in chosen:
                r = base.set_index('booking_id').loc[b]
                try:
                    made.append(ss.create_action(
                        con, 'phone_call', booking_id=int(b), decision_at=as_of, scheduled_at=when[b],
                        known_from=r['score_time'], arrival_date=r['arrival_date'],
                        source_run_id=manifest['policy_key'], risk_tier=r['risk_tier'],
                        deposit_type=r['deposit_type']))
                except common.BLOCKED as e:
                    refused.append({'booking': int(b), 'reason': vocab.friendly_error(e)})
            common.done_and_rerun(f'{len(made):,} call(s) proposed: they appear in Execution & '
                                  'follow-up on their day.' + (f' {len(refused)} refused.' if refused else ''),
                                  {'refused': refused} if refused else None, where='risk_queue')

with st.expander('How recommendations are made'):
    st.markdown(
        f"Each Sunday the risk model scores the week's new bookings. The contact policy (G1) then "
        f"ranks them: the top {manifest['tier_high_share']:.0%} by revenue at risk get a "
        f"**personal call** (I3), the next ones up to {manifest['tier_contact_share']:.0%} a "
        f"**reminder plan** (I2), the rest a **confirmation email** (I1). Calls are placed within "
        f"{call_policy['window_days']} days, at most {ctx.capacity} a day and at least "
        f"{call_policy['min_days_before_arrival']} days before arrival; a call without a free slot "
        "is left for a manual decision. High-risk bookings arriving within 7 days are not "
        "contacted (I5) and are counted in Capacity planning. Approving a recommendation puts its "
        'contacts in the action log on their planned dates; nothing is sent at approval.')

# ---------------------------------------------------------------- one tab per recommendation
cols = ['booking_id', 'arrival_date', 'days_left', 'p_cancel', 'risk_tier', 'gross_value',
        'expected_revenue_at_risk', 'recommended', 'execution_status']
config = {c: st.column_config.Column(LABELS.get(c, c)) for c in cols}
config.update({'p_cancel': P_CANCEL, 'booking_id': st.column_config.NumberColumn('Booking', format='%d'),
               'gross_value': st.column_config.NumberColumn('Booking value', format='€%.0f'),
               'expected_revenue_at_risk': st.column_config.NumberColumn(
                   'Revenue at risk', format='€%.0f', help='Cancellation risk × booking value.'),
               'risk_tier': st.column_config.TextColumn('Risk level'),
               'recommended': st.column_config.TextColumn('Recommendation',
                                                          help='What the contact policy (G1) suggests.'),
               'days_left': st.column_config.NumberColumn('Days to check-in')})

tabs = st.tabs(list(TABS), key='rq_tab', on_change='rerun')
for tab, (label, code) in zip(tabs, TABS.items()):
    if not tab.open:  # only the open tab is computed
        continue
    with tab:
        rows = view if code is None else view[view['rule'].eq(code)].reset_index(drop=True)
        n_eligible = int(rows['eligible'].sum())
        if rows.empty:
            st.info('No recommendation awaiting review matches this search. Change the filters and '
                    'press Search Booking.')
            st.stop()
        # A new result starts with an empty selection (positions would point to other rows).
        key = f'rq_table_{code}_{as_of}_{hash(tuple(rows["booking_id"]))}'
        event = st.dataframe(rows[cols].assign(risk_tier=rows['risk_tier'].map(vocab.TIERS)),
                             hide_index=True, width='stretch', height=420, column_config=config,
                             on_select='rerun', selection_mode='multi-row', key=key,
                             alt=f'{label}: recommendations awaiting review')
        all_eligible = st.checkbox(f'Select all eligible in this list ({n_eligible:,})',
                                   key='rq_all' if code is None else f'rq_all_{code}',
                                   disabled=not n_eligible,
                                   help='Only recommendations that can still be carried out are '
                                        'selected; the others need a person to look at them.')
        picked = rows.iloc[sorted(event.selection.rows)] if event.selection.rows else rows.iloc[0:0]
        if all_eligible:
            picked = rows[rows['eligible']]

        if len(picked) == 1 and not all_eligible:
            one = int(picked['booking_id'].iloc[0])
            if st.button(f'Open booking {one} in Action Review', icon=':material/rate_review:'):
                st.switch_page('views/action_review.py', query_params={'booking': str(one)})

        # ------------------------------------------------------------ bulk approval
        st.subheader('Bulk approval')
        if picked.empty:
            st.info('Select bookings in the table, or tick "Select all eligible", to approve their '
                    'recommendations together.')
            st.stop()

        ok = picked[picked['eligible']]
        skip = picked[~picked['eligible']]
        plans = schedule[schedule['booking_id'].isin(ok['booking_id'])]
        c = st.columns(4)
        c[0].metric('Selected', f'{len(picked):,}', border=True)
        c[1].metric('In the batch', f'{len(ok):,}', border=True)
        c[2].metric('Left out', f'{len(skip):,}', border=True)
        c[3].metric('Contacts', f'{len(plans):,}', border=True,
                    help='Calls, emails and reminders that approval schedules in the action log.')
        if len(ok):
            mix = (plans.assign(what=plans['action_type'].map(vocab.action))
                   .groupby('what').size().rename('contacts').reset_index())
            st.caption('Approval schedules: ' + ', '.join(f'{n:,} × {w.lower()}' for w, n in
                                                          zip(mix['what'], mix['contacts'])))
            with st.expander(f'Bookings in this batch ({len(ok):,})', expanded=len(ok) <= 10):
                table(ok.assign(risk_tier=ok['risk_tier'].map(vocab.TIERS))
                      [['booking_id', 'p_cancel', 'risk_tier', 'gross_value', 'expected_revenue_at_risk',
                        'recommended']], {'gross_value': EUR, 'expected_revenue_at_risk': EUR},
                      height=min(40 + 35 * len(ok), 320))
        if len(skip):
            with st.expander(f'{len(skip):,} selected bookings are left out of the batch',
                             expanded=len(ok) == 0):
                st.dataframe(skip[['booking_id', 'recommended', 'blocked_reason']].rename(
                    columns={'booking_id': 'Booking', 'recommended': 'Recommendation',
                             'blocked_reason': 'Why not in the batch'}), hide_index=True, width='stretch')

        reviewer = common.reviewer_input()
        pending = st.session_state.get('rq_confirm')
        if pending is None:
            if st.button(f'Approve {len(ok):,} recommendations…', type='primary', disabled=not len(ok),
                         icon=':material/done_all:'):
                if not reviewer.strip():
                    st.error('Enter your name as reviewer.')
                else:
                    # Freeze what was summarised: the confirmation acts on exactly these bookings.
                    st.session_state['rq_confirm'] = {'ids': ok['booking_id'].astype(int).tolist(),
                                                      'contacts': len(plans), 'as_of': as_of}
                    st.rerun()
        else:
            with st.container(border=True):
                st.warning(f"Approve **{len(pending['ids']):,} recommendations** "
                           f"({pending['contacts']:,} contacts) as {reviewer or 'reviewer'} on "
                           f"{pending['as_of'].isoformat()}? This decision is final. Nothing is sent "
                           'now: contacts run on their dates from the Execution page.',
                           icon=':material/fact_check:')
                yes, no = st.columns(2)
                if yes.button('Confirm approval', type='primary', width='stretch'):
                    st.session_state.pop('rq_confirm')
                    arrival_of = queue.set_index('booking_id')['arrival_date']
                    with st.spinner('Approving…'):
                        out = ss.review_plans(
                            con, [(b, schedule[schedule['booking_id'].eq(b)].to_dict('records'),
                                   arrival_of[b]) for b in pending['ids']],
                            reviewer=reviewer, approve=True, as_of=pending['as_of'])
                    res = pd.DataFrame(out)
                    done = res[res['status'].eq('approved')]
                    skipped = res[res['status'].eq('skipped')]
                    msg = (f"{len(done):,} recommendations approved, {int(done['created'].sum()):,} "
                           'contacts scheduled.' + (f' {len(skipped):,} skipped.' if len(skipped) else ''))
                    body = ({'skipped': [{'booking': int(b), 'reason': vocab.friendly_error(m)}
                                         for b, m in zip(skipped['booking_id'], skipped['message'])]}
                            if len(skipped) else None)
                    common.done_and_rerun(msg, body, where='risk_queue')
                if no.button('Cancel', width='stretch'):
                    st.session_state.pop('rq_confirm')
                    st.rerun()
