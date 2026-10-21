// Workflow A, node "Stored result response": what complete_generation() stored. Streamlit reads
// the database by request ID anyway, so a lost response loses nothing.
const r = $input.first().json.r ?? {};
const v = $('Validate request').first().json;
return [{ json: { request_id: v.request_id, status: r.status ?? 'unknown', stored: r.stored === true,
  reason: r.reason ?? null, analysis_mode: r.analysis_mode ?? null, error: r.error ?? null,
  errors: r.errors ?? null, result: r.result ?? null, source: 'database' } }];
