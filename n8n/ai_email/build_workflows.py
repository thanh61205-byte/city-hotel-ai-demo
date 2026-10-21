"""Build the two AI email workflows for n8n Cloud from the node code in n8n/ai_email/src/.

  A  n8n/g2_ai_email_draft_workflow.json  Webhook -> validate -> claim (DB) -> Switch email_type
                                           -> prompt -> AI or fixture -> validate -> store (DB) -> respond
  B  n8n/g2_ai_email_send_workflow.json   Webhook -> validate -> claim job (DB) -> begin dispatch (DB)
                                           -> mock | Gmail Send -> store result (DB) -> respond

The old demo workflow (n8n/g2_approval_demo_workflow.json) is not touched. Edit the .js / .md /
.json sources, then run:  python n8n/ai_email/build_workflows.py
No secret is written here: credentials (webhook header auth, Postgres, OpenAI, Gmail) are chosen
in n8n after import. MODEL_ID and SENDER_REF are labels to check against the account (README).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / 'src'
PROMPTS = HERE / 'prompts'
OUT_A = HERE.parent / 'g2_ai_email_draft_workflow.json'
OUT_B = HERE.parent / 'g2_ai_email_send_workflow.json'
PROMPT_VERSION = 'ai-email-v1'
MODEL_ID = 'gpt-6-luna'                      # OpenAI GPT-6 Luna; check it is listed for Gateway credits in n8n
SENDER_REF = 'gmail:g2-team-test-sender'     # = G2_AI_SENDER_REF in Streamlit secrets
SCHEMA = 'g2_ai_v1'
NS = uuid.UUID('7f3c2a10-5b7e-4c1d-9e2f-0a6b8c4d2e10')


def nid(name: str) -> str:
    return str(uuid.uuid5(NS, name))


def const(name: str, value) -> str:
    return f'const {name} = {json.dumps(value, ensure_ascii=False)};\n'


def prompts() -> dict:
    return {k: (PROMPTS / f'{k}_v1.md').read_text(encoding='utf-8').strip()
            for k in ('system', 'reminder', 'booking_confirmation', 'guest_reply')}


def code(file: str, *, lib: bool = False, **consts) -> str:
    """Node source: constants, optional validator library, then the node file."""
    head = ''.join(const(k, v) for k, v in consts.items())
    if lib:
        head += (SRC / 'lib_validator.js').read_text(encoding='utf-8') + '\n'
    return head + (SRC / file).read_text(encoding='utf-8')


def code_node(name, file, pos, **kw):
    return {'parameters': {'jsCode': code(file, **kw)}, 'id': nid(name), 'name': name,
            'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos}


def switch(name, outputs: int, expr: str, pos):
    return {'parameters': {'mode': 'expression', 'numberOutputs': outputs, 'output': f'={{{{ {expr} }}}}'},
            'id': nid(name), 'name': name, 'type': 'n8n-nodes-base.switch', 'typeVersion': 3, 'position': pos}


def postgres(name, query, params_expr, pos):
    return {'parameters': {'operation': 'executeQuery', 'query': query,
                           'options': {'queryReplacement': f'={{{{ {params_expr} }}}}'}},
            'id': nid(name), 'name': name, 'type': 'n8n-nodes-base.postgres', 'typeVersion': 2.5,
            'position': pos, 'retryOnFail': False, 'alwaysOutputData': False,
            'notes': 'Credential: Postgres role g2_ai_n8n (EXECUTE on g2_ai_v1 workflow functions only), SSL required.'}


def webhook(name, path, pos):
    return {'parameters': {'httpMethod': 'POST', 'path': path, 'authentication': 'headerAuth',
                           'responseMode': 'responseNode', 'options': {}},
            'id': nid(name), 'name': name, 'type': 'n8n-nodes-base.webhook', 'typeVersion': 2,
            'position': pos, 'webhookId': nid(name + path),
            'notes': 'Header Auth credential: header X-G2-Webhook-Token, value = G2_AI_WEBHOOK_TOKEN '
                     '(Streamlit secret). Use the Production URL after publishing.'}


def respond(name, pos, code_=200):
    return {'parameters': {'respondWith': 'firstIncomingItem', 'options': {'responseCode': code_}},
            'id': nid(name), 'name': name, 'type': 'n8n-nodes-base.respondToWebhook', 'typeVersion': 1.1,
            'position': pos}


def link(*targets):
    """connections entry: one list per output; each output -> list of target node names."""
    return {'main': [[{'node': t, 'type': 'main', 'index': 0} for t in (out if isinstance(out, list) else [out])]
                     for out in targets]}


SETTINGS = {'executionOrder': 'v1', 'saveManualExecutions': True, 'saveDataErrorExecution': 'all',
            'saveDataSuccessExecution': 'all', 'callerPolicy': 'workflowsFromSameOwner'}


def workflow_a() -> dict:
    P = prompts()
    fx = json.loads((HERE / 'fixtures_v1.json').read_text(encoding='utf-8'))
    claim_q = f'SELECT {SCHEMA}.claim_generation($1::uuid, $2::text, \'n8n\') AS r;'
    store_q = f'SELECT {SCHEMA}.complete_generation($1::uuid, $2::uuid, $3::text, $4::jsonb, $5::jsonb) AS r;'
    nodes = [
        webhook('Webhook', 'g2-ai-email-draft', [0, 300]),
        code_node('Validate request', 'a_validate_request.js', [220, 300]),
        switch('Valid request?', 2, '$json.valid ? 1 : 0', [440, 300]),
        code_node('Invalid request response', 'respond_with.js', [660, 120]),
        respond('Respond: invalid request', [880, 120], 400),
        postgres('Claim generation request (atomic)', claim_q,
                 '[ $json.request_id, $json.workspace_id ]', [660, 300]),
        code_node('Check claim & context', 'a_check_claim.js', [880, 300]),
        switch('Claim outcome', 3,
               "({ respond: 0, store_failed: 1, generate: 2 })[$json.route]", [1100, 300]),
        code_node('Existing state response', 'respond_with.js', [1320, 60]),
        respond('Respond: from database', [1540, 60]),
        switch('Email type', 3,
               "({ reminder: 0, booking_confirmation: 1, guest_reply: 2 })[$json.email_type]", [1320, 360]),
        code_node('Prompt: reminder', 'a_build_prompt.js', [1540, 240],
                  BRANCH='reminder', PROMPTS=P, PROMPT_VERSION=PROMPT_VERSION),
        code_node('Prompt: booking confirmation', 'a_build_prompt.js', [1540, 380],
                  BRANCH='booking_confirmation', PROMPTS=P, PROMPT_VERSION=PROMPT_VERSION),
        code_node('Prompt: reply to guest', 'a_build_prompt.js', [1540, 520],
                  BRANCH='guest_reply', PROMPTS=P, PROMPT_VERSION=PROMPT_VERSION),
        switch('AI or fixture', 2, "$json.requested_mode === 'live_ai' ? 1 : 0", [1760, 380]),
        code_node('Fixture output (not AI)', 'a_fixture_output.js', [1980, 240], lib=True, FIXTURES=fx),
        {'parameters': {'promptType': 'define', 'text': '={{ $json.user_prompt }}', 'hasOutputParser': False,
                        'messages': {'messageValues': [{'message': '={{ $json.system_prompt }}'}]}},
         'id': nid('Draft with AI (one call)'), 'name': 'Draft with AI (one call)',
         'type': '@n8n/n8n-nodes-langchain.chainLlm', 'typeVersion': 1.5, 'position': [1980, 480],
         'retryOnFail': False, 'onError': 'continueErrorOutput',
         'notes': 'One model call per draft; no output-parser repair loop. Error output -> AI call failed.'},
        {'parameters': {'model': {'__rl': True, 'mode': 'list', 'value': MODEL_ID, 'cachedResultName': MODEL_ID},
                        # No temperature and no maxTokens: gpt-6-luna rejects both (verified 21/10/2026 on n8n Cloud:
                        # "'max_tokens' is not supported with this model"; the node cannot send
                        # max_completion_tokens). Length is bounded by the prompt and the 4000-char validator.
                        'options': {'responseFormat': 'json_object',
                                    'timeout': 30000, 'maxRetries': 0}},
         'id': nid('OpenAI Chat Model'), 'name': 'OpenAI Chat Model',
         'type': '@n8n/n8n-nodes-langchain.lmChatOpenAi', 'typeVersion': 1.2, 'position': [1980, 680],
         'notes': 'Credential field: "Use Gateway credits" (n8n Cloud trial balance, no API key) if this model '
                  'is listed under "Included in n8n"; otherwise "Use my own credential". At zero balance the call '
                  'fails and the draft is stored as failed (fixture offered). Check the model and JSON output; '
                  'update MODEL_ID in build_workflows.py if you change it.'},
        code_node('Parse AI output', 'a_parse_ai.js', [2200, 420], MODEL_ID=MODEL_ID, PROMPT_VERSION=PROMPT_VERSION),
        code_node('AI call failed', 'a_ai_failed.js', [2200, 600], MODEL_ID=MODEL_ID, PROMPT_VERSION=PROMPT_VERSION),
        code_node('Validate output', 'a_validate_output.js', [2420, 300], lib=True),
        postgres('Store result before responding', store_q,
                 '[ $json.request_id, $json.lease_token, $json.status, JSON.stringify($json.result), '
                 'JSON.stringify($json.meta) ]', [2640, 300]),
        code_node('Stored result response', 'a_stored_response.js', [2860, 300]),
        respond('Respond: stored result', [3080, 300]),
    ]
    connections = {
        'Webhook': link('Validate request'),
        'Validate request': link('Valid request?'),
        'Valid request?': link('Invalid request response', 'Claim generation request (atomic)'),
        'Invalid request response': link('Respond: invalid request'),
        'Claim generation request (atomic)': link('Check claim & context'),
        'Check claim & context': link('Claim outcome'),
        'Claim outcome': link('Existing state response', 'Validate output', 'Email type'),
        'Existing state response': link('Respond: from database'),
        'Email type': link('Prompt: reminder', 'Prompt: booking confirmation', 'Prompt: reply to guest'),
        'Prompt: reminder': link('AI or fixture'),
        'Prompt: booking confirmation': link('AI or fixture'),
        'Prompt: reply to guest': link('AI or fixture'),
        'AI or fixture': link('Fixture output (not AI)', 'Draft with AI (one call)'),
        'Fixture output (not AI)': link('Validate output'),
        'Draft with AI (one call)': link('Parse AI output', 'AI call failed'),
        'OpenAI Chat Model': {'ai_languageModel': [[{'node': 'Draft with AI (one call)',
                                                     'type': 'ai_languageModel', 'index': 0}]]},
        'Parse AI output': link('Validate output'),
        'AI call failed': link('Validate output'),
        'Validate output': link('Store result before responding'),
        'Store result before responding': link('Stored result response'),
        'Stored result response': link('Respond: stored result'),
    }
    return {'name': 'G2 AI email A - draft (reminder | booking confirmation | reply to guest)',
            'nodes': nodes, 'connections': connections, 'pinData': {},
            'settings': {**SETTINGS, 'executionTimeout': 120}, 'active': False, 'tags': []}


def workflow_b() -> dict:
    claim_q = f'SELECT {SCHEMA}.claim_send_job($1::uuid, $2::text, \'n8n\', $3::text) AS r;'
    begin_q = f'SELECT {SCHEMA}.begin_dispatch($1::uuid, $2::uuid) AS r;'
    finish_q = f'SELECT {SCHEMA}.finish_dispatch($1::uuid, $2::uuid, $3::text, $4::text, $5::text) AS r;'
    nodes = [
        webhook('Webhook', 'g2-ai-email-send', [0, 300]),
        code_node('Validate send request', 'b_validate_request.js', [220, 300], SENDER_REF=SENDER_REF),
        switch('Valid send request?', 2, '$json.valid ? 1 : 0', [440, 300]),
        code_node('Invalid send response', 'respond_with.js', [660, 120]),
        respond('Respond: invalid send request', [880, 120], 400),
        postgres('Claim send job (atomic)', claim_q,
                 '[ $json.job_id, $json.workspace_id, $json.sender_ref ]', [660, 300]),
        code_node('Check claim', 'b_check_claim.js', [880, 300]),
        switch('Claimed?', 2, "$json.route === 'dispatch' ? 1 : 0", [1100, 300]),
        code_node('Claim state response', 'respond_with.js', [1320, 120]),
        respond('Respond: claim state', [1540, 120]),
        postgres('Begin dispatch (re-check approval)', begin_q, '[ $json.job_id, $json.lease_token ]',
                 [1320, 340]),
        code_node('Check dispatch', 'b_check_dispatch.js', [1540, 340]),
        switch('Delivery mode', 4, "({ respond: 0, finish: 1, mock: 2, gmail: 3 })[$json.route]", [1760, 340]),
        code_node('Dispatch refused response', 'respond_with.js', [1980, 100]),
        respond('Respond: dispatch refused', [2200, 100]),
        code_node('Simulate send (no email)', 'b_simulate_send.js', [1980, 340]),
        {'parameters': {'resource': 'message', 'operation': 'send', 'sendTo': '={{ $json.to }}',
                        'subject': '={{ $json.subject }}', 'emailType': 'text', 'message': '={{ $json.body }}',
                        'options': {'appendAttribution': False}},
         'id': nid('Send test email (Gmail Send)'), 'name': 'Send test email (Gmail Send)',
         'type': 'n8n-nodes-base.gmail', 'typeVersion': 2.1, 'position': [1980, 540],
         'retryOnFail': False, 'onError': 'continueErrorOutput',
         'notes': 'Gmail Send (not Reply: sample messages have no Gmail thread). Credential: Gmail OAuth2, '
                  'managed "Sign in with Google" on n8n Cloud. Retry OFF: never rerun this node alone.'},
        code_node('Gmail accepted', 'b_gmail_result.js', [2200, 480], GMAIL_BRANCH='ok'),
        code_node('Gmail error', 'b_gmail_result.js', [2200, 620], GMAIL_BRANCH='error'),
        postgres('Store send result before responding', finish_q,
                 '[ $json.job_id, $json.lease_token, $json.outcome, $json.provider_message_id, $json.detail ]',
                 [2420, 340]),
        code_node('Send result response', 'b_stored_response.js', [2640, 340]),
        respond('Respond: send result', [2860, 340]),
    ]
    connections = {
        'Webhook': link('Validate send request'),
        'Validate send request': link('Valid send request?'),
        'Valid send request?': link('Invalid send response', 'Claim send job (atomic)'),
        'Invalid send response': link('Respond: invalid send request'),
        'Claim send job (atomic)': link('Check claim'),
        'Check claim': link('Claimed?'),
        'Claimed?': link('Claim state response', 'Begin dispatch (re-check approval)'),
        'Claim state response': link('Respond: claim state'),
        'Begin dispatch (re-check approval)': link('Check dispatch'),
        'Check dispatch': link('Delivery mode'),
        'Delivery mode': link('Dispatch refused response', 'Store send result before responding',
                              'Simulate send (no email)', 'Send test email (Gmail Send)'),
        'Dispatch refused response': link('Respond: dispatch refused'),
        'Simulate send (no email)': link('Store send result before responding'),
        'Send test email (Gmail Send)': link('Gmail accepted', 'Gmail error'),
        'Gmail accepted': link('Store send result before responding'),
        'Gmail error': link('Store send result before responding'),
        'Store send result before responding': link('Send result response'),
        'Send result response': link('Respond: send result'),
    }
    return {'name': 'G2 AI email B - send approved DEMO email (mock | team test inbox)',
            'nodes': nodes, 'connections': connections, 'pinData': {},
            'settings': {**SETTINGS, 'executionTimeout': 90}, 'active': False, 'tags': []}


def write() -> None:
    for out, wf in ((OUT_A, workflow_a()), (OUT_B, workflow_b())):
        out.write_text(json.dumps(wf, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(f'Written {out.relative_to(HERE.parent.parent)}')


if __name__ == '__main__':
    write()
