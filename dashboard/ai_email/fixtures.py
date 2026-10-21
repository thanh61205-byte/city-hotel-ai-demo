"""Labelled demo fixtures and manual drafts: drafts without a model call.

- fixture_output: reminder and booking confirmation from fixed templates; guest_reply only for
  the sample messages of n8n/ai_email/fixtures_v1.json, each with a labelled analysis. Labelled
  "Demo fixture — not an AI prediction" (analysis_mode = fixture).
- manual_output: free text without a model: staff choose the label, the template gives the
  wording (analysis_mode = manual). Nothing pretends a model read the message.

n8n's mock branch (n8n/ai_email/src/fixture_output.js) reads the same JSON file.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from dashboard.ai_email import contract as c
from dashboard.ai_email.validate import norm

FIXTURES = Path(__file__).resolve().parents[2] / 'n8n' / 'ai_email' / 'fixtures_v1.json'
FIXTURE_LABEL = 'Demo fixture — not an AI prediction'


@lru_cache(maxsize=1)
def data() -> dict:
    return json.loads(FIXTURES.read_text(encoding='utf-8'))


def samples() -> list[dict]:
    return data()['guest_reply_samples']


def _refs(refs: list[str], context: dict) -> list[str]:
    out = []
    for r in refs:
        if r == '$booking':
            out.append(f"booking:{context['booking']['booking_id']}")
        elif r == '$g1_reminder':
            nxt = c.next_reminder(context)
            if nxt:
                out.append(nxt['source_id'])
        else:
            out.append(r)
    return out


def find_sample(text: str) -> dict | None:
    key = norm(text)
    return next((s for s in samples() if norm(s['text']) == key), None)


def _draft(template: dict, context: dict, refs: list[str], analysis: dict | None) -> dict:
    a = analysis or {}
    return {
        'email_type': context['email_type'],
        'intent': a.get('intent'), 'certainty': a.get('certainty'),
        'evidence_quotes': list(a.get('evidence_quotes', [])),
        'requested_changes': [dict(x) for x in a.get('requested_changes', [])],
        'missing_information': list(a.get('missing_information', [])),
        'requires_clarification': bool(a.get('requires_clarification', False)),
        'draft_subject': template['subject'], 'draft_body': template['body'],
        'source_refs': _refs(refs, context), 'requires_human_review': True,
    }


def fixture_output(context: dict) -> dict | None:
    """The fixed draft of this context, or None (guest message not among the samples)."""
    et, lang = context['email_type'], context['language']
    t = data()['templates']
    if et != 'guest_reply':
        tpl = t[et][lang]
        return _draft(tpl, context, tpl['source_refs'], None)
    sample = find_sample(context['guest_message']['text'])
    if sample is None:
        return None
    a = sample['analysis']
    return _draft(t['guest_reply'][a['intent']][lang], context, data()['guest_reply_source_refs'], a)


def manual_output(context: dict, *, intent: str, certainty: str) -> dict:
    """guest_reply labelled by staff (no model): template wording for the chosen intent."""
    if context['email_type'] != 'guest_reply':
        raise ValueError('Manual labels apply to Reply to guest only')
    if intent not in c.INTENTS or certainty not in c.CERTAINTIES:
        raise ValueError('Choose an intent and a certainty')
    if intent == 'cancellation_requested' and certainty != 'explicit':
        raise ValueError('A tentative or ambiguous cancellation is labelled "unclear"')
    msg = context['guest_message']['text']
    quote = msg[:300].strip()
    changes = []
    if intent == 'change_requested':
        changes = [{'field': 'other', 'raw_text': quote, 'proposed_value': None, 'evidence': quote,
                    'ambiguity_reason': 'Labelled manually by staff: details to confirm with the guest'}]
    clar = intent in ('unclear', 'change_requested') or certainty != 'explicit'
    missing = (['Details of the requested change (labelled manually)'] if intent == 'change_requested'
               else ['Final decision of the guest'] if intent == 'unclear' else [])
    analysis = {'intent': intent, 'certainty': certainty, 'evidence_quotes': [quote],
                'requested_changes': changes, 'missing_information': missing,
                'requires_clarification': clar}
    tpl = data()['templates']['guest_reply'][intent][context['language']]
    return _draft(tpl, context, data()['guest_reply_source_refs'], analysis)
