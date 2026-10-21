"""Model monitoring page: how well the risk scores ranked the bookings that arrived before today.
Every figure is computed here from those bookings (dashboard/model_view.py); never an action list."""
from __future__ import annotations

import altair as alt
import streamlit as st

from dashboard import common, model_view as mv, vocab
from dashboard.common import EUR, T, TIER_ORDER, table

ctx = common.ctx()
manifest, queue, as_of = ctx.manifest, ctx.queue, ctx.as_of

st.header('Model monitoring')
st.caption('How well the risk scores have ranked the bookings that arrived before today. Each booking '
           'keeps the score of its weekly batch, the one its recommendation came from; whether it '
           'was cancelled is known only after its arrival date.')
with st.expander('How to read these figures'):
    st.markdown(
        f"- **Model:** {manifest['selected_model']} (calibration: {manifest['calibration']}), fitted on "
        f"arrivals up to {manifest['fit_date_final']}. Figures are computed here, from bookings "
        'arrived before today only; they change as more bookings arrive.\n'
        '- **ROC-AUC:** the chance that a cancelled booking scored higher than a kept one (0.5 = '
        'random). **PR-AUC:** precision averaged over all review depths. **Brier:** mean squared gap '
        'between the probability and what happened (lower is better).\n'
        f"- **Top 20%:** the contact policy works on shares of each weekly batch (top "
        f"{manifest['tier_high_share']:.0%} called, up to {manifest['tier_contact_share']:.0%} "
        'contacted), not on a fixed threshold, so precision, recall and lift are read at the top 20% '
        'as in the report.\n'
        '- **Early arrivals:** bookings that have arrived so far were booked closer to their stay '
        'than those still ahead, and short-notice bookings cancel less. Rates can move as the later '
        'arrivals come in.\n'
        '- **Not an effect:** these figures show how well the model ranks risk, not how many '
        'cancellations the contacts prevented. That needs the pilot with a control group '
        '(docs/Pilot_evaluation_plan.md).\n'
        '- **Changing the model or the policy** goes through notebooks 04 and 05 and their checks, '
        'never through this dashboard.')

try:
    outcome = common.load_outcomes(str(ctx.data_repo), mv.outcome_fingerprint(ctx.data_repo))
except Exception as exc:
    st.warning(f'Outcomes are not available: {exc}')
    st.stop()
arrived = mv.arrived(queue, outcome, as_of)
if arrived.empty:
    st.info('No scored booking has reached its arrival date yet: figures appear once bookings arrive.')
    st.stop()

f1, f2 = st.columns(2)
segs = f1.multiselect('Market segment', sorted(arrived['market_segment'].dropna().unique()), key='mv_seg',
                      placeholder='All segments')
rooms = f2.multiselect('Room type', sorted(arrived['room_type'].dropna().unique()), key='mv_room',
                       placeholder='All room types')
view = arrived
if segs:
    view = view[view['market_segment'].isin(segs)]
if rooms:
    view = view[view['room_type'].isin(rooms)]
if view.empty:
    st.warning('No arrived booking matches these filters.')
    st.stop()

m = mv.metrics(view)
k = st.columns(4)
k[0].metric('Arrived bookings', f"{m['bookings']:,}", border=True,
            help=f'Individual bookings scored by the model with an arrival date before {as_of:%d %b %Y}.')
k[1].metric('Cancelled', f"{m['cancel_rate']:.1%}", border=True, help='Cancelled or no-show.')
k[2].metric('Predicted', f"{m['mean_p']:.1%}", border=True,
            help='Mean cancellation probability the model gave these bookings when it scored them.')
k[3].metric('ROC-AUC', f"{m['roc_auc']:.3f}", border=True,
            help='Chance that a cancelled booking scored higher than a kept one (0.5 = random).')
k = st.columns(5)
k[0].metric('PR-AUC', f"{m['pr_auc']:.3f}", border=True,
            help=f"Precision averaged over all review depths; random would be {m['cancel_rate']:.3f}.")
k[1].metric('Brier', f"{m['brier']:.3f}", border=True, help='Lower is better.')
k[2].metric('Precision @20%', f"{m['precision@20%']:.1%}", border=True,
            help='Share cancelled among the 20% of bookings with the highest scores.')
k[3].metric('Recall @20%', f"{m['recall@20%']:.1%}", border=True,
            help='Share of all cancellations found in that top 20%.')
k[4].metric('Lift @20%', f"{m['lift@20%']:.2f}×", border=True,
            help='How many times the overall cancellation rate the top 20% reaches.')

left, right = st.columns(2)
with left.container(border=True):
    st.markdown('**Cancellation rate by risk decile**')
    dec = mv.deciles(view).assign(decile=lambda d: d['risk_decile'].astype(str))
    long = dec.melt(id_vars=['decile', 'bookings'], value_vars=['mean_p', 'cancel_rate'],
                    var_name='series', value_name='rate').replace(
        {'series': {'mean_p': 'predicted', 'cancel_rate': 'actual'}})
    st.altair_chart(
        alt.Chart(long).mark_line(point=True).encode(
            x=alt.X('decile:O', title='Risk decile (1 = highest)', sort=[str(i) for i in range(1, 11)]),
            y=alt.Y('rate:Q', title='Cancellation rate', axis=alt.Axis(format='%')),
            # Ink = outcome known after arrival; blue = what the model said in advance.
            color=alt.Color('series:N', scale=alt.Scale(domain=['predicted', 'actual'],
                                                        range=[T['azulejo'], T['ink']]),
                            legend=alt.Legend(orient='top', title=None)),
            tooltip=['decile', 'bookings', 'series', alt.Tooltip('rate:Q', format='.1%')]).properties(height=260),
        alt='Predicted versus actual cancellation rate by risk decile, arrived bookings')
with right.container(border=True):
    st.markdown('**Cancellation rate by arrival week**')
    week = mv.by_week(view).melt(id_vars=['week', 'bookings'], value_vars=['predicted', 'actual'],
                                 var_name='series', value_name='rate')
    st.altair_chart(
        alt.Chart(week).mark_line(point=True).encode(
            x=alt.X('week:T', title='Arrival week'),
            y=alt.Y('rate:Q', title='Cancellation rate', axis=alt.Axis(format='%')),
            color=alt.Color('series:N', scale=alt.Scale(domain=['predicted', 'actual'],
                                                        range=[T['azulejo'], T['ink']]),
                            legend=alt.Legend(orient='top', title=None)),
            tooltip=[alt.Tooltip('week:T'), 'bookings', 'series', alt.Tooltip('rate:Q', format='.1%')])
        .properties(height=260),
        alt='Predicted and actual cancellation rate by arrival week, arrived bookings')

left, right = st.columns([3, 2])
with left.container(border=True):
    st.markdown('**By risk level and by market segment**')
    tiers = (view.groupby('risk_tier').agg(bookings=('booking_id', 'size'), mean_p=('p_cancel', 'mean'),
                                           cancel_rate=('is_canceled', 'mean'))
             .reindex(list(TIER_ORDER)).dropna(how='all').reset_index()
             .assign(risk_tier=lambda d: d['risk_tier'].map(vocab.TIERS)))
    labels = {'risk_tier': 'Risk level', 'market_segment': 'Segment', 'bookings': 'Bookings',
              'mean_p': 'Predicted', 'cancel_rate': 'Actual'}
    table(tiers.rename(columns=labels), {'Predicted': '{:.1%}', 'Actual': '{:.1%}'})
    table(mv.by_segment(view).rename(columns=labels), {'Predicted': '{:.1%}', 'Actual': '{:.1%}'})
with right.container(border=True):
    st.markdown('**What the top X% by risk reaches**')
    top_pct = st.slider('Review the top X% of bookings by risk', 5, 50, 20, 5, key='mv_top')
    cap = mv.top_share_capture(view, top_pct / 100)
    st.metric('Precision (cancelled among reviewed)', f"{cap['precision']:.1%}", border=True)
    st.metric('Share of cancellations reached', f"{cap['recall']:.1%}", border=True)
    st.metric('Share of cancelled booking value reached', f"{cap['cancelled_gross_reached']:.1%}",
              border=True)
    st.caption(f"{cap['bookings']:,} bookings reviewed; a random choice would reach about {top_pct}%.")

with st.expander('Riskiest 100 arrived bookings, with what happened'):
    show = view.sort_values('p_cancel', ascending=False).head(100)[
        ['booking_id', 'score_time', 'arrival_date', 'market_segment', 'room_type', 'gross_value',
         'p_cancel', 'risk_tier', 'is_canceled']]
    table(show.assign(is_canceled=show['is_canceled'].map({1: 'Yes', 0: 'No'})),
          {'gross_value': EUR}, height=360, alt_text='Riskiest 100 arrived bookings and their outcome')
    st.download_button('Download the arrived bookings (CSV)', view.to_csv(index=False).encode(),
                       file_name='g2_model_monitoring.csv', mime='text/csv', icon=':material/download:')
