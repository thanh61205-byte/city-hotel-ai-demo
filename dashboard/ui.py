"""Visual tokens and small presentation helpers of the G2 dashboard (no business logic).

The colour roles mirror .streamlit/config.toml: one accent (azulejo blue) for what the user
acts on, red / amber / slate reserved for the risk tiers, green for what is done, and ink for
outcomes known only after arrival, so a colour never means two things.
"""
from __future__ import annotations

from datetime import date
from html import escape

import streamlit as st

TOKENS = {
    'ink': '#14213D',
    'azulejo': '#2457A6',
    'azulejo_light': '#8FA9CF',
    'paper': '#FFFFFF',          # page background
    'surface': '#FFFFFF',        # cards, tables, the Today band
    'surface_alt': '#F3F6FA',    # inputs, table headers, secondary areas
    'line': '#DCE3EC',
    'muted': '#64748B',
    'high': '#B42318',
    'medium': '#B7791F',
    'low': '#64748B',
    'success': '#2F7D4F',
    'info': '#2457A6',
    'radius': '0.5rem',
}
RISK_COLORS = {'high': TOKENS['high'], 'medium': TOKENS['medium'], 'low': TOKENS['low']}

# Badge colour per status text (Streamlit's markdown badge colours). Risk tiers, review and
# approval, execution: one colour per meaning on every page.
BADGE_COLORS = {
    # risk
    'high': 'red', 'medium': 'orange', 'low': 'gray',
    # review / approval
    'Awaiting review': 'orange', 'Awaiting approval': 'orange', 'Approved': 'green',
    'Rejected': 'red', 'Decide manually': 'violet', 'No contact planned': 'gray',
    'No approval needed': 'gray',
    # execution
    'Due': 'blue', 'Failed': 'red', 'Scheduled': 'gray', 'Sent': 'green', 'Completed': 'green',
    'Done': 'green', 'Closed': 'gray', 'Not started': 'gray', 'Ready': 'green',
}

_CSS = f"""
<style>
.block-container {{padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1400px}}
[data-testid="stMainBlockContainer"] h2 {{padding-top: 0.25rem}}
[data-testid="stMainBlockContainer"] h3 {{padding-top: 1rem}}
[data-testid="stMetric"] {{background: {TOKENS['surface']}}}
[data-testid="stMetricValue"] {{font-variant-numeric: tabular-nums}}
[data-testid="stMetricLabel"] p {{color: {TOKENS['muted']}; font-weight: 500}}
[data-testid="stExpander"] details {{border-radius: {TOKENS['radius']}; background: {TOKENS['surface']}}}
[data-testid="stForm"] {{border-radius: {TOKENS['radius']}; background: {TOKENS['surface']}}}
[data-testid="stDataFrame"] {{border-radius: {TOKENS['radius']}}}
button[kind="primary"], button[kind="primaryFormSubmit"] {{font-weight: 600}}
.g2-clock {{display: flex; flex-wrap: wrap; align-items: stretch; gap: 0 2rem;
  border: 1px solid {TOKENS['line']}; border-left: 6px solid {TOKENS['azulejo']};
  border-radius: {TOKENS['radius']}; background: {TOKENS['surface']}; padding: 0.9rem 1.25rem;
  margin: 0.25rem 0 1.25rem}}
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


def badge(text, label: str | None = None) -> str:
    """Markdown for a coloured status badge: `text` picks the colour (BADGE_COLORS), `label` is
    what is shown (default: the text itself). Unknown statuses are grey."""
    if text is None or text != text:  # None / NaN
        return '—'
    shown = str(label if label is not None else text).replace('[', '(').replace(']', ')')
    return f":{BADGE_COLORS.get(str(text), 'gray')}-badge[{shown}]"


def risk_badge(tier) -> str:
    return badge(tier, str(tier).capitalize() if tier == tier and tier is not None else None)


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
