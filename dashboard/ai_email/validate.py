"""Checks of a draft. Valid JSON does not prove a correct email: three separate layers.

1. validate_output: the model / fixture output against the contract of its email type (exact
   keys, enums, evidence quotes really in the guest message, source_refs from the context,
   requested dates only when the guest wrote a full date). An invalid output is NOT a draft.
   n8n's "Validate output" node (n8n/ai_email/src/validate_output.js) runs the same rules; the
   shared cases are in tests/fixtures/ai_email/validator_cases.json.
2. send_blockers: computed by the server from the context and the rendered email, never taken
   from the model: missing booking identity or essential facts of the email type, booking no
   longer eligible, unresolved placeholders, dates / prices / links not in the sources,
   unsupported claims ("cancelled", "refunded", "rooms available"), internal risk wording.
3. Staff read the email before approving: these checks cannot prove every sentence is true.

missing_information is shown but does not block: a draft that asks the guest for the new date
is exactly what should be sent when the date is missing.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from dashboard.ai_email import contract as c, dates, render

# Claims a draft may not make without an event that proves them (docs/SPEC_AI_N8N_VI.md §4).
FORBIDDEN_CLAIMS = [
    r'\b(has|have|had) been (cancel+ed|changed|modified|moved|refunded|rebooked)\b',
    r'\bwe (have|\'ve) (cancel+ed|changed|modified|moved|refunded)\b',
    r'\b(is|are) (now )?(cancel+ed|confirmed as changed)\b',
    r'\bwill be refunded\b', r'\b(full|partial) refund\b', r'\brefund of\b',
    r'\brooms? (is |are )?(still )?available\b', r'\b(is|are) available for (your|the) new\b',
    r'\bfree of charge\b', r'\bno (extra |additional )?(fee|charge|cost)s?\b',
    r'\bwithout (any )?(fee|charge|penalty)\b',
    r'đã (được )?(hủy|huỷ)', r'đã (được )?(đổi|thay đổi|dời|lùi) (ngày|phòng|sang|đặt phòng)',
    r'đã (được )?hoàn tiền', r'được hoàn tiền', r'sẽ được hoàn', r'hoàn (lại )?toàn bộ',
    r'còn phòng', r'miễn phí', r'không (mất|tính) (phí|thêm phí)',
]
_CLAIMS = [re.compile(p, re.I) for p in FORBIDDEN_CLAIMS]
_MONEY = re.compile(r'[€$£]\s?\d|\d\s?[€$£]|\b(EUR|USD|GBP|VND|VNĐ)\b|\beuros?\b|'
                    r'\d[\d.,]*\s?(đồng|đ|nghìn|triệu)(?!\w)', re.I)
_LINK = re.compile(r'[\w.+-]+@[\w-]+(\.[\w-]+)+|https?://|\bwww\.', re.I)
_INTERNAL = re.compile(r'\b(p_cancel|risk (score|tier|level)|cancellation risk|probability|'
                       r'high[- ]risk|tier (high|medium|low))\b|rủi ro|xác suất|nguy cơ hủy', re.I)
_GENERIC_PLACEHOLDER = re.compile(r'\[(?!\[)[^\]\n]{1,40}\](?!\])|<[^<>\n]{1,40}>|\bXXX+\b|\bTBD\b|'
                                  r'\bTODO\b|\{\{|\}\}', re.I)


def norm(text: str) -> str:
    t = unicodedata.normalize('NFC', str(text or '')).lower()
    t = t.translate(str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'", '{': '(', '}': ')'}))
    return ' '.join(t.split())


def in_message(quote: str, message: str) -> bool:
    q = norm(quote).strip(' .,!?;:"\'')
    return bool(q) and q in norm(message)


def _is_str(v, lo=1, hi=None) -> bool:
    return isinstance(v, str) and len(v.strip()) >= lo and (hi is None or len(v) <= hi)


def validate_output(context: dict, out) -> list[str]:
    """Errors of a draft output for this context (empty list = valid)."""
    if not isinstance(out, dict):
        return ['output must be a JSON object']
    errors = []
    keys = set(out)
    missing, extra = set(c.OUTPUT_KEYS) - keys, keys - set(c.OUTPUT_KEYS)
    if missing:
        errors.append(f'missing keys: {sorted(missing)}')
    if extra:
        errors.append(f'unexpected keys: {sorted(extra)}')
    if missing:
        return errors
    et = context['email_type']
    if out['email_type'] != et:
        errors.append(f"email_type {out['email_type']!r} does not match the request ({et})")
    if not _is_str(out['draft_subject'], 1, c.MAX_SUBJECT):
        errors.append(f'draft_subject must be text of 1-{c.MAX_SUBJECT} characters')
    if not _is_str(out['draft_body'], 1, c.MAX_BODY):
        errors.append(f'draft_body must be text of 1-{c.MAX_BODY} characters')
    refs = out['source_refs']
    if not (isinstance(refs, list) and refs and all(isinstance(r, str) for r in refs)):
        errors.append('source_refs must be a non-empty list of source IDs')
    else:
        unknown = sorted(set(refs) - c.source_ids(context))
        if unknown:
            errors.append(f'source_refs not in the context: {unknown}')
    mi = out['missing_information']
    if not (isinstance(mi, list) and len(mi) <= 10 and all(_is_str(x, 1, 300) for x in mi)):
        errors.append('missing_information must be a list of at most 10 short texts')
    if not isinstance(out['requires_clarification'], bool):
        errors.append('requires_clarification must be true or false')
    if out['requires_human_review'] is not True:
        errors.append('requires_human_review must be true')

    if et != 'guest_reply':
        if out['intent'] is not None or out['certainty'] is not None:
            errors.append(f'{et} has no guest intent: intent and certainty must be null')
        if out['evidence_quotes'] != [] or out['requested_changes'] != []:
            errors.append(f'{et} has no guest message: evidence_quotes and requested_changes must be empty')
        return errors

    message = (context.get('guest_message') or {}).get('text', '')
    if out['intent'] not in c.INTENTS:
        errors.append(f'intent must be one of {list(c.INTENTS)}')
    if out['certainty'] not in c.CERTAINTIES:
        errors.append(f'certainty must be one of {list(c.CERTAINTIES)}')
    quotes = out['evidence_quotes']
    if not (isinstance(quotes, list) and 1 <= len(quotes) <= 5 and all(isinstance(q, str) for q in quotes)):
        errors.append('evidence_quotes must list 1-5 quotes from the guest message')
    else:
        bad = [q for q in quotes if not in_message(q, message)]
        if bad:
            errors.append(f'evidence quotes not found in the guest message: {bad}')
    if isinstance(refs, list) and 'guest_message' not in refs:
        errors.append('a reply must cite guest_message in source_refs')
    changes = out['requested_changes']
    date_left_open = False
    if not (isinstance(changes, list) and len(changes) <= 5):
        errors.append('requested_changes must be a list of at most 5 items')
        changes = []
    for i, ch in enumerate(changes):
        if not isinstance(ch, dict) or set(ch) != set(c.CHANGE_KEYS):
            errors.append(f'requested_changes[{i}] must have exactly {list(c.CHANGE_KEYS)}')
            continue
        if ch['field'] not in c.CHANGE_FIELDS:
            errors.append(f'requested_changes[{i}].field must be one of {list(c.CHANGE_FIELDS)}')
        for k in ('raw_text', 'evidence'):
            if not _is_str(ch[k]) or not in_message(ch[k], message):
                errors.append(f'requested_changes[{i}].{k} must be quoted from the guest message')
        pv = ch['proposed_value']
        if pv is not None and not isinstance(pv, str):
            errors.append(f'requested_changes[{i}].proposed_value must be text or null')
        if ch['field'] in ('arrival_date', 'departure_date'):
            if not dates.is_explicit_date(pv, message):
                errors.append(f'requested_changes[{i}].proposed_value {pv!r} is not a full date the '
                              'guest wrote: leave it null and ask the guest')
            if pv is None:
                date_left_open = True
        if ch['ambiguity_reason'] is not None and not isinstance(ch['ambiguity_reason'], str):
            errors.append(f'requested_changes[{i}].ambiguity_reason must be text or null')
    intent, certainty, clar = out['intent'], out['certainty'], out['requires_clarification']
    if intent == 'cancellation_requested' and certainty != 'explicit':
        errors.append('a cancellation request must be explicit; a tentative one is "unclear"')
    if intent == 'change_requested' and not changes:
        errors.append('change_requested needs at least one requested change')
    if clar is not True and (intent == 'unclear' or certainty != 'explicit' or date_left_open):
        errors.append('requires_clarification must be true when the intent or a requested date is unclear')
    return errors


def _blocker(code: str, message: str) -> dict:
    return {'code': code, 'message': message}


def source_dates(context: dict) -> set[date]:
    out = set()
    for d in [context['business_as_of'], context['booking'].get('arrival_date'),
              *(s['scheduled_at'] for s in context.get('schedule', [])),
              (context.get('guest_message') or {}).get('message_business_at')]:
        try:
            out.add(date.fromisoformat(d))
        except (TypeError, ValueError):
            pass
    message = (context.get('guest_message') or {}).get('text', '')
    for m in dates.find_dates(message):  # dates the guest wrote may be repeated back
        out.add(date(m['year'] or date.fromisoformat(context['business_as_of']).year, m['month'], m['day']))
    return out


def content_blockers(context: dict, subject: str, prose: str) -> list[dict]:
    """Fact checks of the wording (subject + body text written by the model / staff)."""
    text = f'{subject}\n{prose}'
    out = []
    if not prose.strip():
        out.append(_blocker('empty_body', 'The email has no text.'))
    unknown = [m['text'] for m in dates.find_dates(text) if not dates.matches(m, source_dates(context))]
    if unknown:
        out.append(_blocker('date_not_in_source', f'Dates not in the booking, G1 schedule or guest '
                                                  f'message: {unknown}. Use [[booking.arrival_date]] '
                                                  'or ask the guest.'))
    if _MONEY.search(text):
        out.append(_blocker('price_not_in_source', 'Prices, fees or amounts are not in any source: '
                                                   'remove them or let staff verify first.'))
    claims = sorted({m.group(0) for rx in _CLAIMS for m in rx.finditer(text)})
    if claims:
        out.append(_blocker('unsupported_claim', f'Claims with no confirming event: {claims}. Say '
                                                 'that staff will verify instead.'))
    if _LINK.search(text):
        out.append(_blocker('link_or_address', 'Email addresses or links are not allowed in the text '
                                               '(the recipient is set by the server, not the draft).'))
    if _INTERNAL.search(text):
        out.append(_blocker('internal_wording', 'Internal risk wording must not reach a guest email.'))
    generic = sorted({m.group(0) for m in _GENERIC_PLACEHOLDER.finditer(text)})
    if generic:
        out.append(_blocker('placeholder', f'Unfilled placeholders: {generic}'))
    return out


def fact_blockers(context: dict) -> list[dict]:
    """Essential facts per email type and booking eligibility (not the wording)."""
    b, et, as_of = context['booking'], context['email_type'], context['business_as_of']
    out = []
    if b.get('booking_id') is None:
        out.append(_blocker('missing_booking_id', 'Booking reference missing.'))
    arrival = b.get('arrival_date')
    try:
        arrival_ok = arrival is not None and date.fromisoformat(arrival) is not None
    except ValueError:
        arrival_ok = False
    if not arrival_ok:
        out.append(_blocker('missing_arrival_date', 'Arrival date missing or invalid in the source.'))
    elif arrival <= as_of:
        out.append(_blocker('arrival_reached', f'Arrival {arrival} is on or before the business date '
                                               f'{as_of}: the booking is no longer eligible.'))
    if et == 'reminder' and not c.g1_reminders(context):
        out.append(_blocker('no_g1_reminder', 'G1 has no reminder for this booking: an AI draft '
                                              'cannot add a contact to the schedule.'))
    if et in ('reminder', 'booking_confirmation') and context.get('g2_stop_outcome') == 'cancelled':
        out.append(_blocker('cancelled_in_store', 'The action log records a cancellation for this '
                                                  'booking: no reminder or confirmation.'))
    if et == 'guest_reply' and not (context.get('guest_message') or {}).get('text'):
        out.append(_blocker('missing_guest_message', 'Reply to guest needs the guest message.'))
    return out


def check_revision(context: dict, subject: str, body: str) -> dict:
    """Render a revision's wording and compute everything stored with it."""
    r = render.render(context, subject, body)
    blockers = fact_blockers(context)
    if r['unresolved']:
        blockers.append(_blocker('unresolved_placeholder',
                                 f"Placeholders that have no source value: {r['unresolved']}"))
    blockers += content_blockers(context, r['subject'][len(render.DEMO_PREFIX):], r['prose'])
    return {'rendered_subject': r['subject'], 'rendered_body': r['body'],
            'content_hash': render.content_hash(r['subject'], r['body']),
            'send_blockers': blockers, 'unresolved': r['unresolved']}


def server_missing_information(context: dict, analysis: dict | None) -> list[str]:
    """Non-blocking points staff should see, added by the server to the model's list."""
    out = []
    lang_vi = context['language'] == 'vi'
    if not context['booking'].get('room_type_code'):
        out.append('Mã loại phòng không có trong nguồn (bỏ qua trong email)' if lang_vi
                   else 'Room type code not in the source (left out of the email)')
    if analysis and analysis.get('email_type') == 'guest_reply':
        for ch in analysis.get('requested_changes') or []:
            if ch.get('field') in ('arrival_date', 'departure_date') and ch.get('proposed_value') is None:
                out.append('Khách chưa cho ngày cụ thể; email hỏi lại' if lang_vi
                           else 'The guest gave no exact date; the email asks for it')
                break
    return out
