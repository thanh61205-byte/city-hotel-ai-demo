#!/usr/bin/env bash
# G2 local demo (macOS / Linux): Streamlit dashboard + the n8n demo workflow on this computer.
# Nothing is sent to guests: the workflow only checks the action and replies (executed = false).
#
#   bash run_demo.sh            # dashboard + n8n (needs Node.js 24+)
#   bash run_demo.sh --no-n8n   # dashboard only
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"
N8N_VERSION=2.43.8
N8N_DIR="$ROOT/.n8n-local"; N8N="$N8N_DIR/node_modules/.bin/n8n"
PORT=5690; WEBHOOK="http://127.0.0.1:$PORT/webhook/g2-hotel-approval-demo"
NAME='G2 Hotel - Approval & execution guard demo (no emails)'
USE_N8N=1; [ "${1:-}" = "--no-n8n" ] && USE_N8N=0
step() { printf '\033[36m[G2 demo] %s\033[0m\n' "$*"; }
up() { curl -fs -m 2 "http://127.0.0.1:$PORT/healthz/readiness" >/dev/null 2>&1; }
alive() { curl -fs -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; }

# 1. Python environment
if [ ! -x .venv/bin/python ]; then
  step 'Creating the Python environment .venv ...'
  python3 -m venv .venv
fi
.venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo 'Python 3.11 or newer is required.'; exit 1; }
step 'Installing dashboard packages (requirements-g2.txt) ...'
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements-g2.txt

# 2. n8n (optional)
N8N_PID=""
if [ "$USE_N8N" = 1 ]; then
  export N8N_USER_FOLDER="$N8N_DIR/data" N8N_PORT="$PORT" N8N_RUNNERS_BROKER_PORT="$((PORT + 1))" \
         N8N_LISTEN_ADDRESS=127.0.0.1 N8N_DIAGNOSTICS_ENABLED=false N8N_PERSONALIZATION_ENABLED=false
  if alive; then
    step 'Waiting for the existing n8n instance to be ready ...'
    for _ in $(seq 1 150); do up && break; sleep 2; done
    up || { echo 'Existing n8n is not ready; inspect its terminal before retrying.'; exit 1; }
    step "n8n is already running on port $PORT: reusing it."
  elif ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 24 ]; then
    echo '[G2 demo] Node.js 24+ not found: starting the dashboard WITHOUT n8n (install Node.js 24 LTS to add it).'
    USE_N8N=0
  else
    if [ ! -x "$N8N" ]; then
      step "Installing n8n $N8N_VERSION into .n8n-local (first run only, a few minutes) ..."
      npm install --prefix "$N8N_DIR" "n8n@$N8N_VERSION" --no-audit --no-fund --loglevel=error
    fi
    LINE="$("$N8N" list:workflow 2>/dev/null | grep -F "|$NAME" | head -1 || true)"
    if [ -z "$LINE" ]; then
      step 'Importing the demo workflow (n8n/g2_approval_demo_workflow.json) ...'
      "$N8N" import:workflow --input="$ROOT/n8n/g2_approval_demo_workflow.json" >/dev/null
      LINE="$("$N8N" list:workflow 2>/dev/null | grep -F "|$NAME" | head -1)"
    fi
    "$N8N" publish:workflow --id="${LINE%%|*}" >/dev/null
    step 'Starting n8n (log: .n8n-local/n8n.log) ...'
    "$N8N" start > "$N8N_DIR/n8n.log" 2>&1 &
    N8N_PID=$!
    trap '[ -n "$N8N_PID" ] && kill "$N8N_PID" 2>/dev/null' EXIT
    for _ in $(seq 1 150); do up && break; sleep 2; done
    up || { echo "n8n did not start (see .n8n-local/n8n.log)."; exit 1; }
    step 'n8n is ready.'
  fi
fi

# 3. Dashboard
if [ "$USE_N8N" = 1 ]; then export G2_N8N_WEBHOOK_URL="$WEBHOOK"; else unset G2_N8N_WEBHOOK_URL; fi
step 'Opening the dashboard at http://localhost:8501 (Ctrl+C to stop dashboard and n8n) ...'
.venv/bin/python -m streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
