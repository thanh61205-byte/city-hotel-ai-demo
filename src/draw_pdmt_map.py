"""Draw docs/pdmt_map.png for the current F -> G1 -> G2 state (numbers read from the artifacts).

    python src/draw_pdmt_map.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
INK, INK2 = '#1f2937', '#4b5563'
COL = {'problem': ('#e35a5c', '#fdecec'), 'data': ('#3b82c4', '#e8f0fb'), 'method': ('#2e9e5b', '#e8f5ec'),
       'tools': ('#e2a31c', '#fdf5e6'), 'value': ('#7c5cc4', '#f0edf9')}
TAG = {'REAL': '#1d4ed8', 'DERIVED': '#0f766e', 'SYNTHETIC': '#b45309'}


def numbers(root: Path = ROOT) -> dict:
    tm = pd.read_csv(root / 'artifacts/modeling/test_metrics.csv', index_col=0)
    bc = pd.read_csv(root / 'artifacts/business_case/business_case.csv', index_col=0)
    key = json.loads((root / 'artifacts/business_case/key_numbers.json').read_text())
    pol = json.loads((root / 'artifacts/business_case/g1_policy.json').read_text())
    sc = pd.read_csv(root / 'artifacts/scenarios_capacity/scenario_kpis.csv', index_col=0)
    tc = json.loads((root / 'artifacts/text_clustering/summary.json').read_text())
    mf = json.loads((root / 'artifacts/modeling/manifest.json').read_text())
    # The map says price fields are not model inputs and the metrics are the retrospective cohort:
    # check both against the F manifest so the picture cannot drift from the model.
    excluded = set(mf['config']['excluded_price_features'])
    assert {'adr_clean', 'adr_vs_ref', 'adr_per_guest'} <= excluded, excluded
    assert 'retrospective arrival cohort' in mf['contracts']['headline'], mf['contracts']['headline']
    return {'auc': tm.at['final_model', 'roc_auc'], 'lift20': tm.at['final_model', 'lift@20%'],
            'lift20_rule': tm.at['rule_segment_x_leadband', 'lift@20%'],
            'f_run': pol['stage_f_run'], 'high': pol['policy']['high_share'], 'contact': pol['policy']['contact_share'],
            'cap': pol['call_capacity_per_day'], 'net': key['S4_net_per_year'], 'roi': key['S4_roi_3y'],
            'payback': key['S4_payback_months'], 'one_off': key['one_off'],
            'revpar0': sc.at['S0 Actual 2017', 'revpar_eur'], 'revpar5': sc.at['S5 G1 + group early release', 'revpar_eur'],
            'occ0': sc.at['S0 Actual 2017', 'occupancy'], 'occ5': sc.at['S5 G1 + group early release', 'occupancy'],
            'k': tc['k_selected'], 'agree': tc['label_agreement_with_generator'],
            'bc_s5': bc.loc[bc.index.str.startswith('S5')].iloc[0]['annual_net_benefit_eur']}


def panel(ax, x, y, w, h, title, key):
    edge, fill = COL[key]
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0,rounding_size=0.12', fc=fill, ec=edge, lw=2))
    ax.add_patch(FancyBboxPatch((x, y + h - 0.5), w, 0.5, boxstyle='round,pad=0,rounding_size=0.12', fc=edge, ec=edge))
    ax.text(x + w / 2, y + h - 0.25, title, ha='center', va='center', color='white', fontsize=15, fontweight='bold')


def card(ax, x, y, w, h, title, body, edge, tags=(), size=10.2, title_color=None):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0,rounding_size=0.08', fc='white', ec=edge, lw=1.5))
    ax.text(x + 0.15, y + h - 0.18, title, ha='left', va='top', fontsize=size + 2, fontweight='bold', color=title_color or edge)
    ty = y + h - 0.62
    tx = x + 0.15
    for t in tags:
        ax.text(tx, ty, f' {t} ', ha='left', va='center', fontsize=9, fontweight='bold', color='white',
                bbox=dict(boxstyle='round,pad=0.35', fc=TAG[t], ec='none'))
        tx += 0.13 * len(t) + 0.45
    ax.text(x + 0.15, ty - (0.35 if tags else -0.12), body, ha='left', va='top', fontsize=size, color=INK, linespacing=1.3)


def arrow(ax, x1, y1, x2, y2, color=INK2, label=None):
    ax.annotate('', (x2, y2), (x1, y1), arrowprops=dict(arrowstyle='-|>', color=color, lw=2))
    if label:
        ax.text((x1 + x2) / 2 + 0.1, (y1 + y2) / 2, label, fontsize=9.5, color=color, fontweight='bold', va='center')


def draw(out: Path = ROOT / 'docs/pdmt_map.png') -> Path:
    n = numbers()
    fig = plt.figure(figsize=(20, 11.64))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 20); ax.set_ylim(0, 11.64); ax.axis('off')
    ax.text(10, 11.3, 'PDMT Map — Cancellation Decision Support, City Hotel Lisbon', ha='center', va='center',
            fontsize=22, fontweight='bold', color=INK)
    ax.text(10, 10.8, f"State after F → G1 → G2 (F run {n['f_run']}) · retrospective prototype on a public PMS extract · "
            'simulated inputs are labelled', ha='center', va='center', fontsize=11.5, color=INK2)

    # PROBLEM
    panel(ax, 0.2, 2.65, 3.65, 7.85, 'PROBLEM', 'problem')
    ax.text(0.42, 9.75, 'High cancellation\nrates make occupancy\nand revenue uncertain.', va='top', fontsize=14.5,
            fontweight='bold', color=INK, linespacing=1.25)
    ax.text(0.42, 8.0, '41.7% of 79,330 bookings\ncancelled or no-show; €10.9M\nof €25.3M gross booked value\n'
            'exposed (not a realised loss).', va='top', fontsize=10.8, color=INK2, linespacing=1.3)
    ax.text(0.42, 6.35, 'How might City Hotel Lisbon\npredict cancellation risk\nearly, prioritise high-risk\n'
            'bookings and recommend\nproportionate interventions to\noptimise revenue and room\ncapacity?',
            va='top', fontsize=12, color=INK, linespacing=1.3)
    ax.text(0.42, 3.4, 'Process gaps and all effect sizes\nare assumptions to validate in a\npilot with a holdout arm.',
            va='top', fontsize=10, color=INK2, linespacing=1.3)

    # DATA
    e = COL['data'][0]
    panel(ax, 4.1, 2.65, 5.45, 7.85, 'DATA', 'data')
    card(ax, 4.3, 7.75, 5.05, 2.15, '1  PREDICTION (model inputs)', 'Booking extract, City Hotel only: lead time, stay,\n'
         'party, segment, channel, room type, meal, agent,\nguest history; calendar and on-the-books demand.\n'
         'ADR / price fields excluded. Near-arrival snapshot.', e, ('REAL', 'DERIVED'), size=10.2)
    card(ax, 4.3, 5.25, 5.05, 2.3, '2  DECISION & SCENARIOS (not inputs)', 'Finance: room cost, commission, handling,\n'
         'resale. Operations: rooms sold / available per\ndate × room type. Uplift, backfire, resale,\n'
         'release share, price elasticity (labelled).', e, ('SYNTHETIC', 'DERIVED'), size=10.2)
    card(ax, 4.3, 2.85, 5.05, 2.2, '3  RETROSPECTIVE (after the outcome)', 'CX: contacts, responses, satisfaction,\n'
         'cancellation-reason text. Generated from the\noutcome, so never a predictor.', e, ('SYNTHETIC',), size=10.2)

    # METHOD
    m = COL['method'][0]
    panel(ax, 9.8, 2.65, 5.95, 7.85, 'METHOD', 'method')
    card(ax, 10.0, 7.75, 5.55, 2.15, 'F  Cancellation-risk model', f"XGBoost; retrospective 2017 arrival cohort: ROC-AUC {n['auc']:.2f},\n"
         f"lift@20% {n['lift20']:.2f} (segment × lead rule {n['lift20_rule']:.2f}). Operations: weekly\n"
         'batches at the end of the booking week. Group blocks:\nsegment × deposit rule. Manifest with input hashes.', m, size=10.2)
    card(ax, 10.0, 5.25, 5.55, 2.3, 'G1  Rules, capacity and scenarios', f"Call tier top {n['high']:.0%} by p × value, reminders to\n"
         f"{n['contact']:.0%} of the batch, max {n['cap']} calls / day, overflow to\nmanual review; group early release; "
         'G3 overbooking OFF.\nScenarios S0–S6: occupancy, ADR, RevPAR, net, ROI.', m, size=10.2)
    card(ax, 10.0, 2.85, 5.55, 2.2, 'Text & CX analysis (retrospective)', f"TF-IDF + K-Means on reason text: k = {n['k']}, "
         f"{n['agree']:.0%} label\nagreement with the generator (template text, pipeline\n"
         'demo). Informs message wording only.', m, size=10.2)
    arrow(ax, 12.78, 7.75, 12.78, 7.55, m)
    ax.text(12.95, 7.65, 'p_cancel', fontsize=10, color=m, fontweight='bold', va='center')
    ax.plot([10.2, 15.3], [5.15, 5.15], ls='--', color='#9ca3af', lw=1.3)
    ax.text(12.78, 5.15, 'no flow from lane 3 into the model', ha='center', va='center', fontsize=9, color=INK2,
            bbox=dict(fc=COL['method'][1], ec='none', pad=1))
    for y in (8.8, 6.4, 3.95):
        arrow(ax, 9.37, y, 9.98, y)

    # TOOLS
    t = COL['tools'][0]
    panel(ax, 16.0, 2.65, 3.8, 7.85, 'TOOLS', 'tools')
    tc = '#92400e'
    card(ax, 16.2, 8.25, 3.4, 1.6, 'Python', 'pandas, scikit-learn, XGBoost:\npreparation, model, scenarios,\ntext analysis; unit tests',
         t, size=10, title_color=tc)
    card(ax, 16.2, 6.4, 3.4, 1.65, 'SQLite / PostgreSQL 16', 'G2 store: actions, approvals,\nlogs; guard triggers; writes\n'
         'one at a time per store', t, size=10, title_color=tc)
    card(ax, 16.2, 4.55, 3.4, 1.65, 'n8n (demo workflow)', 'webhook re-checks the guard;\nmock dispatch: sends nothing,\n'
         'changes no PMS (executed: false)', t, size=10, title_color=tc)
    card(ax, 16.2, 2.85, 3.4, 1.5, 'Streamlit dashboard', 'risk, G1 schedule, approvals,\nresponses, closures, KPIs',
         t, size=10, title_color=tc)
    arrow(ax, 3.87, 6.6, 4.08, 6.6, COL['problem'][0])
    arrow(ax, 15.77, 6.6, 15.98, 6.6, m)

    # feedback loop
    ax.plot([17.9, 17.9, 2.0, 2.0], [2.65, 2.33, 2.33, 2.55], color=INK2, lw=2)
    ax.annotate('', (2.0, 2.63), (2.0, 2.45), arrowprops=dict(arrowstyle='-|>', color=INK2, lw=2))
    ax.text(10, 2.33, ' Feedback: intervention log + treatment / holdout KPIs → re-tune rules, re-train model ',
            ha='center', va='center', fontsize=11, color=INK2, bbox=dict(fc='white', ec='none'))

    # VALUE
    v = COL['value'][0]
    ax.add_patch(FancyBboxPatch((0.2, 0.15), 19.6, 1.95, boxstyle='round,pad=0,rounding_size=0.12', fc=COL['value'][1], ec=v, lw=2))
    ax.add_patch(FancyBboxPatch((0.2, 1.62), 19.6, 0.48, boxstyle='round,pad=0,rounding_size=0.12', fc=v, ec=v))
    ax.text(10, 1.86, 'EXPECTED BUSINESS VALUE (expected values under team assumptions; to be measured in the pilot)',
            ha='center', va='center', color='white', fontsize=13.5, fontweight='bold')
    items = [('Earlier risk visibility', f"weekly p_cancel per booking; lift@20%\n{n['lift20']:.2f} vs {n['lift20_rule']:.2f} (retrospective 2017 cohort)"),
             ('Focused follow-up', f"G1 policy: €{n['net'] / 1000:.1f}k net / year, 3-year ROI\n{n['roi']:.2f}, payback {n['payback']:.0f} months"),
             ('Better capacity decisions', f"S5 RevPAR €{n['revpar0']:.2f} → €{n['revpar5']:.2f}, occupancy\n"
              f"{n['occ0']:.1%} → {n['occ5']:.1%}; price step depends on elasticity"),
             ('Measured value', 'approval guard + logs in G2; KPI register,\nrandomised pilot with holdout, ROI sensitivity')]
    for i, (h, b) in enumerate(items):
        x = 0.45 + i * 4.9
        ax.text(x, 1.45, h, va='top', fontsize=13, fontweight='bold', color='#4c1d95')
        ax.text(x, 1.0, b, va='top', fontsize=10.8, color=INK, linespacing=1.3)

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=165, facecolor='white')
    plt.close(fig)
    return out


if __name__ == '__main__':
    print(draw())
