// Workflow B, node "Check dispatch". Input: g2_ai_v1.begin_dispatch(): the job is now
// 'dispatching' (committed) and r holds the approved email read from the database.
// Routes: respond (refused before dispatch), finish (known failure, nothing sent), mock, gmail.
const c = $('Check claim').first().json;
const r = $input.first().json.r ?? {};
const base = { job_id: c.job_id, lease_token: c.lease_token };
if (r.ok !== true) {
  return [{ json: { ...base, route: 'respond', response: { job_id: c.job_id, status: 'refused',
    job_status: r.job_status ?? null, reason: r.reason ?? null, source: 'database' } } }];
}
const problems = [];
if (!String(r.subject ?? '').startsWith('[DEMO] ')) problems.push('subject lacks the [DEMO] prefix');
if (!String(r.body ?? '').includes('DEMO')) problems.push('body lacks the DEMO notice');
if (r.delivery_mode === 'team_test_email' && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(r.to ?? ''))) {
  problems.push('recipient address invalid');
}
if (problems.length) {
  return [{ json: { ...base, route: 'finish', outcome: 'failed', provider_message_id: null,
    detail: 'Not sent: ' + problems.join('; ') } }];
}
return [{ json: { ...base, route: r.delivery_mode === 'mock' ? 'mock' : 'gmail', to: r.to,
  subject: r.subject, body: r.body, content_hash: r.content_hash } }];
