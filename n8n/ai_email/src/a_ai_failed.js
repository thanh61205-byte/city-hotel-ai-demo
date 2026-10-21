// Workflow A, node "AI call failed" (error output of the model call: quota, credit, timeout,
// missing credential). Stored as a failed draft; Streamlit offers the labelled fixture instead.
// No automatic retry.
const j = $input.first().json;
// message is often generic ("Bad request"); description carries the provider's reason.
const parts = [j.error?.message ?? j.message, j.error?.description].filter(x => x && typeof x === 'string');
const msg = parts.length ? [...new Set(parts)].join(': ') : String(j.error ?? 'AI call failed');
return [{ json: { output: null, error: 'AI call failed (not retried): ' + msg.slice(0, 500),
  analysis_mode: 'live_ai', model: MODEL_ID, prompt_version: PROMPT_VERSION } }];
