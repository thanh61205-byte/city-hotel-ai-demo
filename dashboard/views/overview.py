"""Overview page: what today holds and where to go next, with what happened recently (yesterday
or the last 7 days) one click away. Read only: nothing is approved or recorded here."""
from __future__ import annotations

from datetime import timedelta

import streamlit as st

from dashboard import common, model_view as mv, vocab, workflow as wf
from dashboard.common import table

ctx = common.ctx()
con, as_of, queue, schedule = ctx.con, ctx.as_of, ctx.queue, ctx.schedule
HORIZON = 7  # days ahead for "high-risk arrivals"

st.header('Overview')
st.caption('What needs doing today and where to go next. Nothing is approved or recorded on '
           'this page.')

# ---------------------------------------------------------------- guide (folded once read)
with st.expander('How to use this dashboard', icon=':material/help:',
                 expanded=not st.session_state.get('g2_guide_hidden')):
    st.markdown(
        '1. **Start here.** Every Sunday the risk model scores the week\'s new bookings and the '
        'contact policy recommends a contact for each: a personal call for the highest risk, a '
        'reminder plan, or a confirmation email. Check today\'s work; look back when needed.\n'
        '2. **Approve routine recommendations** in the Risk Queue: search, pick bookings and '
        'approve them in a batch.\n'
        '3. **Carry out what is due** in Execution & follow-up: record each call and the '
        'guest\'s answer; emails and reminders go through automation.\n'
        '4. **Look at one booking or decide sensitive actions** in Action Review: reject or change '
        'a recommendation, and approve change offers, deposit requests and room or price actions '
        '(from Capacity planning) one by one under Pending approvals.')
    st.caption('Two statuses are kept apart everywhere: review (has someone decided on the '
               'recommendation?) and execution (has the action been carried out?). Codes such '
               'as I3 or G2 are the rule names used in the project report.')
    if not st.session_state.get('g2_guide_hidden') and st.button('Got it, fold this guide',
                                                                   key='g2_hide_guide'):
        st.session_state['g2_guide_hidden'] = True
        st.rerun()

# ---------------------------------------------------------------- today
st.subheader('Today')
open_ = wf.open_bookings(queue, as_of)
status = wf.review_status(con, open_, schedule)
open_ = open_.assign(review_status=open_['booking_id'].map(status))
ind = open_[open_['track'].eq('individual model')]
high = ind[ind['risk_tier'].eq('high') & (ind['arrival_date'] <= as_of + timedelta(days=HORIZON))]
soon = open_[open_['arrival_date'] <= as_of + timedelta(days=3)]
awaiting = int(open_['review_status'].eq(wf.AWAITING).sum())
pending = len([a for a in wf.pending_approvals(con) if a['decision_at'] <= as_of.isoformat()])
acts = wf.action_status(con, as_of)
due = acts[acts['execution_status'].eq(wf.DUE)]
due_calls = int(due['action_type'].eq('phone_call').sum())

c = st.columns(4)
c[0].metric('Awaiting a decision', f'{awaiting + pending:,}', border=True,
            help=f'{awaiting:,} contact recommendations to review and {pending:,} sensitive actions '
                 '(change offers, deposit requests, room and price actions) awaiting approval.')
c[1].metric('Due today', f'{len(due):,}', border=True,
            help=f'Approved actions due today or earlier and not done yet: {due_calls:,} calls, '
                 f'{len(due) - due_calls:,} emails, reminders and other actions.')
c[2].metric('High-risk arrivals', f'{len(high):,}', border=True,
            help=f'High-risk bookings arriving in the next {HORIZON} days.')
c[3].metric('Arriving in 3 days', f'{len(soon):,}', border=True,
            help='All open bookings (individual and group) arriving within 3 days.')

# ---------------------------------------------------------------- where to go next
st.subheader('Next step')
NEXT = [
    ('views/risk_queue.py', 'Open Risk Queue', ':material/format_list_bulleted:', None,
     f'{awaiting:,} recommendations wait for review. Search, pick bookings and approve them.'),
    ('views/execution.py', 'Open Execution & follow-up', ':material/send:', None,
     f'{len(due):,} actions are due. Record calls and follow up on automation.'),
    ('views/action_review.py', 'Review pending actions', ':material/approval:', {'mode': 'approvals'},
     f'{pending:,} sensitive actions need a decision one by one in Action Review.'),
    ('views/performance.py', 'Open Performance evaluation', ':material/monitoring:', None,
     'What was done, how automation did, and booking outcomes once guests arrive.'),
]
cols = st.columns(4)
for col, (page, label, icon, params, text) in zip(cols, NEXT):
    with col.container(border=True, height='stretch'):
        st.page_link(page, label=label, icon=icon, query_params=params)
        st.caption(text)

# ---------------------------------------------------------------- coming up / recent
left, right = st.columns([3, 2])
with left:
    st.subheader('Coming up')
    first = high.sort_values(['arrival_date', 'p_cancel'], ascending=[True, False]).head(10)
    if first.empty:
        st.info(f'No high-risk booking arrives in the next {HORIZON} days.')
    else:
        st.caption(f'High-risk bookings arriving first in the next {HORIZON} days.')
        # Days left from today (the queue's days_to_arrival is from the score date).
        table(first.assign(rule=first['rule'].map(lambda r: vocab.rule(r, with_code=False)),
                           days_left=[(d - as_of).days for d in first['arrival_date']])
              [['booking_id', 'arrival_date', 'days_left', 'p_cancel', 'rule', 'review_status']])
with right:
    st.subheader('Recent activity')
    recent = wf.recent_activity(con, as_of)
    if recent.empty:
        st.info('Nothing recorded yet. Start in the Risk Queue: review the latest recommendations.')
    else:
        table(recent)

# ---------------------------------------------------------------- looking back (folded)
PERIODS = {'yesterday': 'Yesterday', 'week': 'Last 7 days'}
with st.expander('Look back: yesterday or the last 7 days', icon=':material/history:'):
    period = st.segmented_control('Period', list(PERIODS), default='yesterday',
                                  key='overview_period', format_func=PERIODS.get) or 'yesterday'
    end = as_of - timedelta(days=1)
    start = end if period == 'yesterday' else as_of - timedelta(days=7)
    span = (f"{end:%a %d %b}" if start == end else f"{start:%d %b} to {end:%d %b}")
    st.markdown(f'**{PERIODS[period]} ({span})**')

    scored = queue[queue['score_time'].between(start, end)]
    high_new = int((scored['track'].eq('individual model') & scored['risk_tier'].eq('high')).sum())
    c = st.columns(4)
    c[0].metric('New bookings scored', f'{len(scored):,}', border=True,
                help='Bookings the risk model scored in the period (weekly batches, individual and group).')
    c[1].metric('High risk', f'{high_new:,}', border=True,
                help='Of these, individual bookings in the high-risk level (personal call or too close to call).')
    c[2].metric('Approved', f'{wf.approved_between(con, start, end):,}', border=True,
                help='Contact recommendations approved in the period. Calls are approved on the day the '
                     'batch is scored (they start that day); emails and reminders may wait for review.')
    c[3].metric('Contacts made', f'{wf.contacts_between(con, start, end):,}', border=True,
                help='Calls recorded by staff and emails / reminders sent by automation in the period.')

    try:
        arrived = mv.arrived(queue, common.load_outcomes(str(ctx.data_repo),
                                                         mv.outcome_fingerprint(ctx.data_repo)),
                             as_of, start)
    except Exception as exc:  # the rest of the page works without outcomes
        st.warning(f'Outcomes of arrived bookings are not available: {exc}')
        arrived = None
    cancelled = arrived[arrived['is_canceled'].eq(1)] if arrived is not None else arrived
    n_cancel = 0 if cancelled is None else len(cancelled)
    flagged = 0 if cancelled is None else int(cancelled['risk_tier'].isin(['high', 'medium']).sum())
    c = st.columns(4)
    c[0].metric('Guests confirmed', f'{wf.confirmed_between(con, start, end):,}', border=True,
                help='Bookings whose guest confirmed the stay in the period, during a call or after an email.')
    c[1].metric('Arrival dates reached', '—' if arrived is None else f'{len(arrived):,}', border=True,
                help='Individual bookings due to arrive in the period.')
    c[2].metric('Cancelled', '—' if arrived is None or arrived.empty else
                f'{n_cancel:,} ({n_cancel / len(arrived):.0%})', border=True,
                help='Of these, cancelled or no-show: known now that the arrival date has passed.')
    c[3].metric('Flagged by the model', f'{flagged:,} of {n_cancel:,}' if n_cancel else '—', border=True,
                help='Of these cancellations, how many the model had put in the high or medium risk level '
                     '(the levels that get a call or reminders) when it scored them.')

st.caption('Revenue protected is not shown: it needs a pilot with a control group (see Performance '
           'evaluation and Model monitoring).')
