"""Load the stage-G1 handover (notebook 05) and check it against the F run it was built on.

Sources (artifacts/business_case/, schema 2, see docs/G1_data_contract.md):
  g1_policy.json               frozen policy: shares, ranking, call capacity, call window, F run
  action_queue_g1_2017.csv     one row per eligible F individual booking: G1 tier / rule / status
  action_schedule_g1_2017.csv  dated actions; I3 calls placed by the capacity-limited scheduler

Nothing here is trusted because G1 wrote it: the tiers are re-derived from F's scores with the
frozen policy (same ranking and tie-break as F), the rules from the tiers, and every dated
action from the decision table (I1 / I2 milestones) or the call window and daily capacity (I3).
Pure pandas / numpy: the dashboard does not need the modelling stack.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

G1_QUEUE_REQUIRED = {'booking_id', 'decision_at', 'arrival_date', 'days_to_arrival', 'p_cancel',
                     'expected_revenue_at_risk', 'tier_g1', 'rule_g1', 'schedule_status',
                     'first_contact', 'planned_messages'}
G1_SCHEDULE_REQUIRED = {'booking_id', 'decision_at', 'scheduled_at', 'rule', 'action_type',
                        'schedule_reason', 'message_no'}
# Outcome-dependent fields belong to retrospective_evaluation_2017.csv only (G1 schema 2).
G1_FORBIDDEN = {'is_canceled', 'reservation_status', 'reservation_status_date', 'messages',
                'expected_net_eur', 'outcome_label_known_later'}
STATUSES = {'scheduled', 'manual_capacity_review', 'buffer_only'}
# Owner / approval of each individual rule (decision table §3), as F exports them.
RULE_OWNER = {'I1': ('n8n, monitored by Reservations', 'auto'),
              'I2': ('n8n, monitored by Reservations', 'auto'),
              'I3': ('Reservations / front office', 'none (internal task)'),
              'I5': ('Revenue manager', '-')}
_MASK64 = (1 << 64) - 1


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def g1_paths(repo) -> dict[str, Path]:
    bc = Path(repo) / 'artifacts' / 'business_case'
    return {'policy': bc / 'g1_policy.json', 'queue': bc / 'action_queue_g1_2017.csv',
            'schedule': bc / 'action_schedule_g1_2017.csv'}


def policy_id(policy_path: Path) -> str:
    """Short, filename-safe identity of the exact G1 policy file."""
    return 'g1-' + hashlib.sha256(Path(policy_path).read_bytes()).hexdigest()[:8]


# --- F's ranking helpers, re-implemented without the modelling stack (src/f_modeling.py) ---
def id_tie_breaker(ids, seed: int = 0) -> np.ndarray:
    x = np.asarray(ids).astype(np.int64).astype(np.uint64)
    x = x + np.uint64((seed * 0x9E3779B97F4A7C15 + 0x9E3779B97F4A7C15) & _MASK64)
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    x = x ^ (x >> np.uint64(31))
    return (x >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def rank_in_batch(p, batch, ids) -> pd.DataFrame:
    frame = pd.DataFrame({'p': np.asarray(p, dtype=float), 'b': np.asarray(batch),
                          't': id_tie_breaker(ids), 'i': np.asarray(ids)})
    frame = frame.sort_values(['b', 'p', 't', 'i'], ascending=[True, False, True, True])
    frame['r'] = frame.groupby('b').cumcount()
    frame['n'] = frame.groupby('b')['p'].transform('size')
    return frame.sort_index()[['r', 'n']]


def capacity_slots(n, share: float) -> np.ndarray:
    return np.floor(np.asarray(n, dtype=float) * share + 1e-9).astype(int)


def g1_tiers(p, value_at_risk, batch, ids, high_share, contact_share, high_rank) -> np.ndarray:
    """Notebook 05 make_tiers: call tier = first floor(h n) by high_rank; reminder tier = next by
    p_cancel up to floor(c n); low = the rest."""
    high_score = np.asarray(p if high_rank == 'p_cancel' else value_at_risk, dtype=float)
    rk = rank_in_batch(high_score, batch, ids)
    is_high = rk['r'].to_numpy() < capacity_slots(rk['n'], high_share)
    rest = rank_in_batch(np.where(is_high, -np.inf, np.asarray(p, dtype=float)), batch, ids)
    n_med = capacity_slots(rk['n'], contact_share) - capacity_slots(rk['n'], high_share)
    is_med = ~is_high & (rest['r'].to_numpy() < n_med)
    return np.select([is_high, is_med], ['high', 'medium'], 'low')


def plan_rule(tier: str, days: int) -> str:
    """F's plan_rule: I5 overrides I3 at <= 7 days; I2 needs >= 8 days; otherwise I1."""
    if tier == 'high':
        return 'I3' if days >= 8 else 'I5'
    if tier == 'medium' and days >= 8:
        return 'I2'
    return 'I1'


# --- loading and checks ---
def load_g1(repo, f_queue: pd.DataFrame, f_run_id: str):
    """Return (g1 queue, g1 schedule, policy). f_queue: validated F queue (dates as date)."""
    paths = g1_paths(repo)
    missing = [str(p) for p in paths.values() if not p.exists()]
    _check(not missing, f'Missing G1 outputs (run python src/run_notebooks.py --stage G1): {missing}')
    policy = json.loads(paths['policy'].read_text(encoding='utf-8'))
    _check(policy.get('schema_version') == 2, f"G1 policy schema {policy.get('schema_version')}, expected 2")
    _check(policy.get('stage_f_run') == f_run_id,
           f"G1 was built on F run {policy.get('stage_f_run')}, but artifacts/modeling holds "
           f'{f_run_id}: re-run notebook 05 (python src/run_notebooks.py --stage G1)')
    _check(policy.get('overbooking', {}).get('enabled') is False, 'G1 policy must keep G3 overbooking disabled')

    q = pd.read_csv(paths['queue'])
    s = pd.read_csv(paths['schedule'])
    _check(not G1_QUEUE_REQUIRED.difference(q.columns),
           f'Missing G1 queue fields: {sorted(G1_QUEUE_REQUIRED.difference(q.columns))}')
    _check(not G1_SCHEDULE_REQUIRED.difference(s.columns),
           f'Missing G1 schedule fields: {sorted(G1_SCHEDULE_REQUIRED.difference(s.columns))}')
    leaked = G1_FORBIDDEN.intersection(set(q.columns) | set(s.columns))
    _check(not leaked, f'Outcome-dependent fields in the G1 handover: {sorted(leaked)}')
    _check(q['booking_id'].is_unique, 'G1 queue booking_id must be unique')
    for col in ('decision_at', 'arrival_date'):
        q[col] = pd.to_datetime(q[col]).dt.date
    for col in ('decision_at', 'scheduled_at'):
        s[col] = pd.to_datetime(s[col]).dt.date

    # Same bookings, scores and dates as F's individual queue.
    ind = f_queue[f_queue['track'].eq('individual model')].set_index('booking_id')
    _check(set(q['booking_id']) == set(ind.index),
           f'G1 queue ({len(q)}) is not the F individual queue ({len(ind)})')
    f = ind.loc[q['booking_id']]
    _check(np.allclose(q['p_cancel'].to_numpy(), f['p_cancel'].to_numpy(), rtol=0, atol=1e-9),
           'G1 p_cancel differs from F')
    _check((q['decision_at'].to_numpy() == f['score_time'].to_numpy()).all(),
           'G1 decision_at differs from F score_time')
    _check((q['arrival_date'].to_numpy() == f['arrival_date'].to_numpy()).all()
           and (q['days_to_arrival'].to_numpy() == f['days_to_arrival'].to_numpy()).all(),
           'G1 arrival / days to arrival differ from F')

    # Tiers and rules re-derived from F's scores with the frozen policy.
    pol = policy['policy']
    tiers = g1_tiers(q['p_cancel'], q['p_cancel'] * f['gross_value'].to_numpy(), q['decision_at'].astype(str),
                     q['booking_id'], pol['high_share'], pol['contact_share'], pol['high_rank'])
    _check((tiers == q['tier_g1'].to_numpy()).all(),
           f"G1 tiers cannot be re-derived from F scores with policy {pol}")
    rules = np.array([plan_rule(t, int(d)) for t, d in zip(q['tier_g1'], q['days_to_arrival'])])
    _check((rules == q['rule_g1'].to_numpy()).all(), 'G1 rule does not follow its tier and days to arrival')
    _check(set(q['schedule_status']) <= STATUSES, f"Unknown G1 schedule status {set(q['schedule_status'])}")
    _check((q['schedule_status'].eq('buffer_only') == q['rule_g1'].eq('I5')).all(),
           'buffer_only must be exactly the I5 bookings')
    _check(not q.loc[q['schedule_status'].eq('manual_capacity_review'), 'rule_g1'].ne('I3').any(),
           'Only I3 calls can overflow to manual review')

    validate_g1_schedule(s, q, policy)
    return q, s, policy


def validate_g1_schedule(s: pd.DataFrame, q: pd.DataFrame, policy: dict) -> None:
    """I1 / I2 exactly at the decision-table milestones; I3 within the call window and capacity."""
    rules, call = policy['rules'], policy['call_schedule']
    cap = int(policy['call_capacity_per_day'])
    qi = q.set_index('booking_id')
    _check((s['scheduled_at'] >= s['decision_at']).all(), 'G1 action before its decision time')
    _check((s['scheduled_at'] < s['booking_id'].map(qi['arrival_date'])).all(), 'G1 action on / after arrival')
    _check((s['decision_at'] == s['booking_id'].map(qi['decision_at'])).all(), 'G1 schedule decision date differs')
    _check((s['rule'] == s['booking_id'].map(qi['rule_g1'])).all(), 'G1 schedule rule differs from queue')
    _check(not s.duplicated(['booking_id', 'action_type', 'scheduled_at']).any(), 'Duplicate G1 action')

    expected = set()
    for r in q[q['rule_g1'].isin(['I1', 'I2'])].itertuples():
        d, a = r.decision_at, r.arrival_date
        if r.rule_g1 == 'I1':
            plan = [(d, 'confirm_email')]
            if a - timedelta(days=rules['I1_reminder_days']) > d:
                plan.append((a - timedelta(days=rules['I1_reminder_days']), 'reminder'))
        else:
            dates = sorted({max(d, a - timedelta(days=m)) for m in rules['I2_milestones_days']})
            plan = [(t, 'reminder') for t in dates][:rules['I2_max_messages']]
        expected.update((r.booking_id, k, t, n) for n, (t, k) in enumerate(plan, 1))
    got = s[s['rule'].isin(['I1', 'I2'])]
    _check(set(got[['booking_id', 'action_type', 'scheduled_at', 'message_no']].itertuples(index=False, name=None))
           == expected, 'G1 I1 / I2 dates differ from the decision-table milestones')

    calls = s[s['rule'].eq('I3')]
    _check(calls['action_type'].eq('phone_call').all() and calls['message_no'].eq(1).all(),
           'G1 I3 rows must be one phone call')
    scheduled = set(q.loc[q['rule_g1'].eq('I3') & q['schedule_status'].eq('scheduled'), 'booking_id'])
    _check(set(calls['booking_id']) == scheduled and calls['booking_id'].is_unique,
           'Every scheduled I3 booking needs exactly one call; manual-review bookings none')
    offset = pd.Series([(c - d).days for c, d in zip(calls['scheduled_at'], calls['decision_at'])])
    _check(offset.between(call['start_day'], call['window_days'] - 1).all(),
           f"G1 call outside the window (day {call['start_day']}..{call['window_days'] - 1})")
    lead = pd.Series([(a - c).days for a, c in zip(calls['booking_id'].map(qi['arrival_date']), calls['scheduled_at'])])
    _check(lead.ge(call['min_days_before_arrival']).all(),
           f"G1 call less than {call['min_days_before_arrival']} days before arrival")
    _check(calls.groupby('scheduled_at').size().max() <= cap if len(calls) else True,
           f'G1 schedules more than {cap} calls on a day')
    _check(not set(s['booking_id']) & set(q.loc[q['rule_g1'].eq('I5'), 'booking_id']), 'I5 must have no action')


def apply_g1(f_queue: pd.DataFrame, g1q: pd.DataFrame, g1s: pd.DataFrame, run_key: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Operational queue and schedule for G2: individual rows take G1's tier / rule / status
    (F's are kept as f_risk_tier / f_rule); group rows stay as F exported them."""
    q = f_queue.copy()
    q['f_risk_tier'], q['f_rule'] = q['risk_tier'], q['rule']
    g = g1q.set_index('booking_id')
    ind = q['track'].eq('individual model')
    ids = q.loc[ind, 'booking_id']
    q.loc[ind, 'risk_tier'] = ids.map(g['tier_g1']).to_numpy()
    q.loc[ind, 'rule'] = ids.map(g['rule_g1']).to_numpy()
    q['schedule_status'] = np.where(ind, q['booking_id'].map(g['schedule_status']), 'group_review')
    owner = q['rule'].map({k: v[0] for k, v in RULE_OWNER.items()})
    approval = q['rule'].map({k: v[1] for k, v in RULE_OWNER.items()})
    q.loc[ind, 'owner'] = owner[ind]
    q.loc[ind, 'approval'] = approval[ind]
    q.loc[ind, 'needs_approval'] = False
    text = (g1s.sort_values(['booking_id', 'scheduled_at'])
               .assign(t=lambda d: d['action_type'] + ' ' + d['scheduled_at'].astype(str))
               .groupby('booking_id')['t'].agg('; '.join))
    sched_text = ids.map(text)
    manual = ids.map(g['schedule_status']).eq('manual_capacity_review')
    sched_text = sched_text.where(~manual, 'manual review: no call slot within the window')
    sched_text = sched_text.where(ids.map(g['rule_g1']).ne('I5'),
                                  'count in date x room-type cancellation buffer (no guest contact)')
    q.loc[ind, 'scheduled_actions'] = sched_text.to_numpy()
    q['policy_key'] = run_key
    schedule = g1s.assign(run_id=run_key)
    return q, schedule
