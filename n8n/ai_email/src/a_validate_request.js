// Workflow A, node "Validate request". The webhook gives only a request ID and the workspace:
// the context is read from the database when the request is claimed, never from the payload.
const b = $input.first().json.body ?? {};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const WS = /^[a-z0-9][a-z0-9_-]{0,62}$/;
const reasons = [];
if (!UUID.test(String(b.request_id ?? ''))) reasons.push('request_id must be a UUID');
if (!WS.test(String(b.workspace_id ?? ''))) reasons.push('workspace_id missing or invalid');
const response = { request_id: b.request_id ?? null, status: 'invalid_request', reasons, source: 'n8n' };
return [{ json: { valid: reasons.length === 0, reasons, request_id: String(b.request_id ?? ''),
  workspace_id: String(b.workspace_id ?? ''), response } }];
