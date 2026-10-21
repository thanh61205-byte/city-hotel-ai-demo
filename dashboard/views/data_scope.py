"""Data scope page: where the data comes from and when it was written, what it covers, which
fields exist and which are missing, the checks it passed, and what the dashboard cannot do with
data (no upload or re-scoring here). Read only, except the reload button."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from dashboard import common
from dashboard.data_contract import source_paths
from dashboard.g1_contract import g1_paths

ctx = common.ctx()
manifest, queue, schedule = ctx.manifest, ctx.queue, ctx.schedule
CAPACITY = ctx.capacity
repo = ctx.data_repo

st.header('Data scope')
st.caption('What the dashboard runs on and what it cannot see. Nothing on this page changes data '
           'or actions.')

with st.container(border=True):
    st.markdown('**This run**')
    st.caption(f"**Risk model (F) run:** `{manifest['run_id']}`  \n**Model:** {manifest['selected_model']} "
               f"(calibration: {manifest['calibration']}), fit up to {manifest['fit_date_final']}")
    st.caption(f"**Contact policy (G1):** calls {manifest['tier_high_share']:.0%} (by "
               f"{manifest['high_rank'].replace('_', ' ')}), contact {manifest['tier_contact_share']:.0%} "
               f"of each weekly batch; at most {CAPACITY} calls/day  \n`{manifest['policy_key']}`")
    st.caption(f'**Today:** {ctx.as_of:%A %d %B %Y}  \n**Action log:** `{ctx.db_label}` (one per '
               "model run, policy and day; it starts with the team's history up to yesterday)")
    if st.button('Reload F / G1 outputs', help='Outputs are also reloaded automatically when the '
                 'files in artifacts/modeling/ or artifacts/business_case/ change.'):
        common.load.clear()
        st.rerun()

# ---------------------------------------------------------------- sources
USE = {
    ('F', 'queue'): 'Weekly risk queue: scores, tiers, rules (operational input)',
    ('F', 'schedule'): "Risk model's default schedule (checked, replaced by G1's)",
    ('F', 'consistency'): "Risk model's cross-file checks (must all pass)",
    ('F', 'manifest'): 'Run identity, model, configuration',
    ('F', 'bookings'): 'Booking facts: deposit, room type, synthetic net value; outcome after '
                       'arrival (Overview, Performance, Model monitoring only)',
    ('G1', 'policy'): 'Frozen contact policy: shares, call window, capacity',
    ('G1', 'queue'): "G1 tiers and rules (re-derived and checked at load)",
    ('G1', 'schedule'): 'Dated contacts, capacity-limited calls (checked at load)',
}
rows = []
for stage, paths in (('F', source_paths(repo)), ('G1', g1_paths(repo))):
    for key, path in paths.items():
        if (stage, key) not in USE:
            continue
        stat = path.stat() if path.exists() else None
        rows.append({'File': str(path.relative_to(repo)) if path.is_relative_to(repo) else path.name,
                     'Used for': USE[(stage, key)],
                     'Size': '—' if stat is None else f'{stat.st_size / 1e6:.1f} MB' if stat.st_size > 1e6
                     else f'{stat.st_size / 1e3:.0f} KB',
                     'Written': '—' if stat is None else
                     datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')})
st.subheader('Sources')
st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')

# ---------------------------------------------------------------- coverage and checks
left, right = st.columns(2)
with left.container(border=True):
    st.markdown('**Coverage**')
    ind = queue['track'].eq('individual model')
    st.markdown(
        f"- Scored weeks: {queue['score_time'].nunique()} ({queue['score_time'].min()} to "
        f"{queue['score_time'].max()})\n"
        f"- Arrivals: {queue['arrival_date'].min()} to {queue['arrival_date'].max()}\n"
        f"- Bookings: {int(ind.sum()):,} individual, {int((~ind).sum()):,} group\n"
        f"- Planned contacts: {len(schedule):,}; calls without a slot: "
        f"{manifest['manual_capacity_review']:,}\n"
        '- Hotel: City Hotel (Lisbon), public research dataset')
with right.container(border=True):
    st.markdown('**Checks at load**')
    checks = pd.read_csv(source_paths(repo)['consistency'])
    passed = checks['passed'].astype(str).str.lower().eq('true')
    st.markdown(
        f"- Risk model cross-file checks: **{int(passed.sum())} of {len(checks)} passed**\n"
        '- Contact policy built on the same model run: **passed**\n'
        '- Every G1 tier, rule and dated contact re-derived from the scores: **passed**\n'
        '- No outcome known later in the operational queue: **passed**')
    st.caption('The dashboard does not start if any of these fails, and says which one.')
    with st.expander('Risk model checks'):
        st.dataframe(checks.rename(columns={'check': 'Check', 'passed': 'Passed'}),
                     hide_index=True, width='stretch')

st.subheader('Fields')
f1, f2 = st.columns(2)
with f1.container(border=True):
    st.markdown('**Available**')
    st.markdown('Booking reference, booking and arrival dates, nights, market segment, customer '
                'type, room type, deposit type, gross booking value, cancellation probability, '
                'risk level, recommended rule, planned contact dates.')
with f2.container(border=True):
    st.markdown('**Not in the data**')
    st.markdown('- Guest name and contact details\n- Consent or opt-out\n- Other open actions in '
                'the hotel system\n- Current booking status\n- Message content and channel '
                'preference\n- Room inventory per night')
    st.caption('Staff confirm consent, open actions and status when they record a contact.')

with st.container(border=True):
    st.markdown('**Data tools**')
    st.markdown('Uploading a CSV or re-scoring bookings is **not available in this dashboard**: '
                'scores come from notebook 04 (risk model) and notebook 05 (contact policy), which '
                'need the full modelling stack (`requirements.txt`). Re-run them '
                '(`python src/run_notebooks.py --stage F`, then `--stage G1`) and reload. New data '
                'never creates actions by itself: its recommendations still wait for review in the '
                'Risk Queue, and a new run opens a new action log.')

st.markdown(f"""
### How the data is used
One F run: `{manifest['run_id']}` — {manifest['selected_model']} model, fit on arrivals up
to {manifest['fit_date_final']}, read from `artifacts/modeling/` (F's `output_consistency.csv`
checks must all pass). Decision time: {manifest['decision_time']}.

One G1 policy: `{manifest['policy_key']}` from `artifacts/business_case/` ({manifest['g1_chosen_on']}).
The dashboard re-derives G1's tiers from F's scores with that policy, its rules from the tiers,
and checks every dated action (I1 / I2 milestones; I3 calls within the window, at most
{CAPACITY} a day). G1 must have been built on the same F run, otherwise nothing loads.
{manifest['queue_rows']:,} queue rows, {manifest['schedule_rows']:,} scheduled actions,
{manifest['manual_capacity_review']} calls in manual review. When notebook 04 or 05 rewrites these
files the dashboard reloads them on the next interaction and opens a new state DB for the new
run / policy; the old log stays in its own file.

Whether a booking was cancelled is known only after its arrival. It is read for bookings that
arrived before today (Overview, Performance, Model monitoring) and never feeds the queue, the
schedule or the action log.

### What the data does not hold
- **Synthetic:** net contribution (`syn_net_*`): costs and commissions are estimates.
- **Not in the data:** consent / opt-out and other open actions, confirmed by staff at execution.

### Still open with F / G1
- Value at risk: F uses p × gross value; the decision table uses p × net contribution. Both shown.
- `offer_change` always needs approval here (stricter than the table, which asks it only on a price change).
- Real call capacity N, D for block release, deposit policy (I6), overbooking limits (G3).
""")
