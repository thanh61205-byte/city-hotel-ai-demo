"""Shared code for stage F (notebook 04): fold-safe feature transforms, model builders, simple rules,
evaluation metrics and the scoring entry point of the saved model bundle.

Kept as a module (not inline in the notebook) so the saved bundle can be loaded and scored anywhere:
    from f_modeling import load_bundle, score
    bundle = load_bundle('artifacts/modeling/final_model_bundle.joblib')
    p_cancel = score(bundle, frame)   # frame = rows of modeling_dataset.csv joined with agent_id
"""
from __future__ import annotations

import joblib
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

LEAD_BINS = [-np.inf, 7, 30, 90, 180, np.inf]
LEAD_LABELS = ['0-7d', '8-30d', '31-90d', '91-180d', '180d+']


# ---------------------------------------------------------------------------------------------
# Fold-safe transform: the top-N agent list is learned from the rows the pipeline is fitted on,
# so every fold (and the final model) uses only agents seen up to its own fit date.
# ---------------------------------------------------------------------------------------------
class TopAgentEncoder(BaseEstimator, TransformerMixin):
    """agent_id -> agent id if among the `top_n` most frequent agents of the fit rows, else 'other';
    'none' (no agent) stays 'none'. Replaces the agent_top column of notebook 03, whose top-15 list
    was fitted on all training arrivals up to 2016-12-31."""

    def __init__(self, top_n: int = 15):
        self.top_n = top_n

    def fit(self, X, y=None):
        agents = pd.Series(np.asarray(X).ravel()).astype(str)
        counts = agents[agents.ne('none')].value_counts()
        self.top_agents_ = sorted(counts.index[:self.top_n].tolist())
        return self

    def transform(self, X):
        agents = pd.Series(np.asarray(X).ravel()).astype(str)
        out = np.where(agents.isin(self.top_agents_), agents, 'other')
        out = np.where(agents.eq('none'), 'none', out)
        return out.reshape(-1, 1)

    def get_feature_names_out(self, input_features=None):
        return np.asarray(['agent_top'], dtype=object)


def build_pipeline(model_name: str, numeric: list[str], categorical: list[str], params: dict | None = None,
                   seed: int = 42, top_agents: int = 15) -> Pipeline:
    """Preprocessing + estimator. All fitted steps (imputers, scaler, encoders, top-agent list) are refit
    whenever the pipeline is fitted, i.e. inside every fold. No class weighting: probabilities stay on
    the scale of the observed cancellation rate (they are later multiplied by money)."""
    params = dict(params or {})
    num_steps = [('impute', SimpleImputer(strategy='median'))]
    if model_name in {'logistic_regression', 'baseline_logistic'}:
        num_steps.append(('scale', StandardScaler()))
    transformers = [('num', Pipeline(num_steps), numeric)]
    if categorical:
        transformers.append(('cat', Pipeline([
            ('impute', SimpleImputer(strategy='most_frequent')),
            ('onehot', OneHotEncoder(handle_unknown='ignore', min_frequency=20, sparse_output=False)),
        ]), categorical))
    if model_name != 'baseline_logistic':
        transformers.append(('agent', Pipeline([
            ('top', TopAgentEncoder(top_agents)),
            ('onehot', OneHotEncoder(handle_unknown='ignore', sparse_output=False)),
        ]), ['agent_id']))
    prep = ColumnTransformer(transformers, remainder='drop', sparse_threshold=0.0)
    if model_name in {'logistic_regression', 'baseline_logistic'}:
        est = LogisticRegression(max_iter=5000, random_state=seed, C=params.get('C', 1.0))
    elif model_name == 'xgboost':
        from xgboost import XGBClassifier
        xgb_params = {k: v for k, v in params.items() if k != 'half_life_days'}
        est = XGBClassifier(objective='binary:logistic', eval_metric='logloss', tree_method='hist',
                            n_jobs=4, random_state=seed, **xgb_params)
    else:
        raise ValueError(f'Unknown model: {model_name}')
    return Pipeline([('preprocess', prep), ('model', est)])


def recency_weights(arrival_dates: pd.Series, fit_date, half_life_days) -> np.ndarray | None:
    """Exponential time-decay sample weights (weight 0.5 for a stay `half_life_days` before the fit date).
    None = equal weights. Used to let the model follow the upward drift of the cancellation rate."""
    if not half_life_days:
        return None
    age = (pd.Timestamp(fit_date) - pd.to_datetime(arrival_dates)).dt.days.clip(lower=0).to_numpy()
    return np.power(0.5, age / float(half_life_days))


def fit_pipeline(pipe: Pipeline, frame: pd.DataFrame, features: list[str], fit_date, half_life_days=None):
    w = recency_weights(frame['arrival_date'], fit_date, half_life_days)
    kwargs = {} if w is None else {'model__sample_weight': w}
    return pipe.fit(frame[features], frame['is_canceled'].astype(int), **kwargs)


# ---------------------------------------------------------------------------------------------
# Simple rules a hotel could apply without ML (fitted on the same training rows as the model)
# ---------------------------------------------------------------------------------------------
def lead_band(series: pd.Series) -> pd.Series:
    return pd.cut(series, bins=LEAD_BINS, labels=LEAD_LABELS, right=True).astype(str)


class RateTableRule:
    """Historical cancellation rate per cell of `keys`, shrunk towards the rate of the first key
    (m pseudo-bookings), then towards the overall rate. Example keys: market_segment x lead band."""

    def __init__(self, keys: list[str], m: int = 30):
        self.keys, self.m = keys, m

    def _frame(self, frame):
        f = frame.copy()
        if 'lead_band' in self.keys:
            f['lead_band'] = lead_band(f['lead_time'])
        return f

    def fit(self, frame: pd.DataFrame):
        f = self._frame(frame)
        y = f['is_canceled'].astype(int)
        self.global_ = float(y.mean())
        first = f.groupby(self.keys[0])['is_canceled'].agg(['sum', 'count'])
        self.first_ = ((first['sum'] + self.m * self.global_) / (first['count'] + self.m)).to_dict()
        self.cells_ = f.groupby(self.keys)['is_canceled'].agg(['sum', 'count'])
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        f = self._frame(frame)
        base = f[self.keys[0]].map(self.first_).fillna(self.global_).to_numpy(dtype=float)
        idx = pd.MultiIndex.from_frame(f[self.keys]) if len(self.keys) > 1 else pd.Index(f[self.keys[0]])
        cells = self.cells_.reindex(idx)
        s, c = cells['sum'].fillna(0).to_numpy(), cells['count'].fillna(0).to_numpy()
        return (s + self.m * base) / (c + self.m)


# ---------------------------------------------------------------------------------------------
# Calibration (optional; chosen on pre-test evidence in the notebook)
# ---------------------------------------------------------------------------------------------
def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit_calibrator(method: str, p, y):
    if method == 'none':
        return None
    if method == 'isotonic':
        return IsotonicRegression(out_of_bounds='clip').fit(p, y)
    if method == 'sigmoid':
        return LogisticRegression(C=1e6, max_iter=1000).fit(_logit(p).reshape(-1, 1), y)
    raise ValueError(method)


def apply_calibrator(cal, p):
    if cal is None:
        return np.asarray(p, dtype=float)
    if isinstance(cal, IsotonicRegression):
        return cal.predict(p)
    return cal.predict_proba(_logit(p).reshape(-1, 1))[:, 1]


# ---------------------------------------------------------------------------------------------
# Operating policy: capacity-based ranking within a scoring batch
# ---------------------------------------------------------------------------------------------
_MASK64 = (1 << 64) - 1


def id_tie_breaker(ids, seed: int = 0) -> np.ndarray:
    """Pseudo-random number in [0, 1) derived from each booking_id (splitmix64 hash, fixed seed).
    Ties between equal scores are broken by this number. Because it depends on the id and not on the
    row position, shuffling the input never changes a ranking or a tier. Never break ties by row order or
    by booking_id itself: booking_id follows the original file order, in which cancelled bookings are
    clustered, so it carries label information (a tied rule would look far better than it is)."""
    x = np.asarray(ids).astype(np.int64).astype(np.uint64)
    x = x + np.uint64((seed * 0x9E3779B97F4A7C15 + 0x9E3779B97F4A7C15) & _MASK64)
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    x = x ^ (x >> np.uint64(31))
    return (x >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def random_tie_breaker(n: int, seed: int = 0) -> np.ndarray:
    """Positional random order (kept only for frames without an id, e.g. bootstrap resamples).
    Prefer id_tie_breaker: a positional order changes when the rows are shuffled."""
    return np.random.default_rng(seed).random(n)


def _tie(n, ids):
    return id_tie_breaker(ids) if ids is not None else random_tie_breaker(n)


def rank_in_batch(p, batch, ids=None) -> pd.DataFrame:
    """Rank r (0 = highest risk) and size n of each booking's scoring batch, in input row order.
    Ties are broken by id_tie_breaker(ids), then by the id itself (fully deterministic)."""
    p = np.asarray(p, dtype=float)
    frame = pd.DataFrame({'p': p, 'b': np.asarray(batch), 't': _tie(len(p), ids),
                          'i': np.asarray(ids) if ids is not None else np.arange(len(p))})
    frame = frame.sort_values(['b', 'p', 't', 'i'], ascending=[True, False, True, True])
    frame['r'] = frame.groupby('b').cumcount()
    frame['n'] = frame.groupby('b')['p'].transform('size')
    return frame.sort_index()[['r', 'n']]


def rank_share(p, batch, ids=None) -> np.ndarray:
    """Share rank of each booking inside its scoring batch (0 = highest risk)."""
    rk = rank_in_batch(p, batch, ids)
    return (rk['r'] / rk['n']).to_numpy()


def capacity_slots(n, share: float, rounding: str = 'floor') -> np.ndarray:
    """Number of slots a batch of n bookings gets for a capacity share. 'floor' (default) treats the share
    as a hard ceiling: a batch never gets more than share x n actions, so batches below 1 / share bookings
    get no slot. 'ceil' guarantees at least one slot per non-empty batch and can exceed the share."""
    x = np.asarray(n, dtype=float) * share
    if rounding == 'floor':
        return np.floor(x + 1e-9).astype(int)
    if rounding == 'ceil':
        return np.ceil(x - 1e-9).astype(int)
    raise ValueError(rounding)


def assign_tier(p, batch, high_share: float, medium_share: float, ids=None, rounding: str = 'floor') -> np.ndarray:
    """high = the first capacity_slots(n, high_share) bookings of the batch, medium = the next ones up to
    capacity_slots(n, medium_share), low = the rest. Values match the CHECK constraint of
    hotel.risk_scores.risk_tier."""
    rk = rank_in_batch(p, batch, ids)
    n_high = capacity_slots(rk['n'], high_share, rounding)
    n_flag = capacity_slots(rk['n'], medium_share, rounding)
    r = rk['r'].to_numpy()
    return np.select([r < n_high, r < n_flag], ['high', 'medium'], 'low')


# ---------------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------------
def topk_stats(y, p, share, tie_breaker=None):
    """Cohort-level top-k metric (a metric definition, not the capacity policy): k = ceil(share x n),
    at least 1. Ties are broken by `tie_breaker` (pass id_tie_breaker(booking_id))."""
    y = np.asarray(y)
    order = np.lexsort((tie_breaker if tie_breaker is not None else random_tie_breaker(len(y)), -np.asarray(p)))
    k = max(1, int(np.ceil(share * len(y))))
    hits = y[order[:k]].sum()
    precision = hits / k
    return {'precision': precision, 'recall': hits / max(y.sum(), 1), 'lift': precision / y.mean()}


def metric_bundle(y, p, shares=(0.05, 0.10, 0.20), tie_breaker=None):
    out = {'roc_auc': roc_auc_score(y, p), 'pr_auc': average_precision_score(y, p)}
    for share in shares:
        tag = int(round(share * 100))
        for k, v in topk_stats(y, p, share, tie_breaker).items():
            out[f'{k}@{tag}%'] = v
    return out


def block_bootstrap(y, scores: dict, blocks, n_boot=500, seed=42, shares=(0.05, 0.10, 0.20)):
    """Resample whole blocks (arrival weeks) with replacement; the same draw is used for every scorer,
    so differences between scorers are paired."""
    rng = np.random.default_rng(seed)
    y = np.asarray(y)
    uniq, inv = np.unique(np.asarray(blocks), return_inverse=True)
    members = [np.flatnonzero(inv == i) for i in range(len(uniq))]
    jitter = rng.random(len(y))
    store = {name: [] for name in scores}
    for _ in range(n_boot):
        idx = np.concatenate([members[i] for i in rng.integers(0, len(uniq), len(uniq))])
        if y[idx].min() == y[idx].max():
            continue
        for name, s in scores.items():
            store[name].append(metric_bundle(y[idx], np.asarray(s)[idx], shares, jitter[idx]))
    return {name: pd.DataFrame(v) for name, v in store.items()}


def policy_bootstrap(y, flags: dict, blocks, n_boot=500, seed=42):
    """Paired bootstrap for a batch policy whose flags are already fixed per batch (e.g. weekly tiers):
    whole batches are resampled, so every resample keeps the same capacity rule. Returns per-draw
    precision and true positives for every policy."""
    rng = np.random.default_rng(seed)
    y = np.asarray(y)
    uniq, inv = np.unique(np.asarray(blocks), return_inverse=True)
    members = [np.flatnonzero(inv == i) for i in range(len(uniq))]
    out = {name: {'precision': [], 'tp': []} for name in flags}
    for _ in range(n_boot):
        idx = np.concatenate([members[i] for i in rng.integers(0, len(uniq), len(uniq))])
        for name, f in flags.items():
            f = np.asarray(f, dtype=bool)[idx]
            out[name]['tp'].append(int(y[idx][f].sum()))
            out[name]['precision'].append(float(y[idx][f].mean()) if f.any() else np.nan)
    return {name: pd.DataFrame(v) for name, v in out.items()}


# ---------------------------------------------------------------------------------------------
# Time contract of the operational simulation: weekly decision time, eligibility, action schedule
# ---------------------------------------------------------------------------------------------
def week_end(dates: pd.Series) -> pd.Series:
    """Decision time of a weekly batch: the Sunday that closes the booking week (W-SUN), as a date.
    All bookings created in that week are known then, so the weekly ranking can be computed."""
    return pd.to_datetime(dates).dt.to_period('W-SUN').dt.end_time.dt.normalize()


def assign_population(frame: pd.DataFrame, rule: str = 'A', block_size: int = 5) -> np.ndarray:
    """Population proxy of notebook 03. Rule A: Groups OR Non Refund OR dup_group_size >= block_size
    (dup_group_size is a retrospective snapshot count). Rule B: Groups OR Non Refund only."""
    grp = frame['market_segment'].eq('Groups') | frame['deposit_type'].eq('Non Refund')
    if rule == 'A':
        grp = grp | frame['dup_group_size'].ge(block_size)
    elif rule != 'B':
        raise ValueError(rule)
    return np.where(grp, 'group_block', 'individual')


def eligibility(frame: pd.DataFrame, decision_at) -> pd.DataFrame:
    """Decision-table §2 checks at the decision time, for the conditions the extract can verify.
    Uses reservation_status / reservation_status_date ONLY to know whether the booking was already
    cancelled at the decision time (never as a feature). Consent and open actions are not in the data:
    they are reported as 'unknown', never assumed to pass. The same holds for the booking status: if the
    status columns are missing, or null for a row (or a Canceled row has no status date), that row's
    active_at_decision is 'unknown' and it is NOT eligible. With complete status data the output is
    unchanged (plain booleans)."""
    d = pd.to_datetime(pd.Series(decision_at, index=frame.index))
    out = pd.DataFrame(index=frame.index)
    out['created_by_decision'] = frame['booking_date'].le(d)
    out['arrival_after_decision'] = frame['arrival_date'].gt(d)
    if {'reservation_status', 'reservation_status_date'} <= set(frame.columns):
        status = frame['reservation_status']
        status_date = pd.to_datetime(frame['reservation_status_date'])
        status_known = status.notna() & (status.ne('Canceled') | status_date.notna())
        cancelled = status.eq('Canceled') & status_date.le(d)
    else:
        status_known = pd.Series(False, index=frame.index)
        cancelled = pd.Series(False, index=frame.index)
    active_ok = status_known & ~cancelled
    if status_known.all():
        out['active_at_decision'] = active_ok
    else:
        out['active_at_decision'] = active_ok.astype(object).where(status_known, 'unknown')
    comp = frame['market_segment'].eq('Complementary')
    if 'adr_clean' in frame.columns:
        comp = comp & frame['adr_clean'].fillna(0).eq(0)
    out['not_complimentary'] = ~comp
    out['consent_status'] = 'unknown'
    out['open_action_check'] = 'unknown'
    out['eligible_known_checks'] = (out['created_by_decision'] & out['arrival_after_decision'] & active_ok
                                    & out['not_complimentary'])
    reason = np.select([~out['created_by_decision'], ~out['arrival_after_decision'], ~status_known,
                        cancelled, ~out['not_complimentary']],
                       ['not created yet', 'arrived', 'status unknown', 'already cancelled',
                        'complimentary (adr 0)'], '')
    out['ineligible_reason'] = reason
    return out


def plan_rule(tier: str, days_to_arrival: int) -> tuple[str, str, str]:
    """Decision table §3 rule for an individual booking at its decision time -> (rule, owner, approval).
    I5 (<= 7 days) overrides I3; I2 needs >= 8 days; everything else is I1."""
    if tier == 'high':
        return ('I3', 'Reservations / front office', 'none (internal task)') if days_to_arrival >= 8 else \
               ('I5', 'Revenue manager', '-')
    if tier == 'medium' and days_to_arrival >= 8:
        return ('I2', 'n8n, monitored by Reservations', 'auto')
    return ('I1', 'n8n, monitored by Reservations', 'auto')


def schedule_actions(booking_id, decision_at, arrival_date, rule, i2_milestones=(30, 14), i1_reminder_days=7,
                     max_i2_messages=2) -> list[dict]:
    """Dated actions of one booking, never before its decision time (decision table §3):
    I3 -> phone call (attempt 1) at the decision time; I5 -> no guest action (buffer count only);
    I2 -> personalised reminders at the milestones still ahead; if a milestone has already passed when the
         booking enters the band, one reminder immediately, then the remaining milestones (max 2 messages);
    I1 -> standard confirmation at the decision time + reminder 7 days before arrival if that is still ahead."""
    d, a = pd.Timestamp(decision_at), pd.Timestamp(arrival_date)
    rows = []
    def add(when, action_type, reason):
        rows.append({'booking_id': booking_id, 'decision_at': d, 'scheduled_at': pd.Timestamp(when), 'rule': rule,
                     'action_type': action_type, 'schedule_reason': reason})
    if rule == 'I3':
        add(d, 'phone_call', 'call queue (attempt 1 of max 2)')
    elif rule == 'I2':
        due = [(m, a - pd.Timedelta(days=m)) for m in i2_milestones]
        ahead = [(m, t) for m, t in due if t >= d]
        if len(ahead) < len(due):                 # entered the band after a milestone had passed
            add(d, 'reminder', 'immediate (milestone already passed at decision time)')
            ahead = [(m, t) for m, t in ahead if t > d]
        for m, t in ahead:
            add(t, 'reminder', f'milestone {m} days before arrival')
        rows[:] = rows[:max_i2_messages]
    elif rule == 'I1':
        add(d, 'confirm_email', 'standard confirmation')
        t = a - pd.Timedelta(days=i1_reminder_days)
        if t > d:
            add(t, 'reminder', f'standard reminder {i1_reminder_days} days before arrival')
    elif rule != 'I5':
        raise ValueError(rule)
    for i, r in enumerate(rows, 1):
        r['message_no'] = i
    return rows


# ---------------------------------------------------------------------------------------------
# Bundle: one file with everything needed to score a booking
# ---------------------------------------------------------------------------------------------
def load_bundle(path):
    return joblib.load(path)


def score(bundle: dict, frame: pd.DataFrame) -> np.ndarray:
    """p_cancel of the INDIVIDUAL model for rows of modeling_dataset.csv (joined with agent_id). Applies the
    calibrator stored in the bundle (None = raw model probabilities). No population or time check: use
    score_bookings() for the two-track system and the operational time contract."""
    missing = [c for c in bundle['features'] if c not in frame.columns]
    if missing:
        raise KeyError(f'Missing input columns: {missing}')
    raw = bundle['model'].predict_proba(frame[bundle['features']])[:, 1]
    return apply_calibrator(bundle['calibrator'], raw)


def score_bookings(bundle: dict, frame: pd.DataFrame, score_time) -> pd.DataFrame:
    """Two-track scorer of the hand-over: population rule -> individual model or group rule, with the time
    contract and the eligibility checks the data allows. Raises if the score time is on or before the fit
    date of the model or of the group rule (an operational score cannot come from a model that did not
    exist yet). Tiers are not set here: they need the whole weekly batch (assign_tier)."""
    score_time = pd.Timestamp(score_time)
    for key in ['fit_date', 'selection_cutoff', 'group_fit_date']:
        if score_time <= pd.Timestamp(bundle[key]):
            raise ValueError(f'score_time {score_time.date()} is not after {key} {bundle[key]}')
    pop_rule = bundle['population_rule']
    track = assign_population(frame, pop_rule['rule'], pop_rule['block_size'])
    out = pd.DataFrame({'booking_id': frame['booking_id'].to_numpy(), 'score_time': score_time,
                        'population': track}, index=frame.index)
    out['p_cancel'] = np.nan
    ind = track == 'individual'
    if ind.any():
        out.loc[ind, 'p_cancel'] = score(bundle, frame[ind])
    if (~ind).any():
        out.loc[~ind, 'p_cancel'] = bundle['group_rule'].predict(frame[~ind])
    out['model_version'] = np.where(ind, bundle['run_id'], 'group-rule_' + bundle['run_id'])
    out['days_to_arrival'] = (frame['arrival_date'] - score_time).dt.days.to_numpy()
    return out.join(eligibility(frame, score_time))
