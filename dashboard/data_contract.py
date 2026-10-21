"""Load and validate the stage-F v3.1 outputs the G2 dashboard uses, straight from the repo.

Sources (one F run, checked by run_id):
  artifacts/modeling/action_queue_2017.csv     weekly operational queue (tier, rule, owner, approval)
  artifacts/modeling/action_schedule_2017.csv  dated actions (decision_at <= scheduled_at)
  artifacts/modeling/output_consistency.csv    F's cross-file checks (must all pass)
  artifacts/modeling/manifest.json             run_id, model, config
  data/processed/bookings_prepared.csv         deposit_type, reserved_room_type, syn_net_booking_value

The dashboard shows only the operational weekly queue: bookings scored at the end of their
booking week and still eligible then. Post-outcome fields (is_canceled,
outcome_label_known_later, ...) are dropped here; the outcome of bookings that arrived before
today is read apart, by dashboard/model_view.py (Overview, Performance evaluation, Model monitoring).
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pandas as pd

from dashboard.state_store import RULE_ACTIONS

REPO = Path(__file__).resolve().parents[1]

POST_OUTCOME = {'is_canceled', 'outcome_label_known_later', 'reservation_status',
                'reservation_status_date', 'outcome (known later)'}
QUEUE_REQUIRED = {
    'run_id', 'track', 'booking_id', 'score_time', 'arrival_date', 'days_to_arrival',
    'market_segment', 'room_type', 'gross_value', 'p_cancel', 'expected_revenue_at_risk',
    'syn_net_value_at_risk', 'risk_tier', 'rule', 'owner', 'approval', 'needs_approval',
    'consent_status', 'open_action_check',
}
SCHEDULE_REQUIRED = {'run_id', 'booking_id', 'decision_at', 'scheduled_at', 'rule',
                     'action_type', 'schedule_reason', 'message_no'}
BOOKING_FIELDS = ['booking_id', 'deposit_type', 'reserved_room_type', 'syn_net_booking_value',
                  'exclude_from_model']
TIERS = ('high', 'medium', 'low')
# Guest actions F may schedule per rule; I5 and G2 have none (decision table §3 / §4).
SCHEDULE_ACTIONS = RULE_ACTIONS
RULE_FOR_TIER = {'high': {'I3', 'I5'}, 'medium': {'I2', 'I1'}, 'low': {'I1'}}


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def source_paths(repo: str | Path = REPO) -> dict[str, Path]:
    repo = Path(repo)
    art = repo / 'artifacts' / 'modeling'
    return {
        'queue': art / 'action_queue_2017.csv',
        'schedule': art / 'action_schedule_2017.csv',
        'consistency': art / 'output_consistency.csv',
        'manifest': art / 'manifest.json',
        'bookings': repo / 'data' / 'processed' / 'bookings_prepared.csv',
    }


def validate_queue(df: pd.DataFrame) -> pd.DataFrame:
    missing = QUEUE_REQUIRED.difference(df.columns)
    _check(not missing, f'Missing queue fields: {sorted(missing)}')
    leaked = POST_OUTCOME.intersection(df.columns)
    _check(not leaked, f'Post-outcome fields in operational data: {sorted(leaked)}')
    _check(df['run_id'].nunique() == 1, 'Queue mixes several F runs')
    _check(df['booking_id'].notna().all() and df['booking_id'].is_unique,
           'Each booking_id must be unique and non-null')
    _check(df['p_cancel'].between(0.0, 1.0).all(), 'p_cancel must be between 0 and 1')

    ind = df['track'].eq('individual model')
    _check(df.loc[ind, 'risk_tier'].isin(TIERS).all(), 'Individual rows need a risk tier')
    _check(df.loc[~ind, 'risk_tier'].isna().all(), 'Group rows must not carry an individual tier')
    for tier, rules in RULE_FOR_TIER.items():
        ok = df.loc[ind & df['risk_tier'].eq(tier), 'rule'].isin(rules)
        _check(ok.all(), f'Tier {tier} has a rule outside {sorted(rules)}')
    _check(df.loc[~ind, 'needs_approval'].astype(bool).all(), 'Group actions must need approval')

    df = df.copy()
    for col in ('score_time', 'arrival_date', 'booking_date'):
        if col in df:
            df[col] = pd.to_datetime(df[col], errors='raise').dt.date
    _check((df['arrival_date'] > df['score_time']).all(), 'Arrival must be after decision time')
    return df


def validate_schedule(df: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    missing = SCHEDULE_REQUIRED.difference(df.columns)
    _check(not missing, f'Missing schedule fields: {sorted(missing)}')
    df = df.copy()
    for col in ('decision_at', 'scheduled_at'):
        df[col] = pd.to_datetime(df[col], errors='raise').dt.date
    _check((df['scheduled_at'] >= df['decision_at']).all(),
           'An action is scheduled before its decision time')
    _check(df['run_id'].eq(queue['run_id'].iloc[0]).all(), 'Schedule and queue runs differ')
    key = queue.set_index('booking_id')[['score_time', 'rule']].add_prefix('q_')
    joined = df.join(key, on='booking_id')
    _check(joined['q_score_time'].notna().all(), 'Schedule booking missing from the queue')
    _check((joined['q_score_time'] == joined['decision_at']).all(),
           'Schedule does not match the queue decision time')
    _check((joined['q_rule'] == joined['rule']).all(), 'Schedule rule differs from queue rule')

    # Re-derive the decision-table shape of every booking's schedule here instead of trusting a
    # stored check: output_consistency.csv was written by F and cannot prove the file now on disk.
    allowed = df['rule'].map(SCHEDULE_ACTIONS)
    bad = [a not in ok if isinstance(ok, frozenset) else True
           for a, ok in zip(df['action_type'], allowed)]
    _check(not any(bad), 'Schedule action not allowed for its rule (I1: confirm_email + reminder, '
           f'I2: reminder, I3: phone_call, I5 / G2: none): {df.loc[bad, "booking_id"].head().tolist()}')
    _check(not df.duplicated(['booking_id', 'action_type', 'scheduled_at']).any(),
           'Duplicate scheduled action (booking, type, date)')
    arrival = df['booking_id'].map(queue.set_index('booking_id')['arrival_date'])
    _check((df['scheduled_at'] < arrival).all(), 'An action is scheduled on or after arrival')
    counts = df.groupby(['booking_id', 'action_type']).size().unstack(fill_value=0)
    for col in ('confirm_email', 'reminder', 'phone_call'):
        if col not in counts:
            counts[col] = 0
    rule_of = queue.set_index('booking_id')['rule']
    by_rule = counts.join(rule_of)
    i1, i2, i3 = (by_rule[by_rule['rule'].eq(r)] for r in ('I1', 'I2', 'I3'))
    _check(i1['confirm_email'].eq(1).all() and i1['reminder'].le(1).all(),
           'I1 needs exactly one confirmation and at most one reminder')
    _check(i2['reminder'].between(1, 2).all(), 'I2 needs one or two reminders')
    _check(i3['phone_call'].eq(1).all(), 'I3 needs exactly one call (attempt 1)')
    calls = df[df['action_type'].isin(['confirm_email', 'phone_call'])]
    _check((calls['scheduled_at'] == calls['decision_at']).all(),
           'I1 confirmation / I3 call must be at the decision time')
    planned = set(queue.loc[queue['rule'].isin(['I1', 'I2', 'I3']), 'booking_id'])
    _check(planned <= set(df['booking_id']), 'An I1 / I2 / I3 booking has no scheduled action')
    numbered = df.sort_values(['booking_id', 'scheduled_at', 'message_no'])
    expected = numbered.groupby('booking_id').cumcount() + 1
    _check((numbered['message_no'] == expected).all(), 'message_no must run 1..n per booking')

    # Independently check the dated policy in decision table §3, including missing
    # reminders. Counts alone cannot catch an I1/I2 reminder moved to a wrong day.
    expected_rows = set()
    for row in queue[queue['rule'].isin(['I1', 'I2', 'I3'])].itertuples():
        d, a = row.score_time, row.arrival_date
        if row.rule == 'I1':
            plan = [(d, 'confirm_email')]
            reminder = a - timedelta(days=7)
            if reminder > d:
                plan.append((reminder, 'reminder'))
        elif row.rule == 'I2':
            dates = sorted({max(d, a - timedelta(days=n)) for n in (30, 14)})
            plan = [(when, 'reminder') for when in dates]
        else:
            plan = [(d, 'phone_call')]
        expected_rows.update((row.booking_id, kind, when, n)
                             for n, (when, kind) in enumerate(plan, 1))
    actual_rows = set(df[['booking_id', 'action_type', 'scheduled_at', 'message_no']]
                      .itertuples(index=False, name=None))
    _check(actual_rows == expected_rows,
           'Schedule dates / reminders differ from decision table milestones '
           '(I1: confirmation now, reminder -7 if ahead; I2: -30 / -14, immediate if passed)')
    return df


def load_f_outputs(repo: str | Path = REPO) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Return (queue, schedule, meta) for the latest F run in artifacts/modeling/."""
    src = source_paths(repo)
    missing = [str(p) for p in src.values() if not p.exists()]
    _check(not missing, f'Missing F v3.1 inputs (run notebook 04 first): {missing}')

    manifest = json.loads(src['manifest'].read_text(encoding='utf-8'))
    run_id = manifest['run_id']
    checks = pd.read_csv(src['consistency'])
    failed = checks.loc[~checks['passed'].astype(str).str.lower().eq('true'), 'check'].tolist()
    _check(not failed, f'F output_consistency has failed checks: {failed}')

    queue = pd.read_csv(src['queue'])
    schedule = pd.read_csv(src['schedule'])
    for name, df in (('queue', queue), ('schedule', schedule)):
        runs = set(df['run_id'].unique())
        _check(runs == {run_id}, f'{name} run_id {runs} != manifest run_id {run_id}')

    bookings = pd.read_csv(src['bookings'], usecols=BOOKING_FIELDS)
    queue = queue.merge(bookings, on='booking_id', how='left', validate='one_to_one',
                        indicator=True)
    _check(queue['_merge'].eq('both').all(), 'Queue booking_id not found in bookings_prepared.csv')
    _check(queue['exclude_from_model'].astype(str).str.lower().eq('false').all(),
           'Queue contains a booking excluded by data-quality rules')
    queue = queue.drop(columns=['_merge', 'exclude_from_model'])
    queue = queue.drop(columns=[c for c in queue.columns if c in POST_OUTCOME])
    queue = queue.rename(columns={'reserved_room_type': 'room_type'})
    # F's figure is p x gross value; the decision table (C2) defines value at risk on the
    # simulated net contribution. Both are kept, under different names, until F/G1 agree.
    queue['syn_net_value_at_risk'] = (queue['p_cancel'] * queue['syn_net_booking_value']).round(2)
    queue['expected_revenue_at_risk'] = queue['expected_revenue_at_risk'].round(2)

    queue = validate_queue(queue)
    schedule = validate_schedule(schedule, queue)
    cfg = manifest.get('config', {})
    meta = {
        'run_id': run_id,
        'model_version': manifest.get('model_version'),
        'selected_model': manifest.get('selected_model'),
        'calibration': manifest.get('calibration'),
        'run_finished': manifest.get('run_finished'),
        'fit_date_final': cfg.get('fit_date_final'),
        'decision_time': cfg.get('decision_time'),
        'tier_high_share': cfg.get('tier_high_share'),
        'tier_medium_share': cfg.get('tier_medium_share'),
        'queue_rows': int(len(queue)),
        'schedule_rows': int(len(schedule)),
    }
    return queue, schedule, meta


def load_outputs(repo: str | Path = REPO) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """What the dashboard runs on: F's queue with G1's policy, tiers, rules and schedule.

    F is loaded and fully checked first (load_f_outputs), then the G1 handover is checked
    against it (dashboard/g1_contract.py). Individual rows carry G1's tier / rule /
    schedule_status (F's defaults kept as f_risk_tier / f_rule); group rows stay F's.
    The schedule is G1's (I3 calls placed within the call window and daily capacity).
    """
    from dashboard import g1_contract as g1c
    queue, _f_schedule, meta = load_f_outputs(repo)
    g1q, g1s, policy = g1c.load_g1(repo, queue, meta['run_id'])
    key = f"{meta['run_id']}+{g1c.policy_id(g1c.g1_paths(repo)['policy'])}"
    queue, schedule = g1c.apply_g1(queue, g1q, g1s, key)
    pol = policy['policy']
    meta.update({
        'policy_key': key, 'policy_source': 'G1 (artifacts/business_case/g1_policy.json)',
        'tier_high_share': pol['high_share'], 'tier_contact_share': pol['contact_share'],
        'tier_medium_share': pol['contact_share'], 'high_rank': pol['high_rank'],
        'call_capacity_per_day': int(policy['call_capacity_per_day']),
        'call_schedule': policy['call_schedule'], 'g1_chosen_on': policy.get('chosen_on'),
        'execution_policy': {'call_schedule': policy['call_schedule'],
                             'overbooking': policy['overbooking']},
        'manual_capacity_review': int(queue['schedule_status'].eq('manual_capacity_review').sum()),
        'schedule_rows': int(len(schedule)),
    })
    return queue, schedule, meta


def source_fingerprint(repo: str | Path = REPO) -> tuple:
    """(file, modification time, size) of every F and G1 input: changes when either is re-run.

    The dashboard passes it as the cache key of its loader, so a new run on disk is
    loaded (and re-validated) on the next rerun instead of serving the cached old run.
    """
    from dashboard.g1_contract import g1_paths
    out = []
    for key, path in sorted({**source_paths(repo), **{f'g1_{k}': v for k, v in g1_paths(repo).items()}}.items()):
        st = path.stat() if path.exists() else None
        out.append((key, st.st_mtime_ns if st else None, st.st_size if st else None))
    return tuple(out)


BUFFER_SOURCES = {'G2': 'group rule (G2)', 'I5': 'model, high risk ≤ 7 days (I5)'}


def cancellation_buffer(queue: pd.DataFrame, as_of=None) -> pd.DataFrame:
    """Expected cancellations per arrival date x reserved room type, for capacity decisions.

    Decision table: group / block bookings (rule G2) and individual high-risk bookings that
    arrive within 7 days (rule I5, no call) both feed this buffer. Each source keeps its own
    columns. The sum of p is an expected count of cancelled bookings, not a number of rooms
    that will certainly be resold; per-night inventory is not checked here.

    as_of (today): only bookings F had scored by then (score_time <= as_of) and whose
    arrival is still ahead (arrival_date > as_of). This is the table to decide from. Without
    as_of the whole 2017 period is summed: a retrospective total, never a decision input.
    """
    src = queue[queue['rule'].isin(list(BUFFER_SOURCES))]
    if as_of is not None:
        as_of = pd.Timestamp(as_of).date()
        src = src[(src['score_time'] <= as_of) & (src['arrival_date'] > as_of)]
    keys = ['arrival_date', 'room_type']
    out = (src.groupby(keys, as_index=False)
              .agg(bookings=('booking_id', 'size'),
                   expected_cancellations=('p_cancel', 'sum'),
                   gross_value=('gross_value', 'sum'),
                   expected_revenue_at_risk=('expected_revenue_at_risk', 'sum'),
                   first_decision=('score_time', 'min')))
    for rule in BUFFER_SOURCES:
        part = (src[src['rule'].eq(rule)].groupby(keys)
                .agg(**{f'bookings_{rule}': ('booking_id', 'size'),
                        f'expected_cancellations_{rule}': ('p_cancel', 'sum')}))
        out = out.join(part, on=keys)
        out[f'bookings_{rule}'] = out[f'bookings_{rule}'].fillna(0).astype(int)
        out[f'expected_cancellations_{rule}'] = out[f'expected_cancellations_{rule}'].fillna(0.0)
    cols = [c for c in out.columns if c.startswith('expected_cancellations')]
    out[cols] = out[cols].round(2)
    return out.sort_values(keys).reset_index(drop=True)
