"""Visual tokens and small presentation helpers of the G2 dashboard (no business logic).

The colour roles mirror .streamlit/config.toml: one accent (azulejo blue) for what the user
acts on, red / amber / slate reserved for the risk tiers, and ink for outcomes known only after
arrival, so a colour never means two things.
"""
from __future__ import annotations

from datetime import date
from html import escape

import streamlit as st

TOKENS = {
    'ink': '#14213D',
    'azulejo': '#2457A6',
    'azulejo_light': '#8FA9CF',
    'paper': '#F6F8FB',
    'line': '#D5DCE6',
    'muted': '#64748B',
    'high': '#B42318',
    'medium': '#B7791F',
    'low': '#64748B',
}
RISK_COLORS = {'high': TOKENS['high'], 'medium': TOKENS['medium'], 'low': TOKENS['low']}

_CSS = f"""
<style>
.block-container {{padding-top: 1.2rem; max-width: 1400px}}
[data-testid="stMetricValue"] {{font-variant-numeric: tabular-nums}}
.g2-clock {{display: flex; flex-wrap: wrap; align-items: stretch; gap: 0 2rem;
  border: 1px solid {TOKENS['line']}; border-left: 6px solid {TOKENS['azulejo']};
  border-radius: 0.375rem; background: #FFFFFF; padding: 0.9rem 1.25rem; margin: 0.25rem 0 1rem}}
.g2-clock .g2-today {{min-width: 15rem}}
.g2-clock .g2-label {{color: {TOKENS['muted']}; font-size: 0.8rem; margin: 0}}
.g2-clock .g2-date {{color: {TOKENS['azulejo']}; font-size: 1.75rem; font-weight: 700;
  line-height: 1.15; margin: 0.1rem 0 0; font-variant-numeric: tabular-nums}}
.g2-clock .g2-sub {{color: {TOKENS['ink']}; font-size: 0.875rem; margin: 0.15rem 0 0}}
.g2-clock .g2-stat {{display: flex; flex-direction: column; justify-content: center; min-width: 9rem;
  padding: 0.25rem 0}}
.g2-clock .g2-value {{color: {TOKENS['ink']}; font-size: 1.25rem; font-weight: 600; margin: 0;
  font-variant-numeric: tabular-nums}}
.g2-bar {{height: 6px; background: {TOKENS['line']}; border-radius: 3px; margin-top: 0.35rem;
  width: 9rem; overflow: hidden}}
.g2-bar > span {{display: block; height: 100%; background: {TOKENS['azulejo']}}}
.g2-bar.g2-full > span {{background: {TOKENS['high']}}}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def today_band(today: date, *, last_batch: date | None, last_batch_size: int, calls_made: int,
               capacity: int, due_today: int, pending: int) -> None:
    """Today, the day every page works from, with the day's call capacity and workload."""
    share = min(calls_made / capacity, 1.0) if capacity else 0.0
    full = ' g2-full' if capacity and calls_made >= capacity else ''
    stats = [
        ('Calls logged today', f'{calls_made} / {capacity}',
         f'<div class="g2-bar{full}"><span style="width:{share:.0%}"></span></div>'),
        ('Planned for today', f'{due_today:,} actions', ''),
        ('Waiting for approval', f'{pending:,}', ''),
    ]
    cells = ''.join(
        f'<div class="g2-stat"><p class="g2-label">{escape(label)}</p>'
        f'<p class="g2-value">{escape(value)}</p>{extra}</div>'
        for label, value, extra in stats)
    if last_batch is None:
        sub = 'No risk scores yet.'
    else:
        when = 'today' if last_batch == today else last_batch.strftime('%a %d %b')
        sub = f'Latest risk scores: {when}, {last_batch_size:,} bookings.'
    st.markdown(
        f'<div class="g2-clock" role="status" aria-label="Today and workload">'
        f'<div class="g2-today"><p class="g2-label">Today</p>'
        f'<p class="g2-date">{today.strftime("%a %d %b %Y")}</p>'
        f'<p class="g2-sub">{escape(sub)}</p></div>'
        f'{cells}</div>',
        unsafe_allow_html=True)
