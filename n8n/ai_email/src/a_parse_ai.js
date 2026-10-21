// Workflow A, node "Parse AI output". One model call per draft: an unreadable answer is a
// failed draft, never a second "repair" call (credit and loop control).
const j = $input.first().json;
const raw = String(j.text ?? j.output ?? j.response?.text ?? '').trim()
  .replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
let output = null, error = null;
try {
  output = JSON.parse(raw);
} catch (e) {
  error = 'AI output is not valid JSON';
}
return [{ json: { output, error, analysis_mode: 'live_ai', model: MODEL_ID, prompt_version: PROMPT_VERSION } }];
