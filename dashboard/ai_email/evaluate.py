"""Evaluation of the AI drafts (docs/SPEC_AI_N8N_VI.md §8). Two separate scores, never mixed:

1. Reply to guest: intent classification on the labelled set
   tests/fixtures/ai_email/guest_reply_labelled.jsonl (40 sentences, 10 per intent, split
   dev / test BEFORE any prompt change; prompts are tuned on dev only). Confusion matrix,
   precision / recall per intent and macro-F1 on the test split, with sample size and
   model / prompt version. Hard cases (negation, condition, relative dates, injection) also
   have an expected behaviour that is checked separately.
2. Reminder / booking confirmation: factual correctness and completeness
   (tests/fixtures/ai_email/factual_cases.json): the server's fact blockers per case, and for a
   live run, no content blocker in the model's wording.

Fixtures are never scored as AI predictions. A live run needs the shared PostgreSQL store and
workflow A with live AI; without them this module reports "not run" instead of numbers.

    python -m dashboard.ai_email.evaluate --split test --out output/ai_email_eval
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from dashboard.ai_email import contract as c, validate

ROOT = Path(__file__).resolve().parents[2]
LABELLED = ROOT / 'tests' / 'fixtures' / 'ai_email' / 'guest_reply_labelled.jsonl'
FACTUAL = ROOT / 'tests' / 'fixtures' / 'ai_email' / 'factual_cases.json'


def labelled(split: str | None = None) -> list[dict]:
    rows = [json.loads(line) for line in LABELLED.read_text(encoding='utf-8').splitlines() if line.strip()]
    return [r for r in rows if split is None or r['split'] == split]


def metrics(gold: list[str], pred: list[str | None], labels=c.INTENTS) -> dict:
    """Confusion matrix (gold x predicted; None = no valid prediction), per-intent precision /
    recall / F1 and macro-F1 over the intents."""
    cm = {g: Counter() for g in labels}
    for g, p in zip(gold, pred):
        cm[g][p if p in labels else 'invalid'] += 1
    per = {}
    for lab in labels:
        tp = cm[lab][lab]
        fp = sum(cm[g][lab] for g in labels if g != lab)
        fn = sum(cm[lab].values()) - tp
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        per[lab] = {'precision': prec, 'recall': rec, 'f1': 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
                    'support': tp + fn}
    return {'n': len(gold), 'confusion': {g: dict(cm[g]) for g in labels}, 'per_intent': per,
            'macro_f1': sum(v['f1'] for v in per.values()) / len(labels),
            'accuracy': sum(g == p for g, p in zip(gold, pred)) / len(gold) if gold else 0.0}


def factual_context(case: dict, base: dict, language: str = 'en', workspace_id: str = 'eval') -> dict:
    booking = {**base['booking'], **case.get('booking', {})}
    schedule = case.get('schedule', base['schedule'])
    return c.build_context(workspace_id=workspace_id, email_type=case['email_type'], language=language,
                           business_as_of=base['business_as_of'], source_run_id='eval', policy_version='eval',
                           booking=booking, schedule_rows=schedule,
                           g2_stop_outcome=case.get('g2_stop_outcome', base.get('g2_stop_outcome')))


def factual_report(drafts: dict[str, dict] | None = None) -> list[dict]:
    """Per case: expected vs computed fact blockers, and (when drafts are given, e.g. from a
    live run) the content blockers of the draft wording."""
    spec = json.loads(FACTUAL.read_text(encoding='utf-8'))
    out = []
    for case in spec['cases']:
        ctx = factual_context(case, spec['base'])
        got = sorted(b['code'] for b in validate.fact_blockers(ctx))
        row = {'id': case['id'], 'email_type': case['email_type'], 'expected': sorted(case['expected_fact_blockers']),
               'computed': got, 'facts_ok': got == sorted(case['expected_fact_blockers'])}
        d = (drafts or {}).get(case['id'])
        if d is not None:
            errors = validate.validate_output(ctx, d)
            checked = validate.check_revision(ctx, d.get('draft_subject', ''), d.get('draft_body', '')) if not errors else None
            row['schema_errors'] = errors
            row['content_blockers'] = [] if checked is None else [b['code'] for b in checked['send_blockers']
                                                                  if b['code'] not in got]
        out.append(row)
    return out


def run_live(cfg, store, split: str, language_from_text: bool = True) -> dict:
    """Send every labelled sentence of `split` through workflow A with live AI (one request each)
    and score the stored results. Costs AI credit: run on purpose only."""
    from dashboard.ai_email import client
    if not (cfg.n8n_draft and cfg.live_ai):
        raise RuntimeError('Live evaluation needs workflow A with live AI and the shared PostgreSQL store')
    base = json.loads(FACTUAL.read_text(encoding='utf-8'))['base']
    store.ensure_workspace(cfg.workspace_id, source_run_id='eval', policy_version='eval',
                           business_as_of=base['business_as_of'])
    gold, pred, rows = [], [], []
    for r in labelled(split):
        msg = {'text': r['text'], 'message_business_at': base['business_as_of'], 'source': 'sample_input'}
        ctx = c.build_context(workspace_id=cfg.workspace_id, email_type='guest_reply', language=r['language'],
                              business_as_of=base['business_as_of'], source_run_id='eval', policy_version='eval',
                              booking=base['booking'], schedule_rows=base['schedule'], guest_message=msg)
        req = store.create_request(ctx, engine='n8n', requested_mode='live_ai', requested_by='evaluation')
        client.request_draft(cfg, req['request_id'])
        req = store.get_request(req['request_id'])
        out = req['result_json'] if req['status'] == 'succeeded' else None
        p = out.get('intent') if out else None
        gold.append(r['intent'])
        pred.append(p)
        rows.append({'id': r['id'], 'gold': r['intent'], 'pred': p, 'certainty': out and out.get('certainty'),
                     'status': req['status'], 'error': req['error'], 'tags': r['tags'],
                     'requires_clarification': out and out.get('requires_clarification'),
                     'model': req['model'], 'prompt_version': req['prompt_version']})
    return {'split': split, 'metrics': metrics(gold, pred), 'rows': rows,
            'run_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds')}


def write_report(result: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"intent_eval_{result['split']}_{date.today():%Y%m%d}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--split', default='test', choices=['dev', 'test'])
    ap.add_argument('--out', default=str(ROOT / 'output' / 'ai_email_eval'))
    ap.add_argument('--factual-only', action='store_true', help='server fact checks only (no AI call)')
    args = ap.parse_args(argv)
    for row in factual_report():
        print(f"{row['id']:<24} {row['email_type']:<21} facts {'OK ' if row['facts_ok'] else 'BAD'} {row['computed']}")
    if args.factual_only:
        return 0
    from dashboard.ai_email import config, store as st
    cfg = config.load()
    if not (cfg.n8n_draft and cfg.live_ai):
        print('Intent evaluation NOT RUN: live AI through n8n workflow A and G2_AI_DB_URL are not configured. '
              'No metrics are reported.')
        return 2
    store = st.AiStore(cfg.db_target)
    result = run_live(cfg, store, args.split)
    m = result['metrics']
    print(f"n={m['n']} macro-F1={m['macro_f1']:.3f} accuracy={m['accuracy']:.3f}")
    print(f'Report: {write_report(result, Path(args.out))}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
