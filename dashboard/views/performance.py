"""Performance evaluation page: is the system useful? Two kinds of figures, kept apart.

Automation performance (the action log, up to today): how many bookings were flagged, approved, contacted,
how many due actions were carried out, how automation did. Outcomes: actual cancellations, only
for bookings whose arrival date is before today, never fed back into the queue. A flagged booking
is not a cancelled one, and a completed contact is not a prevented cancellation: there is no
control group here, so no revenue protected is claimed. Approval turnaround (KPI P3) is not shown:
the log records approvals on the computer's clock, not on the working day (docs/kpi_register.csv)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import common, model_view as mv, state_store as ss, vocab, workflow as wf
from dashboard.common import TIER_ORDER

ctx = common.ctx()
con, as_of, queue = ctx.con, ctx.as_of, ctx.queue
day = as_of.isoformat()

st.header('Performance evaluation')
st.caption('Results up to today, in two parts kept apart. Automation performance counts what the '
           'team and automation did, from the action log. Booking outcomes (cancelled or not) are '
           'known only after the arrival date, so they cover arrived bookings only.')

# ================================================================ automation performance
st.subheader('Automation performance')
st.caption('From the action log up to today: recommendations approved, contacts carried out and '
           'hand-overs to automation (n8n).')
known = queue[queue['track'].eq('individual model') & (queue['score_time'] <= as_of)]
reviews = pd.DataFrame(ss.plan_reviews(con), columns=['decision', 'as_of'])
reviews = reviews[reviews['as_of'] <= day]
acts = wf.action_status(con, as_of)
due = acts[(acts['due'] <= day) & ~acts['execution_status'].isin(
    [wf.AWAITING_APPROVAL, wf.REJECTED_ACTION])]
done = due['execution_status'].isin([wf.SENT, wf.COMPLETED])
attempts = pd.DataFrame(ss.execution_attempts(con), columns=['result', 'as_of'])
attempts = attempts[attempts['as_of'] <= day]

c = st.columns(5)
c[0].metric('Flagged high risk', f"{int(known['risk_tier'].eq('high').sum()):,}", border=True,
            help='High-risk bookings scored up to today (flagged, not cancelled).')
c[1].metric('Approved', f"{int(reviews['decision'].eq('approved').sum()):,}", border=True,
            help=f"Recommendations approved; {int(reviews['decision'].eq('rejected').sum()):,} rejected.")
c[2].metric('Contacts done', f'{int(done.sum()):,}', border=True,
            help='Calls recorded by staff and emails / reminders sent by automation, plus room actions '
                 'carried out.')
c[3].metric('Done when due', '—' if due.empty else f'{done.mean():.1%}', border=True,
            help='Of the approved actions due by today, the share carried out. The rest are still '
                 'open, failed or closed without contact (for example a reminder no longer needed '
                 'because the guest had answered).')
c[4].metric('Automation accepted', '—' if attempts.empty else
            f"{attempts['result'].eq('accepted').mean():.0%}", border=True,
            help=f'Share of hand-overs to automation (n8n) that were accepted ({len(attempts):,} attempts).')

executed = acts[acts['execution_status'].isin([wf.SENT, wf.COMPLETED])]
if not executed.empty:
    weekly = (executed.assign(week=pd.to_datetime(executed['executed_on']).dt.to_period('W-SUN')
                              .dt.start_time.dt.date, type=executed['action_type'].map(vocab.action))
              .pivot_table(index='week', columns='type', values='action_id', aggfunc='count',
                           fill_value=0))
    st.markdown('**Contacts done per week**')
    st.bar_chart(weekly)

# ================================================================ outcomes
st.subheader('Booking outcomes after arrival')
st.caption('Cancelled or not, known only once the arrival date has passed. Never fed back into the '
           'queue, the schedule or the actions.')
try:
    outcome = common.load_outcomes(str(ctx.data_repo), mv.outcome_fingerprint(ctx.data_repo))
except Exception as exc:  # the operational figures above keep working without it
    st.warning(f'Outcomes are not available: {exc}')
    st.stop()
out = mv.arrived(known, outcome, as_of)
if out.empty:
    st.info('No scored booking has reached its arrival date yet. Cancellations appear here once '
            'arrival dates pass.')
    st.stop()
contacted = set(acts.loc[acts['execution_status'].isin([wf.SENT, wf.COMPLETED]), 'booking_id'].dropna())
out = out.assign(contacted=out['booking_id'].isin(contacted))
st.warning('These describe what happened to bookings that have arrived; they are not the effect of '
           'the contacts. Bookings were contacted because of their risk and there is no control '
           'group, so no revenue protected or cancellation prevented is claimed.',
           icon=':material/history:')

c = st.columns(3)
c[0].metric('Arrived bookings', f'{len(out):,}', border=True,
            help='Individual bookings scored by the model with an arrival date before today.')
c[1].metric('Cancelled', f"{out['is_canceled'].mean():.1%}", border=True)
c[2].metric('Predicted', f"{out['p_cancel'].mean():.1%}", border=True,
            help='Mean cancellation probability the model gave these bookings when scored.')

left, right = st.columns(2)
with left:
    st.markdown('**By risk level**')
    tiers = (out.groupby('risk_tier').agg(bookings=('booking_id', 'size'), predicted=('p_cancel', 'mean'),
                                          actual=('is_canceled', 'mean'))
             .reindex(list(TIER_ORDER)).dropna(how='all').reset_index()
             .assign(risk_tier=lambda d: d['risk_tier'].map(vocab.TIERS)))
    st.dataframe(tiers, hide_index=True, width='stretch',
                 column_config={'risk_tier': 'Risk level', 'bookings': 'Bookings',
                                'predicted': st.column_config.NumberColumn('Predicted', format='percent'),
                                'actual': st.column_config.NumberColumn('Actual', format='percent')})
with right:
    st.markdown('**Contacted or not (descriptive)**')
    by_contact = (out.groupby('contacted').agg(bookings=('booking_id', 'size'),
                                               actual=('is_canceled', 'mean'))
                  .reset_index().assign(contacted=lambda d: d['contacted'].map(
                      {True: 'Contact recorded', False: 'No contact recorded'})))
    st.dataframe(by_contact, hide_index=True, width='stretch',
                 column_config={'contacted': 'Group', 'bookings': 'Bookings',
                                'actual': st.column_config.NumberColumn('Actual', format='percent')})
    st.caption('Not comparable groups: contacts went to the riskiest bookings.')
st.page_link('views/model_revenue.py', label='How well the model ranks risk: Model monitoring',
             icon=':material/insights:')
