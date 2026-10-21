// Workflow B, node "Validate send request". Only the job ID and workspace are used. Anything
// else in the payload (subject, body, recipient, approved: true, ...) is ignored and reported:
// the email, recipient and approval are read from the database by the claim functions.
// SENDER_REF (prepended by build_workflows.py) names the Gmail credential this workflow sends
// with; the approval binds it, so changing the sender requires a new approval.
const b = $input.first().json.body ?? {};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const WS = /^[a-z0-9][a-z0-9_-]{0,62}$/;
const reasons = [];
if (!UUID.test(String(b.job_id ?? ''))) reasons.push('job_id must be a UUID');
if (!WS.test(String(b.workspace_id ?? ''))) reasons.push('workspace_id missing or invalid');
const ignored = Object.keys(b).filter(k => !['job_id', 'workspace_id'].includes(k));
return [{ json: { valid: reasons.length === 0, reasons, job_id: String(b.job_id ?? ''),
  workspace_id: String(b.workspace_id ?? ''), sender_ref: SENDER_REF, ignored_fields: ignored,
  response: { job_id: b.job_id ?? null, status: 'invalid_request', reasons, source: 'n8n' } } }];
