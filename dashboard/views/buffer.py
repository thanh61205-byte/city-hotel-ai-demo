"""Capacity planning page: expected cancellations per stay date × room type as known today (group blocks and high-risk bookings too close to call), the dates to watch, and
room / price actions, which are decisions apart from guest reminders and always need a revenue
manager's approval."""
from __future__ import annotations

from datetime import timedelta

import altair as alt
import streamlit as st

from dashboard import common, state_store as ss, vocab
from dashboard.common import BLOCKED, EUR, T, done_and_rerun, show_blocked, show_flash, table
from dashboard.data_contract import BUFFER_SOURCES

ctx = common.ctx()
manifest, con, as_of, buffer = ctx.manifest, ctx.con, ctx.as_of, ctx.buffer

st.header('Capacity planning')
st.caption('Rooms that may free up, per stay date and room type, as known today. '
           'This supports room and price decisions; it is not part of the daily reminder work, and '
           'nothing here is decided automatically.')
show_flash('buffer')
with st.expander('How the expected cancellations are counted'):
    st.markdown('Only bookings the risk model has scored by today and whose arrival is '
                'still ahead. Two sources feed it, as the decision table says: '
                + ' and '.join(BUFFER_SOURCES.values()) + '. Group rows use the group rule rate as '
                'their probability. The sum of probabilities is an expected number of cancelled '
                'bookings, not rooms certain to be resold, and per-night inventory is not checked here.')

if buffer.empty:
    st.info('No group booking or high-risk late booking is known for a future stay date.')
    st.stop()

days = st.segmented_control('Look ahead', [14, 30, 60], default=30, key='cap_days',
                            format_func=lambda d: f'Next {d} days') or 30
ahead = buffer[buffer['arrival_date'] <= as_of + timedelta(days=days)]
b = st.columns(3)
b[0].metric('Bookings counted', f"{ahead['bookings'].sum():,}", border=True)
b[1].metric('Expected cancellations', f"{ahead['expected_cancellations'].sum():.1f}", border=True,
            help='Sum of cancellation probabilities: an expected count, not a forecast of rooms.')
b[2].metric('Booking value at risk', f"€{ahead['expected_revenue_at_risk'].sum():,.0f}", border=True)

if ahead.empty:
    st.info(f'Nothing to plan in the next {days} days.')
else:
    by_day = (ahead.melt(id_vars=['arrival_date'], value_vars=['expected_cancellations_G2',
                                                               'expected_cancellations_I5'],
                         var_name='source', value_name='expected')
              .replace({'source': {'expected_cancellations_G2': vocab.rule('G2'),
                                   'expected_cancellations_I5': vocab.rule('I5')}})
              .groupby(['arrival_date', 'source'], as_index=False)['expected'].sum())
    with st.container(border=True):
        st.markdown('**Expected cancellations by stay date**')
        st.altair_chart(
            alt.Chart(by_day).mark_bar().encode(
                x=alt.X('yearmonthdate(arrival_date):O', title=None, axis=alt.Axis(format='%d %b')),
                y=alt.Y('expected:Q', title='Expected cancellations', stack=True),
                color=alt.Color('source:N', scale=alt.Scale(range=[T['azulejo'], T['high']]),
                                legend=alt.Legend(orient='top', title=None)),
                tooltip=[alt.Tooltip('arrival_date:T', title='Stay date'), 'source',
                         alt.Tooltip('expected:Q', format='.2f')]).properties(height=220),
            alt='Expected cancellations by stay date, group blocks and late high-risk bookings')
    watch = (ahead.groupby('arrival_date', as_index=False)
             .agg(bookings=('bookings', 'sum'), expected_cancellations=('expected_cancellations', 'sum'),
                  expected_revenue_at_risk=('expected_revenue_at_risk', 'sum'))
             .sort_values('expected_cancellations', ascending=False).head(5))
    st.markdown('**Dates to watch:** the most expected cancellations in the period')
    table(watch, {'expected_cancellations': '{:.2f}', 'expected_revenue_at_risk': EUR})
    with st.expander('Every stay date × room type in the period'):
        table(ahead, {'expected_cancellations': '{:.2f}', 'expected_cancellations_G2': '{:.2f}',
                      'expected_cancellations_I5': '{:.2f}', 'gross_value': EUR,
                      'expected_revenue_at_risk': EUR}, height=300)

# ---------------------------------------------------------------- room / price decisions
# What each date action means in practice and what to check before it is carried out.
GUIDE = {
    'capacity_allocation': (
        'Hold rooms back: keep some rooms of this type off general sale for this night, so they '
        'stay available if the expected cancellations do not happen.',
        ['Check in the PMS how many rooms of this type are still free for this night.',
         'Agree how many rooms to hold and until when.',
         'A revenue manager approves it in Action Review before anything changes in the PMS.']),
    'resale': (
        'Release rooms for resale: put rooms back on sale once cancellations have actually freed '
        'them, not on the expectation alone.',
        ['Check in the PMS that the cancellations have really happened for this night.',
         'Release only rooms that are free in the PMS; never sell a room that is still booked.',
         'A revenue manager approves it in Action Review before the rooms go back on sale.']),
    'price_adjustment': (
        'Adjust price: change the rate of this room type for this night. There are no tested '
        'price limits yet, so the change is a manual decision.',
        ['Check current occupancy and the rate in the PMS / channel manager for this night.',
         'Write the proposed new rate and the reason in the approval.',
         'A revenue manager approves it in Action Review before the rate changes anywhere.']),
}

st.subheader('Propose a room or price action')
st.caption('A revenue manager approves it in Action Review (Pending approvals) before it can be '
           'carried out. The figures shown are stored with the proposal. Overbooking is switched '
           'off by policy; price changes have no tested price limits yet. The system does not '
           'recommend these actions from cancellation risk alone.')
keys = [f'{r.arrival_date} | {r.room_type}' for r in buffer.itertuples()]
group_actions = tuple(a for a in ss.DATE_ACTIONS if a != 'overbooking')
p1, p2 = st.columns(2)
pick = p1.selectbox('Stay date | room type', keys, key='cap_pick')
g_type = p2.selectbox('Action', group_actions, index=group_actions.index('capacity_allocation'),
                      format_func=vocab.action, key='cap_action')
stay, room = pick.split(' | ')
cell = buffer[(buffer['arrival_date'].astype(str) == stay) & (buffer['room_type'] == room)].iloc[0]

left, right = st.columns(2)
with left.container(border=True, height='stretch'):
    st.markdown(f'**Stay date {stay} · room {room}**')
    m = st.columns(3)
    m[0].metric('Bookings on this date', f"{int(cell['bookings']):,}")
    m[1].metric('Expected cancellations (this date)', f"{cell['expected_cancellations']:.2f}")
    m[2].metric('Value at risk (this date)', f"€{cell['expected_revenue_at_risk']:,.0f}")
    st.caption(f"Sources: {int(cell['bookings_G2']):,} group bookings "
               f"({cell['expected_cancellations_G2']:.2f} expected cancellations) and "
               f"{int(cell['bookings_I5']):,} high-risk bookings too close to call "
               f"({cell['expected_cancellations_I5']:.2f}).")
    st.caption('Expected cancellations are a sum of cancellation probabilities, not rooms certain '
               'to be free. The system does not check room inventory per night: check the PMS.')
with right.container(border=True, height='stretch'):
    what, steps = GUIDE[g_type]
    st.markdown(f'**{vocab.action(g_type)}**')
    st.markdown(what)
    st.markdown('\n'.join(f'{i}. {step}' for i, step in enumerate(steps, 1)))

if st.button('Propose action (goes to approval)', type='primary', icon=':material/send:'):
    rule = {'capacity_allocation': 'G2', 'resale': 'G2', 'overbooking': 'G3',
            'price_adjustment': 'G5'}[g_type]
    try:
        # Decided today: not before F first knew a booking of this cell,
        # not on or after the stay date.
        snapshot = (f"buffer as of {as_of.isoformat()}: {int(cell['bookings'])} bookings, "
                    f"{cell['expected_cancellations']:.2f} expected cancellations")
        aid = ss.create_action(con, g_type, stay_date=stay, room_type=room, rule_id=rule,
                               source_run_id=manifest['policy_key'], decision_at=as_of,
                               known_from=cell['first_decision'], note=snapshot)
    except BLOCKED as e:
        show_blocked(e)
    else:
        done_and_rerun(f'Action #{aid} ({vocab.action(g_type)}) proposed: it waits for a revenue '
                       'manager in Action Review (Pending approvals).', where='buffer')
