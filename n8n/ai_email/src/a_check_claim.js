// Workflow A, node "Check claim & context". Input: the row of g2_ai_v1.claim_generation().
// Not claimed (done, in progress, not found) -> answer from the database, no model call.
// Claimed but invalid for its email type -> stored as failed, no model call.
const v = $('Validate request').first().json;
const r = $input.first().json.r ?? {};
const base = { request_id: v.request_id, workspace_id: v.workspace_id };
if (r.status !== 'claimed') {
  return [{ json: { ...base, route: 'respond', response: {
    request_id: v.request_id, status: r.status ?? 'error', reason: r.reason ?? null,
    result: r.result ?? null, errors: r.errors ?? null, error: r.error ?? null,
    analysis_mode: r.analysis_mode ?? null, source: 'database' } } }];
}
const ctx = r.context ?? {};
const reasons = [];
if (!['reminder', 'booking_confirmation', 'guest_reply'].includes(ctx.email_type)) reasons.push('unknown email_type');
if (!['en', 'vi'].includes(ctx.language)) reasons.push('unsupported language');
if (!Number.isInteger(ctx.booking?.booking_id)) reasons.push('booking_id missing');
const gm = ctx.guest_message ?? null;
if (ctx.email_type === 'guest_reply') {
  if (!gm || typeof gm.text !== 'string' || !gm.text.trim()) {
    reasons.push('guest_reply needs the guest message');
  } else {
    if (gm.text.length > 2000) reasons.push('guest message longer than 2000 characters');
    if (!['sample_input', 'staff_transcription'].includes(gm.source)) reasons.push('guest message source invalid');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(gm.message_business_at ?? ''))) reasons.push('message business date missing');
  }
} else if (gm !== null) {
  reasons.push(`${ctx.email_type} takes no guest message`);
}
if (!['live_ai', 'fixture'].includes(r.requested_mode)) reasons.push('n8n drafts live_ai or fixture requests only');
const out = { ...base, lease_token: r.lease_token, context: ctx, requested_mode: r.requested_mode,
  email_type: ctx.email_type };
if (reasons.length) return [{ json: { ...out, route: 'store_failed', error: 'Invalid request: ' + reasons.join('; ') } }];
return [{ json: { ...out, route: 'generate' } }];
