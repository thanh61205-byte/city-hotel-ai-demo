// Workflow A, node "Validate output" (lib_validator.js prepended). Valid -> succeeded;
// otherwise failed with the reasons. The next node stores it BEFORE the HTTP response.
const claim = $('Check claim & context').first().json;
const it = $input.first().json;
let errors;
if (it.route === 'store_failed') errors = [it.error];
else if (it.output === null || it.output === undefined) errors = [it.error || 'no output'];
else errors = validateOutput(claim.context, it.output);
const ok = errors.length === 0;
const meta = {
  analysis_mode: it.analysis_mode ?? null, model: it.model ?? null, prompt_version: it.prompt_version ?? null,
  validation_errors: ok ? null : errors, token_usage: null,
  error: ok ? null : ('Draft rejected: ' + errors.join('; ')).slice(0, 900),
};
return [{ json: { request_id: claim.request_id, lease_token: claim.lease_token,
  status: ok ? 'succeeded' : 'failed', result: ok ? it.output : null, meta } }];
