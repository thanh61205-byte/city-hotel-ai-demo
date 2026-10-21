// Shared by n8n Code nodes (prepended by build_workflows.py). JavaScript port of
// dashboard/ai_email/validate.py validate_output(): same rules, checked against the same cases
// (tests/fixtures/ai_email/validator_cases.json). Valid JSON is not a correct email: the server
// still computes send blockers on the rendered text and staff still read it.
const OUTPUT_KEYS = ['email_type', 'intent', 'certainty', 'evidence_quotes', 'requested_changes',
  'missing_information', 'requires_clarification', 'draft_subject', 'draft_body', 'source_refs',
  'requires_human_review'];
const CHANGE_KEYS = ['field', 'raw_text', 'proposed_value', 'evidence', 'ambiguity_reason'];
const INTENTS = ['confirmed', 'change_requested', 'cancellation_requested', 'unclear'];
const CERTAINTIES = ['explicit', 'tentative', 'ambiguous'];
const CHANGE_FIELDS = ['arrival_date', 'departure_date', 'room_type', 'guests', 'other'];
const MONTHS = { january: 1, jan: 1, february: 2, feb: 2, march: 3, mar: 3, april: 4, apr: 4, may: 5,
  june: 6, jun: 6, july: 7, jul: 7, august: 8, aug: 8, september: 9, sep: 9, sept: 9, october: 10,
  oct: 10, november: 11, nov: 11, december: 12, dec: 12 };
const MONTH_RE = Object.keys(MONTHS).sort((a, b) => b.length - a.length).join('|');

const norm = s => String(s ?? '').normalize('NFC').toLowerCase()
  .replace(/[“”]/g, '"').replace(/[‘’]/g, "'").replace(/\{/g, '(').replace(/\}/g, ')')
  .split(/\s+/).filter(Boolean).join(' ');
const stripEnds = s => s.replace(/^[ .,!?;:"']+|[ .,!?;:"']+$/g, '');
const inMessage = (quote, message) => {
  const q = stripEnds(norm(quote));
  return q.length > 0 && norm(message).includes(q);
};
const isStr = (v, lo = 1, hi = Infinity) => typeof v === 'string' && v.trim().length >= lo && v.length <= hi;

function explicitDates(text) {
  const out = new Set();
  const add = (y, m, d) => {
    const dt = new Date(Date.UTC(y, m - 1, d));
    if (dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d) {
      out.add(dt.toISOString().slice(0, 10));
    }
  };
  const s = String(text ?? '');
  for (const m of s.matchAll(/\b(\d{4})-(\d{1,2})-(\d{1,2})\b/g)) add(+m[1], +m[2], +m[3]);
  for (const m of s.matchAll(/\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b/g)) add(+m[3], +m[2], +m[1]);
  for (const m of s.matchAll(/ngày\s+(\d{1,2})\s*(?:\/|tháng\s+)(\d{1,2})\s*(?:\/|năm\s+)(\d{4})/gi)) add(+m[3], +m[2], +m[1]);
  for (const m of s.matchAll(new RegExp(`\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+(?:of\\s+)?(${MONTH_RE})\\.?,?\\s+(\\d{4})\\b`, 'gi'))) {
    add(+m[3], MONTHS[m[2].toLowerCase()], +m[1]);
  }
  for (const m of s.matchAll(new RegExp(`\\b(${MONTH_RE})\\.?\\s+(\\d{1,2})(?:st|nd|rd|th)?,?\\s+(\\d{4})\\b`, 'gi'))) {
    add(+m[3], MONTHS[m[1].toLowerCase()], +m[2]);
  }
  return out;
}
const isExplicitDate = (value, message) => value === null || explicitDates(message).has(String(value));

function validateOutput(context, out) {
  if (out === null || typeof out !== 'object' || Array.isArray(out)) return ['output must be a JSON object'];
  const errors = [];
  const keys = Object.keys(out);
  const missing = OUTPUT_KEYS.filter(k => !keys.includes(k));
  const extra = keys.filter(k => !OUTPUT_KEYS.includes(k));
  if (missing.length) errors.push(`missing keys: ${missing.join(', ')}`);
  if (extra.length) errors.push(`unexpected keys: ${extra.join(', ')}`);
  if (missing.length) return errors;
  const et = context.email_type;
  if (out.email_type !== et) errors.push(`email_type ${out.email_type} does not match the request (${et})`);
  if (!isStr(out.draft_subject, 1, 150)) errors.push('draft_subject must be text of 1-150 characters');
  if (!isStr(out.draft_body, 1, 4000)) errors.push('draft_body must be text of 1-4000 characters');
  const sourceIds = new Set((context.sources ?? []).map(s => s.source_id));
  const refs = out.source_refs;
  if (!(Array.isArray(refs) && refs.length && refs.every(r => typeof r === 'string'))) {
    errors.push('source_refs must be a non-empty list of source IDs');
  } else {
    const unknown = refs.filter(r => !sourceIds.has(r));
    if (unknown.length) errors.push(`source_refs not in the context: ${unknown.join(', ')}`);
  }
  const mi = out.missing_information;
  if (!(Array.isArray(mi) && mi.length <= 10 && mi.every(x => isStr(x, 1, 300)))) {
    errors.push('missing_information must be a list of at most 10 short texts');
  }
  if (typeof out.requires_clarification !== 'boolean') errors.push('requires_clarification must be true or false');
  if (out.requires_human_review !== true) errors.push('requires_human_review must be true');

  if (et !== 'guest_reply') {
    if (out.intent !== null || out.certainty !== null) errors.push(`${et} has no guest intent: intent and certainty must be null`);
    if (!(Array.isArray(out.evidence_quotes) && out.evidence_quotes.length === 0 &&
          Array.isArray(out.requested_changes) && out.requested_changes.length === 0)) {
      errors.push(`${et} has no guest message: evidence_quotes and requested_changes must be empty`);
    }
    return errors;
  }
  const message = context.guest_message?.text ?? '';
  if (!INTENTS.includes(out.intent)) errors.push(`intent must be one of ${INTENTS.join(', ')}`);
  if (!CERTAINTIES.includes(out.certainty)) errors.push(`certainty must be one of ${CERTAINTIES.join(', ')}`);
  const quotes = out.evidence_quotes;
  if (!(Array.isArray(quotes) && quotes.length >= 1 && quotes.length <= 5 && quotes.every(q => typeof q === 'string'))) {
    errors.push('evidence_quotes must list 1-5 quotes from the guest message');
  } else {
    const bad = quotes.filter(q => !inMessage(q, message));
    if (bad.length) errors.push(`evidence quotes not found in the guest message: ${JSON.stringify(bad)}`);
  }
  if (Array.isArray(refs) && !refs.includes('guest_message')) errors.push('a reply must cite guest_message in source_refs');
  let changes = out.requested_changes;
  let dateLeftOpen = false;
  if (!(Array.isArray(changes) && changes.length <= 5)) {
    errors.push('requested_changes must be a list of at most 5 items');
    changes = [];
  }
  changes.forEach((ch, i) => {
    if (ch === null || typeof ch !== 'object' || Array.isArray(ch) ||
        Object.keys(ch).length !== CHANGE_KEYS.length || !CHANGE_KEYS.every(k => k in ch)) {
      errors.push(`requested_changes[${i}] must have exactly ${CHANGE_KEYS.join(', ')}`);
      return;
    }
    if (!CHANGE_FIELDS.includes(ch.field)) errors.push(`requested_changes[${i}].field must be one of ${CHANGE_FIELDS.join(', ')}`);
    for (const k of ['raw_text', 'evidence']) {
      if (!isStr(ch[k]) || !inMessage(ch[k], message)) errors.push(`requested_changes[${i}].${k} must be quoted from the guest message`);
    }
    const pv = ch.proposed_value;
    if (pv !== null && typeof pv !== 'string') errors.push(`requested_changes[${i}].proposed_value must be text or null`);
    if (ch.field === 'arrival_date' || ch.field === 'departure_date') {
      if (!isExplicitDate(pv, message)) errors.push(`requested_changes[${i}].proposed_value is not a full date the guest wrote`);
      if (pv === null) dateLeftOpen = true;
    }
    if (ch.ambiguity_reason !== null && typeof ch.ambiguity_reason !== 'string') {
      errors.push(`requested_changes[${i}].ambiguity_reason must be text or null`);
    }
  });
  const { intent, certainty } = out;
  if (intent === 'cancellation_requested' && certainty !== 'explicit') {
    errors.push('a cancellation request must be explicit; a tentative one is "unclear"');
  }
  if (intent === 'change_requested' && changes.length === 0) errors.push('change_requested needs at least one requested change');
  if (out.requires_clarification !== true && (intent === 'unclear' || certainty !== 'explicit' || dateLeftOpen)) {
    errors.push('requires_clarification must be true when the intent or a requested date is unclear');
  }
  return errors;
}
