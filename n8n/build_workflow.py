"""Regenerate n8n/g2_approval_demo_workflow.json from the node code in n8n/src/.

Edit n8n/src/guard.js or dispatch.js (tested by tests/test_g2_n8n.py), then run:
    python n8n/build_workflow.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # repo root
SRC = ROOT / 'n8n' / 'src'
OUT = ROOT / 'n8n' / 'g2_approval_demo_workflow.json'
GUARD, DISPATCH = 'Approval & execution-time guard', 'Mock dispatch (nothing sent)'


def workflow() -> dict:
    code = lambda f: (SRC / f).read_text(encoding='utf-8')  # noqa: E731
    return {
        'name': 'G2 Hotel - Approval & execution guard demo (no emails)',
        'nodes': [
            {'parameters': {'httpMethod': 'POST', 'path': 'g2-hotel-approval-demo',
                            'responseMode': 'responseNode', 'options': {}},
             'id': '8e56d8b0-bba4-451a-9971-caf3a5b16b44', 'name': 'Webhook',
             'type': 'n8n-nodes-base.webhook', 'typeVersion': 2, 'position': [240, 300],
             'webhookId': 'c037759c-7b8e-4627-ab79-d61cf2816246'},
            {'parameters': {'jsCode': code('guard.js')},
             'id': '3d7131e0-08d7-49d9-b0ec-a1623b71b05a', 'name': GUARD,
             'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [480, 300]},
            {'parameters': {'jsCode': code('dispatch.js')},
             'id': '6a0f4c52-1f7e-4d4b-9b0e-2c8d3e7f9a11', 'name': DISPATCH,
             'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [720, 300]},
            {'parameters': {'respondWith': 'firstIncomingItem', 'options': {'responseCode': 200}},
             'id': '3b9c0280-93d0-4e2f-80ec-a5b05641ed79', 'name': 'Respond to Webhook',
             'type': 'n8n-nodes-base.respondToWebhook', 'typeVersion': 1.1, 'position': [960, 300]},
        ],
        'connections': {
            'Webhook': {'main': [[{'node': GUARD, 'type': 'main', 'index': 0}]]},
            GUARD: {'main': [[{'node': DISPATCH, 'type': 'main', 'index': 0}]]},
            DISPATCH: {'main': [[{'node': 'Respond to Webhook', 'type': 'main', 'index': 0}]]},
        },
        'pinData': {}, 'settings': {'executionOrder': 'v1'}, 'active': False, 'tags': [],
    }


if __name__ == '__main__':
    OUT.write_text(json.dumps(workflow(), indent=2), encoding='utf-8')
    print(f'Written {OUT.relative_to(ROOT)}')
