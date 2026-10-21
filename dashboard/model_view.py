"""Model monitoring (dashboard page "Model monitoring"): how well the risk scores the team used
have ranked the bookings that already arrived before today.

Only bookings that arrived before today are measured: their outcome (cancelled or not) is known
by now. Each booking keeps the score it got in its weekly batch, the one its recommendation came
from. Every figure is computed here from those bookings; notebook 04's figures are not used,
because they include arrivals after today.

Sources:
  operational weekly queue (dashboard/data_contract.py)   p_cancel, risk tier, segment, value
  data/processed/bookings_prepared.csv                    is_canceled (known after arrival)
The outcome never reaches the queue, the schedule or the action log.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from dashboard.data_contract import REPO

TOP_SHARE = 0.20  # the report's review share (precision / recall / lift at the top 20%)


def outcome_path(repo: str | Path = REPO) -> Path:
    return Path(repo) / 'data' / 'processed' / 'bookings_prepared.csv'


def outcome_fingerprint(repo: str | Path = REPO) -> tuple:
    p = outcome_path(repo)
    return (p.name, p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else ()


def outcomes(repo: str | Path = REPO) -> pd.Series:
    """is_canceled per booking_id (1 = cancelled or no-show), known only after arrival."""
    df = pd.read_csv(outcome_path(repo), usecols=['booking_id', 'is_canceled'])
    return df.set_index('booking_id')['is_canceled'].astype(int)


def arrived(queue: pd.DataFrame, outcome: pd.Series, today: date,
            start: date | None = None) -> pd.DataFrame:
    """Individual bookings that arrived before today (from `start` on, if given), with outcome."""
    ind = queue[queue['track'].eq('individual model') & (queue['arrival_date'] < today)]
    if start is not None:
        ind = ind[ind['arrival_date'] >= start]
    return ind.assign(is_canceled=ind['booking_id'].map(outcome)).dropna(subset=['is_canceled'])


def _ranked(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(['p_cancel', 'booking_id'], ascending=[False, True])


def roc_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Probability that a cancelled booking scores above a kept one (ties count half)."""
    pos, n = int(y.sum()), len(y)
    if pos in (0, n):
        return float('nan')
    ranks = pd.Series(p).rank(method='average').to_numpy()
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * (n - pos)))


def pr_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Average precision: precision at each score threshold, weighted by the recall it adds."""
    pos = int(y.sum())
    if pos == 0:
        return float('nan')
    order = np.argsort(-p, kind='mergesort')
    p, y = p[order], y[order]
    last = np.r_[np.diff(p) != 0, True]  # last row of each distinct score
    tp = np.cumsum(y)[last]
    seen = (np.arange(1, len(y) + 1))[last]
    precision, recall = tp / seen, tp / pos
    return float(np.sum(np.diff(np.r_[0.0, recall]) * precision))


def top_share_capture(df: pd.DataFrame, share: float = TOP_SHARE) -> dict:
    """The top `share` of bookings by p_cancel (ties by booking_id): what it reaches."""
    n = int(np.floor(share * len(df) + 1e-9))
    top = _ranked(df).head(n)
    cancelled = df['is_canceled'].eq(1)
    base = float(cancelled.mean()) if len(df) else float('nan')
    precision = float(top['is_canceled'].mean()) if n else float('nan')
    gross_cancelled = df.loc[cancelled, 'gross_value'].sum()
    return {
        'bookings': n,
        'precision': precision,
        'recall': float(top['is_canceled'].sum() / cancelled.sum()) if cancelled.any() else float('nan'),
        'lift': precision / base if base else float('nan'),
        'cancelled_gross_reached': (float(top.loc[top['is_canceled'].eq(1), 'gross_value'].sum()
                                          / gross_cancelled) if gross_cancelled else float('nan')),
    }


def metrics(df: pd.DataFrame) -> dict:
    """Ranking and calibration of the scores on arrived bookings."""
    y = df['is_canceled'].to_numpy(dtype=int)
    p = df['p_cancel'].to_numpy(dtype=float)
    top = top_share_capture(df)
    return {'bookings': len(df), 'cancel_rate': float(y.mean()) if len(y) else float('nan'),
            'mean_p': float(p.mean()) if len(p) else float('nan'),
            'roc_auc': roc_auc(y, p), 'pr_auc': pr_auc(y, p),
            'brier': float(np.mean((p - y) ** 2)) if len(y) else float('nan'),
            'precision@20%': top['precision'], 'recall@20%': top['recall'], 'lift@20%': top['lift']}


def deciles(df: pd.DataFrame) -> pd.DataFrame:
    """Predicted and actual cancellation rate per risk decile (1 = highest risk)."""
    ranked = _ranked(df).reset_index(drop=True)
    ranked['risk_decile'] = (np.arange(len(ranked)) * 10 // max(len(ranked), 1)) + 1
    return (ranked.groupby('risk_decile', as_index=False)
                  .agg(bookings=('booking_id', 'size'), mean_p=('p_cancel', 'mean'),
                       cancel_rate=('is_canceled', 'mean')))


def by_week(df: pd.DataFrame) -> pd.DataFrame:
    """Predicted and actual cancellation rate per arrival week."""
    week = pd.to_datetime(df['arrival_date']).dt.to_period('W-SUN').dt.start_time
    return (df.assign(week=week).groupby('week', as_index=False)
              .agg(bookings=('booking_id', 'size'), predicted=('p_cancel', 'mean'),
                   actual=('is_canceled', 'mean')))


def by_segment(df: pd.DataFrame) -> pd.DataFrame:
    return (df.groupby('market_segment', as_index=False)
              .agg(bookings=('booking_id', 'size'), mean_p=('p_cancel', 'mean'),
                   cancel_rate=('is_canceled', 'mean'))
              .sort_values('bookings', ascending=False))
