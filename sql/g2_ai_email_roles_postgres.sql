-- Least-privilege roles for the G2 AI email store (run as the schema owner AFTER
-- sql/g2_ai_email_postgres.sql). Choose the passwords yourself and keep them out of Git:
--
--   psql "$G2_AI_OWNER_DB_URL" -v app_pw=... -v n8n_pw=... -f sql/g2_ai_email_roles_postgres.sql
-- or, without psql:  python -m dashboard.ai_email.bootstrap --create-roles   (passwords from env)
--
-- Hosted providers differ: if your plan cannot CREATE ROLE, create the two roles in the provider's
-- console and run only the GRANT part (after "@grants"). Connection strings built from these roles
-- go into Streamlit Cloud Secrets (G2_AI_DB_URL) and the n8n Postgres credential, never into Git.
--
-- g2_ai_app  (Streamlit): writes requests, revisions, approvals / revocations, send jobs and events;
--                         reads everything; may run the local mock executor functions.
-- g2_ai_n8n  (n8n Cloud): no table access at all; EXECUTE on the workflow functions only. It can
--                         store a generation result and claim / finish a send job, but it cannot
--                         approve, edit a revision or add a recipient.
SELECT format('CREATE ROLE g2_ai_app LOGIN PASSWORD %L', :'app_pw')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'g2_ai_app') \gexec
SELECT format('CREATE ROLE g2_ai_n8n LOGIN PASSWORD %L', :'n8n_pw')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'g2_ai_n8n') \gexec

-- @grants
REVOKE ALL ON SCHEMA g2_ai_v1 FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA g2_ai_v1 FROM PUBLIC;
GRANT USAGE ON SCHEMA g2_ai_v1 TO g2_ai_app, g2_ai_n8n;

GRANT SELECT ON ALL TABLES IN SCHEMA g2_ai_v1 TO g2_ai_app;
GRANT INSERT ON g2_ai_v1.workspaces, g2_ai_v1.generation_requests, g2_ai_v1.draft_revisions,
                g2_ai_v1.approvals, g2_ai_v1.send_jobs, g2_ai_v1.events TO g2_ai_app;
GRANT UPDATE (revoked_at, revoked_by, revoke_reason) ON g2_ai_v1.approvals TO g2_ai_app;
GRANT UPDATE (status, version, lease_token, lease_expires_at, detail, updated_at, finished_at,
              receipt_verified_by, receipt_verified_at) ON g2_ai_v1.send_jobs TO g2_ai_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA g2_ai_v1 TO g2_ai_app;
GRANT EXECUTE ON FUNCTION g2_ai_v1.content_hash(text, text), g2_ai_v1.approval_problem(bigint, uuid),
                          g2_ai_v1.log_event(text, text, text, text, text, text, text, jsonb),
                          g2_ai_v1.reconcile_stale(text), g2_ai_v1.claim_generation(uuid, text, text),
                          g2_ai_v1.complete_generation(uuid, uuid, text, jsonb, jsonb),
                          g2_ai_v1.claim_send_job(uuid, text, text, text), g2_ai_v1.begin_dispatch(uuid, uuid),
                          g2_ai_v1.finish_dispatch(uuid, uuid, text, text, text) TO g2_ai_app;

GRANT EXECUTE ON FUNCTION g2_ai_v1.claim_generation(uuid, text, text),
                          g2_ai_v1.complete_generation(uuid, uuid, text, jsonb, jsonb),
                          g2_ai_v1.claim_send_job(uuid, text, text, text), g2_ai_v1.begin_dispatch(uuid, uuid),
                          g2_ai_v1.finish_dispatch(uuid, uuid, text, text, text),
                          g2_ai_v1.reconcile_stale(text) TO g2_ai_n8n;
