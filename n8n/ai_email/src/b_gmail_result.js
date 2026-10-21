// Workflow B, nodes "Gmail accepted" / "Gmail error" (GMAIL_BRANCH prepended: 'ok' | 'error').
// Gmail returned a message ID -> provider_accepted (the provider took it; delivery to the inbox
// is confirmed only by a tester). An error that proves Gmail refused before sending (auth /
// credential / invalid recipient, HTTP 400/401/403) -> failed. Anything else (timeout, 5xx,
// network) -> unknown: the email may have left, so it is never resent automatically.
const d = $('Check dispatch').first().json;
const j = $input.first().json;
let outcome, id = null, detail;
if (GMAIL_BRANCH === 'ok') {
  id = j.id ?? j.messageId ?? null;
  outcome = id ? 'provider_accepted' : 'unknown';
  detail = id ? 'Accepted by Gmail (receipt not verified yet)' : 'Gmail answered without a message ID';
} else {
  const msg = String(j.error?.message ?? j.error?.description ?? j.error ?? j.message ?? 'Gmail error');
  const code = Number(j.error?.httpCode ?? j.error?.status ?? j.error?.statusCode ?? NaN);
  const refused = [400, 401, 403].includes(code) ||
    /invalid_grant|unauthori[sz]ed|credential|invalid to header|invalid recipient|forbidden|status code (400|401|403)(?!\d)/i.test(msg);
  outcome = refused ? 'failed' : 'unknown';
  detail = (refused ? 'Gmail refused before sending: ' : 'Gmail result unknown (may have been sent): ') + msg.slice(0, 400);
}
return [{ json: { job_id: d.job_id, lease_token: d.lease_token, outcome, provider_message_id: id, detail } }];
