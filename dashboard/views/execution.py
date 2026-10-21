"""Execution & follow-up page: what is due today and can be done now, what was sent
or completed, what automation refused or failed (retry), and one booking's history. Statuses
come from the log only: nothing shows as sent or done without a recorded execution."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

from dashboard import action_panel, common, state_store as ss, vocab, workflow as wf
from dashboard.ai_email import panel as ai_panel
from dashboard.common import show_flash, table

ctx = common.ctx()
con, as_of, queue, schedule = ctx.con, ctx.as_of, ctx.queue, ctx.schedule
HERE = 'execution'
STATUSES = [wf.DUE, wf.FAILED, wf.SCHEDULED, wf.SENT, wf.COMPLETED, wf.CLOSED,
            wf.AWAITING_APPROVAL, wf.REJECTED_ACTION]

st.header('Execution & follow-up')
st.caption('Carry out approved actions on their date, record what happened, follow up on '
           'automation that refused or failed, and check a booking\'s history.')
show_flash(HERE)

acts = wf.action_status(con, as_of)
calls_left = max(ctx.capacity - ss.calls_made(con, as_of), 0)

# Recommendations for today's contacts that nobody approved yet do not run: say where to go.
today_plan = schedule[schedule['scheduled_at'].eq(as_of)]
waiting = int(wf.review_status(con, queue[queue['booking_id'].isin(today_plan['booking_id'])],
                               schedule).eq(wf.AWAITING).sum())
if waiting:
    with st.container(border=True):
        st.markdown(f'**{waiting:,} bookings with contacts planned for today are still awaiting '
                    'review.** Their contacts appear here once approved.')
        st.page_link('views/risk_queue.py', label='Review them in the Risk Queue',
                     icon=':material/format_list_bulleted:')

if acts.empty:
    st.info('Nothing to carry out yet: approve recommendations in the Risk Queue, or approve '
            'sensitive actions in Action Review.')
    ai_panel.render()
    st.stop()


def detail(r) -> str:
    """What a person needs to know about the action in its current status."""
    s = r['execution_status']
    if s == wf.DUE:
        reason = action_panel.readiness(con, r, as_of)
        return 'Ready' if reason is None else vocab.friendly_error(reason)
    if s == wf.FAILED:
        return f"{vocab.ATTEMPTS.get(r['last_attempt'], r['last_attempt'])}: {r['last_detail'] or ''}"
    if s in (wf.SENT, wf.COMPLETED):
        return f"{vocab.outcome(r['outcome'])} on {r['executed_on']}"
    if s == wf.CLOSED:
        return str(r['closed'])
    if s == wf.SCHEDULED:
        return f"In {(pd.Timestamp(r['due']).date() - as_of).days} days"
    if s == wf.AWAITING_APPROVAL:
        return 'Approve or reject it in Action Review'
    return ''


counts = acts['execution_status'].value_counts()
due = acts[acts['execution_status'].eq(wf.DUE)]
ready = sum(action_panel.readiness(con, r, as_of) is None for r in due.to_dict('records'))
c = st.columns(4)
c[0].metric('Due now', f'{len(due):,}', border=True,
            help=f'{ready:,} can be done now; the others show why not.')
c[1].metric('Failed', f"{counts.get(wf.FAILED, 0):,}", border=True,
            help='Automation refused the action or could not be reached. Nothing was recorded '
                 'as done; retry once fixed.')
c[2].metric('Done', f'{wf.done_between(con, as_of - timedelta(days=6), as_of):,}',
            border=True, help='Recorded in the last 7 days, today included.')
c[3].metric('Calls left', f'{calls_left:,}', border=True,
            help=f'Call slots left today, out of {ctx.capacity} a day.')

# ---------------------------------------------------------------- filters
with st.container(border=True):
    f1, f2, f3, f4 = st.columns([1.6, 1.2, 1, 1])
    statuses = f1.multiselect('Status', STATUSES, default=[wf.DUE, wf.FAILED], key='ex_status',
                              placeholder='Any status')
    kinds = f2.multiselect('Action type', sorted(acts['action_type'].unique()), key='ex_types',
                           format_func=vocab.action, placeholder='Any type')
    search = f3.text_input('Booking reference', key='ex_search', placeholder='e.g. 22719')
    lo = pd.Timestamp(acts['due'].min()).date()
    hi = max(pd.Timestamp(acts['due'].max()).date(), as_of)
    span = f4.date_input('Due between', value=(), min_value=lo, max_value=hi, key='ex_due',
                         format='YYYY/MM/DD')

view = acts
if statuses:
    view = view[view['execution_status'].isin(statuses)]
if kinds:
    view = view[view['action_type'].isin(kinds)]
if search.strip():
    view = view[view['booking_id'].astype('Int64').astype(str).str.contains(search.strip(), regex=False)]
if len(span) == 2:
    view = view[view['due'].between(span[0].isoformat(), span[1].isoformat())]
view = view.assign(
    target=[f'booking {int(b)}' if pd.notna(b) else f'{s} × room {r}'
            for b, s, r in zip(view['booking_id'], view['stay_date'], view['room_type'])],
    status_detail=[detail(r) for r in view.to_dict('records')])
# What needs a person first: failures, then due actions that can run, then by date.
view = view.assign(_o=view['execution_status'].map({s: i for i, s in enumerate(STATUSES)}),
                   _r=~view['status_detail'].eq('Ready')).sort_values(['_o', '_r', 'due', 'action_id'])

st.markdown(f'**{len(view):,} actions** match.')
if view.empty:
    st.info('No action matches these filters. Clear the status filter to see every action in the log.')
else:
    table(view.assign(action_type=view['action_type'].map(vocab.action),
                      rule_id=view['rule_id'].map(lambda r: vocab.rule(r, with_code=False)))
          [['action_id', 'target', 'action_type', 'rule_id', 'due', 'execution_status', 'status_detail']],
          height=min(40 + 35 * len(view), 360))
    labels = dict(zip(view['action_id'], [f'#{i} · {t} · {vocab.action(a)} · {s}' for i, t, a, s in
                                           zip(view['action_id'], view['target'], view['action_type'],
                                               view['execution_status'])]))
    chosen = int(st.selectbox('Open action', list(labels), format_func=labels.get, key='ex_open'))
    with st.container(border=True):
        action_panel.render(chosen, where=HERE, reviewer=common.reviewer_input('ex_reviewer'))

# ---------------------------------------------------------------- one booking's history
st.subheader('Booking history')
# Follows the booking searched above, else the first action listed (or any in the log).
known = view['booking_id'].dropna() if view['booking_id'].notna().any() else acts['booking_id'].dropna()
start = (int(search.strip()) if search.strip().isdigit() else
         int(known.iloc[0]) if len(known) else 0)
bid = st.number_input('Booking reference', min_value=0, step=1, value=start, key=f'ex_history_{start}',
                      help='Everything recorded for this booking up to today.')
history = wf.recent_activity(con, as_of, limit=None, booking_id=int(bid)) if bid else pd.DataFrame()
if history.empty:
    st.caption('Nothing recorded for this booking yet.')
else:
    table(history[['date', 'what', 'who']])

# ---------------------------------------------------------------- AI email drafts (DEMO)
ai_panel.render(default_booking=int(bid) if bid else None)

# ---------------------------------------------------------------- upcoming workload
with st.expander('Workload in the next 14 days'):
    ahead = acts[acts['execution_status'].isin([wf.SCHEDULED, wf.DUE])
                 & acts['due'].between(as_of.isoformat(), (as_of + timedelta(days=14)).isoformat())]
    if ahead.empty:
        st.caption('No approved action falls due in the next 14 days.')
    else:
        st.bar_chart(ahead.assign(type=ahead['action_type'].map(vocab.action))
                     .pivot_table(index='due', columns='type', values='action_id', aggfunc='count',
                                  fill_value=0))
        st.caption(f'Calls are limited to {ctx.capacity} a day; emails and reminders are sent by '
                   'automation.')
