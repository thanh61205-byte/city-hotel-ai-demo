"""README §6.5 — extended scenario: what the G1 policy, the group early-release track and a price
adjustment would do to occupancy, ADR and RevPAR (room-night ledger, stays 2017-01-01..2017-08-31).

Scenarios (each builds on the previous one):
  S0  actual 2017: realised stays from bookings_prepared (= ops_inventory_daily.rooms_sold)
  S4  + G1 follow-up: expected prevented cancellations stay, expected induced cancellations leave
  S5  + group early release (rule G1, group track): late group cancellations released 30 days ahead
  S6  + price adjustment: +price_step on high-demand cells, -price_step on low-demand cells, with the
      labelled elasticity (g1.price_adjustment in config/assumptions.yaml)

All values are EXPECTED values under team assumptions (uplift, backfire, resale probability, release
share, elasticity). Illustrative only: nothing here was measured, and S6 is not a pricing model.
G3 overbooking stays disabled and is not part of any scenario.

    python src/scenario_capacity.py        # writes artifacts/scenarios_capacity/
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'scenarios_capacity'
START, END = pd.Timestamp('2017-01-01'), pd.Timestamp('2017-08-31')
SCENARIOS = ['S0 Actual 2017', 'S4 G1 policy', 'S5 G1 + group early release', 'S6 S5 + price adjustment']


def load_assumptions(root: Path = ROOT) -> dict:
    return yaml.safe_load((root / 'config/assumptions.yaml').read_text(encoding='utf-8'))


def nights(frame: pd.DataFrame, room_col: str, weight: pd.Series, revenue: pd.Series) -> pd.DataFrame:
    """Spread each booking's weight (room-nights per night) and revenue evenly over its stay nights."""
    n = frame['total_nights'].astype(int).to_numpy()
    keep = n > 0
    idx = np.repeat(np.flatnonzero(keep), n[keep])
    offset = np.concatenate([np.arange(k) for k in n[keep]]) if keep.any() else np.array([], int)
    f = frame.iloc[idx]
    out = pd.DataFrame({
        'stay_date': f['arrival_date'].to_numpy() + pd.to_timedelta(offset, 'D'),
        'room_type': f[room_col].to_numpy(),
        'rooms': np.asarray(weight, float)[idx],
        'revenue': (np.asarray(revenue, float) / np.maximum(n, 1))[idx],
    })
    out = out[out['stay_date'].between(START, END)]
    return out.groupby(['stay_date', 'room_type'])[['rooms', 'revenue']].sum()


def resale_prob(rp: dict, notice_days, demand) -> np.ndarray:
    d = np.asarray(notice_days, float)
    bucket = np.select([d < 0, d >= 30, d >= 8], ['no_show', 'notice_30_plus', 'notice_8_29'], 'notice_0_7')
    return np.array([rp[b][x] for b, x in zip(bucket, demand)], float)


def build_inputs(root: Path = ROOT, A: dict | None = None) -> dict:
    A = A or load_assumptions(root)
    ops, G = A['operations'], A['g1']
    P = pd.read_csv(root / 'data/processed/bookings_prepared.csv',
                    parse_dates=['arrival_date', 'reservation_status_date'])
    inv = pd.read_csv(root / 'data/synthetic/ops_inventory_daily.csv', parse_dates=['stay_date'])
    rt = pd.read_csv(root / 'data/synthetic/dim_room_type.csv')
    sellable = set(rt.loc[rt['is_sellable'], 'room_type'])
    inv = inv[inv['stay_date'].between(START, END) & inv['room_type'].isin(sellable)]
    cells = inv.set_index(['stay_date', 'room_type'])[['available_rooms', 'rooms_sold']]

    occ = cells['rooms_sold'] / cells['available_rooms'].where(cells['available_rooms'] > 0)
    arr_occ = occ.reindex(pd.MultiIndex.from_arrays([P['arrival_date'], P['reserved_room_type']])).to_numpy()
    P['demand'] = np.where(np.nan_to_num(arr_occ) >= ops['high_demand_occupancy_threshold']['value'],
                           'high_demand', 'normal')

    stay = P[P['is_canceled'].eq(0)]
    base = nights(stay, 'assigned_room_type', np.ones(len(stay)), stay['adr_clean'] * stay['total_nights'])
    base = base.reindex(cells.index, fill_value=0.0)
    return {'A': A, 'P': P, 'cells': cells, 'base': base,
            'bc': root / 'artifacts/business_case', 'queue_f': root / 'artifacts/modeling/action_queue_2017.csv'}


def g1_delta(I: dict) -> tuple[pd.DataFrame, dict]:
    """S4: expected prevented cancellations stay (minus the share the hotel had resold anyway);
    expected induced cancellations leave (minus the expected resale of their rooms)."""
    A, P = I['A'], I['P']
    rp, price_factor = A['operations']['resale_probability']['value'], A['operations']['resale_price_factor']['value']
    ev = pd.read_csv(I['bc'] / 'retrospective_evaluation_2017.csv')
    plan = pd.read_csv(I['bc'] / 'action_queue_g1_2017.csv', usecols=['booking_id', 'first_contact'],
                       parse_dates=['first_contact'])
    q = ev.merge(plan, on='booking_id', validate='one_to_one').merge(P, on='booking_id', validate='one_to_one')

    pv = q[q['retrospective_prevented'] > 0]
    kept_share = 1 - (pv['syn_rebooked_room_nights'].fillna(0) / pv['total_nights']).clip(0, 1)
    gain = nights(pv, 'reserved_room_type', pv['retrospective_prevented'] * kept_share,
                  pv['retrospective_prevented'] * (pv['gross_value'] - pv['syn_rebooking_revenue'].fillna(0)).clip(lower=0))

    ind = q[q['retrospective_induced'] > 0]
    p_res = resale_prob(rp, (ind['arrival_date'] - ind['first_contact']).dt.days, ind['demand'])
    loss = nights(ind, 'assigned_room_type', -ind['retrospective_induced'] * (1 - p_res),
                  -ind['retrospective_induced'] * ind['gross_value'] * (1 - p_res * price_factor))
    delta = gain.add(loss, fill_value=0.0)
    s4 = pd.read_csv(I['bc'] / 'scenarios_2017.csv', index_col=0).loc['S4 G1 policy']
    info = {'prevented_cancellations_expected': float(q['retrospective_prevented'].sum()),
            'induced_cancellations_expected': float(q['retrospective_induced'].sum()),
            'intervention_cost_eur': float(s4['variable_cost_eur']),
            'g1_net_eur_notebook05': float(s4['net_eur'])}
    return delta, info


def group_delta(I: dict) -> tuple[pd.DataFrame, dict]:
    """S5 increment: notebook 05 cell 13 in room-nights. A late group cancellation released at the
    ask (30 days out) is resold with the 30+ day probability instead of its actual notice probability."""
    A, P = I['A'], I['P']
    ops, GT = A['operations'], A['g1']['group_track']
    rp, price_factor = ops['resale_probability']['value'], ops['resale_price_factor']['value']
    aq = pd.read_csv(I['queue_f'], usecols=['track', 'booking_id', 'days_to_arrival', 'p_cancel'])
    qg = aq[aq['track'].eq('group rule')].merge(P, on='booking_id', how='left', validate='one_to_one')
    ask = qg['arrival_date'] - pd.to_timedelta(GT['release_ask_days'], 'D')
    flag = qg['p_cancel'].ge(GT['flag_threshold']) & qg['days_to_arrival'].gt(GT['release_ask_days'])
    no_show = qg['reservation_status'].eq('No-Show')
    late = qg['is_canceled'].eq(1) & (no_show | qg['reservation_status_date'].gt(ask))
    notice = np.where(no_show, -1, (qg['arrival_date'] - qg['reservation_status_date']).dt.days)
    lift = (resale_prob(rp, np.full(len(qg), 30), qg['demand']) - resale_prob(rp, notice, qg['demand'])).clip(min=0)
    w = (flag & late).to_numpy() * GT['release_success_share']['value'] * lift
    g = qg[w > 0]
    delta = nights(g, 'reserved_room_type', w[w > 0], w[w > 0] * g['gross_value'] * price_factor)
    cost = float(flag.sum() * GT['review_minutes_per_booking']['value'] / 60
                 * GT['revenue_manager_cost_per_hour']['value'])
    return delta, {'group_bookings_flagged': int(flag.sum()), 'late_cancellations_flagged': int((flag & late).sum()),
                   'released_room_nights_expected': float(w @ g['total_nights'].reindex(qg.index, fill_value=0)),
                   'intervention_cost_eur': cost}


def cap(ledger: pd.DataFrame, cells: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """No cell sells more rooms than it has (or fewer than zero); revenue scales with the rooms kept."""
    rooms = ledger['rooms'].clip(lower=0, upper=cells['available_rooms'])
    scale = (rooms / ledger['rooms'].where(ledger['rooms'] > 0)).fillna(1.0)
    capped = float((ledger['rooms'] - rooms).clip(lower=0).sum())
    return pd.DataFrame({'rooms': rooms, 'revenue': ledger['revenue'] * scale}), capped


def price_adjust(ledger: pd.DataFrame, cells: pd.DataFrame, A: dict, elasticity: float, step: float,
                 cut: bool = True) -> pd.DataFrame:
    """Raise the rate on high-demand cells and (optionally) cut it on low-demand cells; volume follows
    the elasticity. Applied to the realised demand of each cell, as if the rate had been set ahead."""
    hi = A['operations']['high_demand_occupancy_threshold']['value']
    lo = A['g1']['price_adjustment']['low_demand_occupancy_threshold']['value']
    occ = ledger['rooms'] / cells['available_rooms'].where(cells['available_rooms'] > 0)
    dp = np.select([occ >= hi, (occ < lo) & cut], [step, -step], 0.0)
    rooms = ledger['rooms'] * (1 + elasticity * dp)
    out = pd.DataFrame({'rooms': rooms, 'revenue': ledger['revenue'] * (1 + elasticity * dp) * (1 + dp)})
    out['price_change'] = dp
    capped, _ = cap(out, cells)
    capped['price_change'] = dp
    return capped


def kpis(ledger: pd.DataFrame, cells: pd.DataFrame) -> dict:
    sold, rev, avail = ledger['rooms'].sum(), ledger['revenue'].sum(), cells['available_rooms'].sum()
    return {'room_nights_sold': float(sold), 'available_room_nights': float(avail), 'occupancy': sold / avail,
            'room_revenue_eur': float(rev), 'adr_eur': rev / sold, 'revpar_eur': rev / avail}


def run(root: Path = ROOT, out: Path | None = OUT) -> dict:
    I = build_inputs(root)
    A, cells, base = I['A'], I['cells'], I['base']
    PA = A['g1']['price_adjustment']
    eps, step = PA['elasticity']['value'], PA['price_step']['value']
    commission = float(I['P']['syn_channel_commission_rate'].median())
    room_cost = float(I['P']['syn_room_cost_per_night'].median())

    d4, info4 = g1_delta(I)
    d5, info5 = group_delta(I)
    s4, cap4 = cap(base.add(d4.reindex(cells.index, fill_value=0.0)), cells)
    s5, cap5 = cap(s4.add(d5.reindex(cells.index, fill_value=0.0)), cells)
    s6 = price_adjust(s5, cells, A, eps, step)
    ledgers = dict(zip(SCENARIOS, [base, s4, s5, s6]))
    costs = dict(zip(SCENARIOS, [0.0, info4['intervention_cost_eur'],
                                 info4['intervention_cost_eur'] + info5['intervention_cost_eur'],
                                 info4['intervention_cost_eur'] + info5['intervention_cost_eur']]))

    k0 = kpis(base, cells)
    rows = []
    for name, led in ledgers.items():
        k = kpis(led, cells)
        d_rev, d_rooms = k['room_revenue_eur'] - k0['room_revenue_eur'], k['room_nights_sold'] - k0['room_nights_sold']
        rows.append({'scenario': name, **k, 'delta_room_nights': d_rooms, 'delta_room_revenue_eur': d_rev,
                     'delta_revpar_eur': k['revpar_eur'] - k0['revpar_eur'],
                     'intervention_cost_eur': costs[name],
                     'contribution_net_eur': d_rev * (1 - commission) - d_rooms * room_cost - costs[name]})
    summary = pd.DataFrame(rows).set_index('scenario')

    by_type = []
    for name, led in ledgers.items():
        g = led.groupby(level='room_type')[['rooms', 'revenue']].sum().join(
            cells.groupby(level='room_type')['available_rooms'].sum())
        by_type.append(pd.DataFrame({'scenario': name, 'room_type': g.index, 'room_nights_sold': g['rooms'].to_numpy(),
                                     'occupancy': (g['rooms'] / g['available_rooms']).to_numpy(),
                                     'adr_eur': (g['revenue'] / g['rooms']).to_numpy(),
                                     'revpar_eur': (g['revenue'] / g['available_rooms']).to_numpy()}))
    by_type = pd.concat(by_type, ignore_index=True)
    monthly = pd.concat({n: l.groupby(l.index.get_level_values('stay_date').to_period('M'))[['rooms', 'revenue']].sum()
                         for n, l in ledgers.items()}, names=['scenario', 'month']).join(
        cells.groupby(cells.index.get_level_values('stay_date').to_period('M'))['available_rooms'].sum().rename_axis('month'))
    monthly['occupancy'] = monthly['rooms'] / monthly['available_rooms']
    monthly['revpar_eur'] = monthly['revenue'] / monthly['available_rooms']

    sens = []
    eps_grid = sorted({PA['elasticity']['sensitivity_range'][0], -0.8, -1.2, PA['elasticity']['sensitivity_range'][1], eps})
    step_grid = sorted({PA['price_step']['sensitivity_range'][0], step, PA['price_step']['sensitivity_range'][1]})
    for e in eps_grid:
        for s in step_grid:
            for cut in (True, False):
                k = kpis(price_adjust(s5, cells, A, e, s, cut), cells)
                k5 = kpis(s5, cells)
                sens.append({'elasticity': e, 'price_step': s, 'variant': 'raise + cut' if cut else 'raise only',
                             'occupancy': k['occupancy'], 'adr_eur': k['adr_eur'], 'revpar_eur': k['revpar_eur'],
                             'delta_revpar_vs_S5_eur': k['revpar_eur'] - k5['revpar_eur'],
                             'delta_room_revenue_vs_S5_eur': k['room_revenue_eur'] - k5['room_revenue_eur']})
    sens = pd.DataFrame(sens)
    pc = s6['price_change']
    result = {
        'horizon': f'stays {START.date()}..{END.date()} (sellable room types)',
        'base_matches_inventory_rooms_sold': bool(np.isclose(base['rooms'].sum(), cells['rooms_sold'].sum())),
        'g1': info4, 'group_track': info5,
        'capped_room_nights': {'S4': cap4, 'S5': cap5},
        'price_adjustment': {'elasticity': eps, 'price_step': step,
                             'cells_raised': int((pc > 0).sum()), 'cells_cut': int((pc < 0).sum()),
                             'cells_total': int(len(pc)), 'basis': PA['elasticity']['basis'],
                             'reading': ('Revenue in a cell moves by about step x (1 + elasticity): a raise pays only while '
                                         'demand is inelastic (elasticity above -1), a cut only while it is elastic. The sign '
                                         'of S6 therefore rests on an elasticity the data cannot estimate (see price_sensitivity.csv); '
                                         'part of its contribution is room cost saved on fewer sold nights.')},
        'contribution_inputs': {'commission_rate_median': commission, 'room_cost_per_night_median': room_cost,
                                'note': 'Variable intervention cost only; one-off and running costs are in business_case.csv.'},
        'scenarios': summary.reset_index().to_dict('records'),
        'note': ('Expected values under team assumptions; illustrative, not a forecast. S6 applies a labelled '
                 'elasticity to realised demand and is not a pricing model. G3 overbooking stays disabled.'),
    }
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        summary.to_csv(out / 'scenario_kpis.csv')
        by_type.to_csv(out / 'scenario_kpis_by_room_type.csv', index=False)
        monthly.to_csv(out / 'scenario_kpis_by_month.csv')
        sens.to_csv(out / 'price_sensitivity.csv', index=False)
        (out / 'summary.json').write_text(json.dumps(result, indent=2, default=float), encoding='utf-8')
        figure(monthly, out / 'fig_revpar_by_month.png')
    return result


def figure(monthly: pd.DataFrame, path: Path) -> None:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 4))
    for (name, g), style in zip(monthly.groupby(level='scenario', sort=False), ['-', '--', '-.', ':']):
        x = [str(m) for m in g.index.get_level_values('month')]
        ax.plot(x, g['revpar_eur'], style, color='0.2', lw=1.4, label=name)
    ax.set_ylabel('RevPAR (EUR)')
    ax.set_title('RevPAR by stay month, 2017 (expected values, illustrative)', loc='left', fontsize=11)
    ax.legend(fontsize=8, frameon=False)
    ax.spines[['top', 'right']].set_visible(False)
    fig.savefig(path, dpi=160, bbox_inches='tight')
    plt.close(fig)


if __name__ == '__main__':
    r = run()
    print(pd.DataFrame(r['scenarios']).set_index('scenario').round(3).T.to_string())
    print(json.dumps({k: r[k] for k in ('base_matches_inventory_rooms_sold', 'g1', 'group_track',
                                        'capped_room_nights', 'price_adjustment')}, indent=1, default=float))
