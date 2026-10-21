// Workflow B, node "Send result response": what finish_dispatch() stored (Streamlit reads the
// database by job ID anyway). If storing failed, the job stays 'dispatching' and becomes
// 'unknown' after 5 minutes: it is reconciled, never resent.
const r = $input.first().json.r ?? {};
const c = $('Check claim').first().json;
return [{ json: { job_id: c.job_id, status: r.stored === true ? 'stored' : 'not_stored',
  job_status: r.job_status ?? null, provider_message_id: r.provider_message_id ?? null,
  detail: r.detail ?? r.reason ?? null, ignored_fields: c.ignored_fields ?? [], source: 'database' } }];
