"""Risk Queue page: find and filter open bookings, pick them, and approve routine
recommendations in a batch. Sensitive actions are never approved here (one by one elsewhere).

Approval goes through state_store.review_plans: one unit of work per booking, a final decision
per booking, so a second click or a second session never creates duplicates."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import common, state_store as ss, vocab, workflow as wf
from dashboard.common import LABELS, P_CANCEL, TIER_ORDER, show_flash

ctx = common.ctx()
con, as_of, queue, schedule, manifest = ctx.con, ctx.as_of, ctx.queue, ctx.schedule, ctx.manifest
call_policy = manifest['call_schedule']

st.header('Risk Queue')
st.caption('Bookings scored by today that have not arrived yet. Filter, pick the ones to '
           'handle, then approve their recommended contacts in one go. Open a single booking to '
           'look closer or change its action.')
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

# ---------------------------------------------------------------- filters
with st.container(border=True):
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
    g1, g2, g3, g4 = st.columns(4)
    reviews = g1.multiselect('Review status', [wf.AWAITING, wf.APPROVED, wf.REJECTED, wf.MANUAL,
                                               wf.NO_CONTACT], key='rq_review', placeholder='Any')
    execs = g2.multiselect('Execution status', [wf.NOT_STARTED, wf.SCHEDULED, wf.DUE, wf.FAILED,
                                                wf.AWAITING_APPROVAL, wf.DONE, wf.CLOSED],
                           key='rq_exec', placeholder='Any')
    lo, hi = base['arrival_date'].min(), base['arrival_date'].max()
    arrival = g3.date_input('Check-in between', value=(), min_value=lo, max_value=hi,
                            key=f'rq_arrival_{as_of}', format='YYYY/MM/DD')
    blo, bhi = queue['booking_date'].min(), queue['booking_date'].max()
    booked = g4.date_input('Booked between', value=(), min_value=blo, max_value=bhi,
                           key='rq_booked', format='YYYY/MM/DD')

view = base
if search.strip():
    view = view[view['booking_id'].astype(str).str.contains(search.strip(), regex=False)]
if window != 'Any time':
    days = {'Today': 0, 'Within 3 days': 3, 'Within 7 days': 7}[window]
    view = view[view['days_left'] <= days]
if tiers:
    view = view[view['risk_tier'].isin(tiers)]
if reviews:
    view = view[view['review_status'].isin(reviews)]
if execs:
    view = view[view['execution_status'].isin(execs)]
if len(arrival) == 2:
    view = view[view['arrival_date'].between(*arrival)]
if len(booked) == 2:
    view = view[view['booking_date'].between(*booked)]
order = {'Priority': (['_t', 'expected_revenue_at_risk'], [True, False]),
         'Cancellation risk': (['p_cancel'], [False]), 'Days to check-in': (['days_left'], [True]),
         'Booking value': (['gross_value'], [False])}[sort]
by, ascending = order
view = (view.assign(_t=view['risk_tier'].map(TIER_ORDER))
        .sort_values(by, ascending=ascending).reset_index(drop=True))

n_eligible = int(view['eligible'].sum())
st.markdown(f'**{len(view):,} bookings** match · {n_eligible:,} can be approved in a batch'
            + (f' · {groups:,} group bookings are handled in Capacity planning' if groups else ''))

# ---------------------------------------------------------------- table and selection
cols = ['booking_id', 'arrival_date', 'days_left', 'p_cancel', 'risk_tier', 'gross_value',
        'recommended', 'review_status', 'execution_status']
config = {c: st.column_config.Column(LABELS.get(c, c)) for c in cols}
config.update({'p_cancel': P_CANCEL, 'booking_id': st.column_config.NumberColumn('Booking', format='%d'),
               'gross_value': st.column_config.NumberColumn('Booking value', format='€%.0f'),
               'risk_tier': st.column_config.TextColumn('Risk level'),
               'recommended': st.column_config.TextColumn('Recommended action',
                                                          help='What the contact policy (G1) suggests.'),
               'days_left': st.column_config.NumberColumn('Days to check-in')})
# A new filter result starts with an empty selection (positions would point to other rows).
key = f'rq_table_{as_of}_{hash(tuple(view["booking_id"]))}'
event = st.dataframe(view[cols], hide_index=True, width='stretch', height=420, column_config=config,
                     on_select='rerun', selection_mode='multi-row', key=key)
all_eligible = st.checkbox(f'Select all eligible in this result ({n_eligible:,})', key='rq_all',
                           disabled=not n_eligible,
                           help='Only recommendations awaiting review that can still be carried out '
                                'are selected; the others need a person to look at them.')
picked = view.iloc[sorted(event.selection.rows)] if event.selection.rows else view.iloc[0:0]
if all_eligible:
    picked = view[view['eligible']]

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

if len(picked) == 1 and not all_eligible:
    one = int(picked['booking_id'].iloc[0])
    if st.button(f'Open booking {one} in Action Review', icon=':material/rate_review:'):
        st.switch_page('views/action_review.py', query_params={'booking': str(one)})

# ---------------------------------------------------------------- bulk approval
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
if len(skip):
    with st.expander(f'{len(skip):,} selected bookings are left out of the batch', expanded=len(ok) == 0):
        st.dataframe(skip[['booking_id', 'recommended', 'blocked_reason']].rename(
            columns={'booking_id': 'Booking', 'recommended': 'Recommended action',
                     'blocked_reason': 'Why not in the batch'}), hide_index=True, width='stretch')

reviewer = common.reviewer_input()
pending = st.session_state.get('rq_confirm')
if pending is None:
    if st.button(f'Approve {len(ok):,} recommendations…', type='primary', disabled=not len(ok)):
        if not reviewer.strip():
            st.error('Enter your name as reviewer.')
        else:
            # Freeze what was summarised: the confirmation acts on exactly these bookings.
            st.session_state['rq_confirm'] = {'ids': ok['booking_id'].astype(int).tolist(),
                                              'contacts': len(plans), 'as_of': as_of}
            st.rerun()
else:
    with st.container(border=True):
        st.warning(f"Approve **{len(pending['ids']):,} recommendations** ({pending['contacts']:,} "
                   f"contacts) as {reviewer or 'reviewer'} on {pending['as_of'].isoformat()}? "
                   'This decision is final. Nothing is sent now: contacts run on their dates from '
                   'the Execution page.', icon=':material/fact_check:')
        yes, no = st.columns(2)
        if yes.button('Confirm approval', type='primary'):
            st.session_state.pop('rq_confirm')
            arrival = queue.set_index('booking_id')['arrival_date']
            with st.spinner('Approving…'):
                out = ss.review_plans(
                    con, [(b, schedule[schedule['booking_id'].eq(b)].to_dict('records'), arrival[b])
                          for b in pending['ids']],
                    reviewer=reviewer, approve=True, as_of=pending['as_of'])
            res = pd.DataFrame(out)
            done = res[res['status'].eq('approved')]
            skipped = res[res['status'].eq('skipped')]
            msg = (f"{len(done):,} recommendations approved, {int(done['created'].sum()):,} contacts "
                   f'scheduled.' + (f' {len(skipped):,} skipped.' if len(skipped) else ''))
            body = ({'skipped': [{'booking': int(b), 'reason': vocab.friendly_error(m)}
                                 for b, m in zip(skipped['booking_id'], skipped['message'])]}
                    if len(skipped) else None)
            common.done_and_rerun(msg, body, where='risk_queue')
        if no.button('Cancel'):
            st.session_state.pop('rq_confirm')
            st.rerun()
