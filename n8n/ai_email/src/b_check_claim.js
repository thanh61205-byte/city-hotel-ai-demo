// Workflow B, node "Check claim". Input: g2_ai_v1.claim_send_job(). Only a claimed job goes on;
// already finished, in progress, refused or unknown jobs are answered from the database.
const v = $('Validate send request').first().json;
const r = $input.first().json.r ?? {};
if (r.status !== 'claimed') {
  return [{ json: { route: 'respond', response: { job_id: v.job_id, status: r.status ?? 'error',
    job_status: r.job_status ?? null, reason: r.reason ?? null, provider_message_id: r.provider_message_id ?? null,
    detail: r.detail ?? null, ignored_fields: v.ignored_fields, source: 'database' } } }];
}
return [{ json: { route: 'dispatch', job_id: v.job_id, workspace_id: v.workspace_id,
  lease_token: r.lease_token, delivery_mode: r.delivery_mode, ignored_fields: v.ignored_fields } }];
