// Workflow A, node "Fixture output (not AI)". Same templates and labelled samples as
// dashboard/ai_email/fixtures.py (FIXTURES = n8n/ai_email/fixtures_v1.json, prepended).
// A free-text guest message is not a fixture: no label is invented for it.
const it = $input.first().json;
const ctx = it.context;
const refs = list => list.flatMap(r => {
  if (r === '$booking') return [`booking:${ctx.booking.booking_id}`];
  if (r === '$g1_reminder') {
    const rows = (ctx.schedule ?? []).filter(s => s.action_type === 'reminder');
    const ahead = rows.filter(s => s.scheduled_at >= ctx.business_as_of);
    const next = ahead[0] ?? rows[rows.length - 1];
    return next ? [next.source_id] : [];
  }
  return [r];
});
const draft = (tpl, refList, a) => ({
  email_type: ctx.email_type, intent: a?.intent ?? null, certainty: a?.certainty ?? null,
  evidence_quotes: a?.evidence_quotes ?? [], requested_changes: a?.requested_changes ?? [],
  missing_information: a?.missing_information ?? [], requires_clarification: a?.requires_clarification ?? false,
  draft_subject: tpl.subject, draft_body: tpl.body, source_refs: refs(refList), requires_human_review: true,
});
const T = FIXTURES.templates;
let output = null, error = null;
if (ctx.email_type !== 'guest_reply') {
  const tpl = T[ctx.email_type][ctx.language];
  output = draft(tpl, tpl.source_refs, null);
} else {
  const key = norm(ctx.guest_message.text);
  const sample = FIXTURES.guest_reply_samples.find(s => norm(s.text) === key);
  if (sample) {
    output = draft(T.guest_reply[sample.analysis.intent][ctx.language], FIXTURES.guest_reply_source_refs, sample.analysis);
  } else {
    error = 'Free text is not a fixture sample: use live AI, or label it manually in Streamlit';
  }
}
return [{ json: { output, error, analysis_mode: 'fixture', model: null, prompt_version: FIXTURES.version } }];
