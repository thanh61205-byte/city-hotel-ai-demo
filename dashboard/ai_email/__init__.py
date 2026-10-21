"""AI email drafts (docs/SPEC_AI_N8N_VI.md): three email types drafted through n8n, reviewed and
approved by staff in Streamlit, then sent as a DEMO test (mock, or the team's test inbox).

Modules:
  contract   email types, enums, the source context sent to n8n and its hash
  dates      date mentions and relative dates ("Friday", "next week") as of the business date
  render     placeholder resolver, deterministic facts block, DEMO prefix / footer, content hash
  validate   output schema per email type, evidence / source checks, send blockers
  fixtures   labelled demo fixtures (not AI predictions) and manual drafts
  store      PostgreSQL (shared) or SQLite (local offline) store: requests, revisions,
             approvals, send jobs, events; the claim protocol of docs/SPEC_AI_N8N_VI.md §6.1
  client     n8n Cloud webhooks A (draft) and B (send) with authentication and timeouts
  config     secrets / environment adapter (Streamlit Cloud Secrets or env vars)
  panel      the "AI email drafts" section of the Execution & follow-up page

Nothing here writes guest_responses / interventions or changes a G2 action: an AI label is not a
booking state, and a DEMO test email is not a guest contact.
"""
