// Workflow B, node "Simulate send (no email)": delivery_mode mock. Nothing leaves n8n.
const d = $input.first().json;
const run = typeof $execution !== 'undefined' && $execution?.id ? String($execution.id) : 'run';
return [{ json: { job_id: d.job_id, lease_token: d.lease_token, outcome: 'simulated',
  provider_message_id: `n8n-mock-${String(d.job_id).slice(0, 8)}-${run}`,
  detail: 'Simulated by n8n workflow B: no email sent' } }];
