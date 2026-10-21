"""README §6.3 — text analytics on cancellation reasons: TF-IDF -> K-Means -> labelled clusters ->
reason mix by segment / channel -> message angle per cluster.

The reason texts in data/synthetic/cx_feedback.csv are GENERATED FROM TEMPLATES (config/assumptions.yaml,
`cancellation_reason`). This run demonstrates the pipeline; it says nothing about real guests.
A cancellation reason is known only after the guest cancels: it is never a model feature and
never used to score or rank bookings; it only informs the wording of contacts.

    python src/text_clustering.py          # writes artifacts/text_clustering/
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics import adjusted_rand_score, silhouette_score

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'text_clustering'
SEED = 42
K_GRID = range(3, 31)
MIXED_PURITY = 0.6  # below this a cluster has no dominant reason: labelled 'mixed'
# Courtesy words carry no reason; dropping them keeps clusters about the reason itself.
COURTESY = {'dear', 'hotel', 'thanks', 'thank', 'regards', 'sorry', 'hello', 'hi', 'best',
            'kind', 'unfortunately', 'apologies', 'please',
            # words every cancellation shares (they say nothing about the reason)
            'booking', 'reservation', 'cancel', 'cancelled', 'cancellation', 'confirm', 'room', 'rooms'}
# Message angle per reason (team suggestion for CX to review; not a tested effect).
ANGLE = {
    'change_of_plans': 'Offer a date / room change (I4) before the guest cancels',
    'found_better_price': 'Remind of what is included / best-rate terms; no unapproved discount',
    'duplicate_booking': 'Ask the guest to confirm which booking to keep (prevents double holds)',
    'group_size_change': 'Offer to adjust the room / guest count instead of cancelling',
    'travel_restriction_or_illness': 'Explain change / cancellation conditions with empathy',
    'other': 'Standard confirmation; ask if anything should change',
}


def load(root: Path = ROOT) -> pd.DataFrame:
    fb = pd.read_csv(root / 'data/synthetic/cx_feedback.csv')
    bk = pd.read_csv(root / 'data/processed/bookings_prepared.csv',
                     usecols=['booking_id', 'is_canceled', 'market_segment', 'distribution_channel'])
    df = fb.merge(bk, on='booking_id', validate='one_to_one')
    df = df[df['is_canceled'].eq(1) & df['cancellation_reason_text'].notna()].copy()
    df['clean'] = df['cancellation_reason_text'].str.lower().map(lambda s: re.sub(r'[^a-z ]+', ' ', s))
    return df.reset_index(drop=True)


def run(root: Path = ROOT, out: Path = OUT) -> dict:
    df = load(root)
    vec = TfidfVectorizer(stop_words=list(ENGLISH_STOP_WORDS | COURTESY), ngram_range=(1, 2),
                          min_df=5, sublinear_tf=True)
    X = vec.fit_transform(df['clean'])
    rng = np.random.default_rng(SEED)
    sample = rng.choice(X.shape[0], size=min(5000, X.shape[0]), replace=False)

    rows = []
    for k in K_GRID:
        km = KMeans(n_clusters=k, n_init=10, random_state=SEED).fit(X)
        rows.append({'k': k, 'silhouette_sample': silhouette_score(X[sample], km.labels_[sample]),
                     'inertia': km.inertia_})
    k_sel = pd.DataFrame(rows)
    k_best = int(k_sel.loc[k_sel['silhouette_sample'].idxmax(), 'k'])
    km = KMeans(n_clusters=k_best, n_init=10, random_state=SEED).fit(X)
    df['cluster'] = km.labels_

    # Label each cluster by its top TF-IDF terms and by the generator category it mostly holds.
    terms = np.array(vec.get_feature_names_out())
    order = km.cluster_centers_.argsort(axis=1)[:, ::-1]
    cat = (df.groupby('cluster')['cancellation_reason_category']
             .agg(lambda s: s.value_counts().idxmax()).rename('main_category'))
    purity = (df.groupby('cluster')['cancellation_reason_category']
                .agg(lambda s: s.value_counts(normalize=True).iloc[0]).rename('purity'))
    clusters = pd.DataFrame({
        'cluster': range(k_best),
        'size': df['cluster'].value_counts().sort_index().to_numpy(),
        'top_terms': [', '.join(terms[order[c, :8]]) for c in range(k_best)],
    }).join(cat, on='cluster').join(purity, on='cluster')
    # The analyst's labelling step is stood in for by the generator category; a cluster without
    # a dominant reason is not given one.
    mixed = clusters['purity'] < MIXED_PURITY
    clusters['label'] = clusters['main_category'].str.replace('_', ' ').where(~mixed, 'mixed / unspecified')
    clusters['message_angle'] = clusters['main_category'].map(ANGLE).fillna(ANGLE['other'])
    clusters.loc[mixed, 'message_angle'] = ANGLE['other']
    clusters['share'] = clusters['size'] / clusters['size'].sum()

    label_of = clusters.set_index('cluster')['label']
    df['label'] = df['cluster'].map(label_of)
    mix_segment = pd.crosstab(df['market_segment'], df['label'], normalize='index').round(4)
    mix_channel = pd.crosstab(df['distribution_channel'], df['label'], normalize='index').round(4)
    versus = pd.crosstab(df['cluster'], df['cancellation_reason_category'])

    summary = {
        'texts': int(len(df)), 'distinct_texts': int(df['cancellation_reason_text'].nunique()),
        'vocabulary': int(len(terms)), 'k_grid': list(K_GRID), 'k_selected': k_best,
        'silhouette_selected': float(k_sel.set_index('k').at[k_best, 'silhouette_sample']),
        'adjusted_rand_vs_generator_category': float(adjusted_rand_score(df['cancellation_reason_category'], df['cluster'])),
        'mean_cluster_purity': float((clusters['purity'] * clusters['share']).sum()),
        'mixed_share': float(clusters.loc[clusters['purity'] < MIXED_PURITY, 'share'].sum()),
        'label_agreement_with_generator': float((df['label'].str.replace(' ', '_') == df['cancellation_reason_category']).mean()),
        'k_note': 'k is chosen by silhouette; with template text it tracks phrasings, so clusters are read at label level (several clusters share a reason label).',
        'mixed_purity_threshold': MIXED_PURITY,
        'note': 'Synthetic template text: shows the pipeline recovers the generator categories, '
                'not real guest reasons. Post-outcome information: never a model feature.',
    }

    out.mkdir(parents=True, exist_ok=True)
    k_sel.to_csv(out / 'k_selection.csv', index=False)
    clusters.to_csv(out / 'clusters.csv', index=False)
    versus.to_csv(out / 'cluster_vs_generator_category.csv')
    mix_segment.to_csv(out / 'reason_mix_by_segment.csv')
    mix_channel.to_csv(out / 'reason_mix_by_channel.csv')
    (out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 4))
    segs = mix_segment.loc[df['market_segment'].value_counts().index[:6]]
    left = np.zeros(len(segs))
    greys = plt.cm.Greys(np.linspace(0.25, 0.85, segs.shape[1]))
    for i, col in enumerate(segs.columns):
        ax.barh(segs.index, segs[col], left=left, color=greys[i], label=col, height=0.6)
        left += segs[col].to_numpy()
    ax.set_xlabel('Share of cancellations with a reason text')
    ax.set_title('Cancellation reason clusters by market segment (synthetic text)', loc='left', fontsize=11)
    ax.legend(fontsize=8, ncol=2, frameon=False, loc='upper center', bbox_to_anchor=(0.5, -0.18))
    ax.spines[['top', 'right']].set_visible(False)
    fig.savefig(out / 'fig_reason_mix_by_segment.png', dpi=160, bbox_inches='tight')
    plt.close(fig)
    return summary


if __name__ == '__main__':
    s = run()
    print(json.dumps(s, indent=2))
