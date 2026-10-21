-- G2 migration for the EXISTING project PostgreSQL schema (sql/schema.sql).
-- Mirrors dashboard/state_store.py so the same rules hold in the shared database.
-- Order: schema.sql -> load.sql -> verify_load.sql -> this file -> g2_verify_guard.sql
--   psql -d <db> -v ON_ERROR_STOP=1 -f sql/g2_approval_guard_postgres.sql
--   psql -d <db> -v ON_ERROR_STOP=1 -f sql/g2_verify_guard.sql
-- Run verify_load.sql BEFORE this file: its rolled-back "valid rows" test puts an
-- 'overbooking' action on a booking, which this migration forbids (date x room-type action).
-- schema.sql drops and recreates the schema, so re-apply this file after it.
-- Idempotent: safe to run twice. It adds protections only; it does NOT execute actions.
-- STATUS: 20/10/2026, PostgreSQL 16, isolated cluster: schema -> load -> verify_load (30/30)
-- -> this file -> g2_verify_guard.sql (19/19, incl. G3), and both again: all passed.
-- Earlier: run in the 19/10/2026 review (schema -> load -> verify_load ->
-- this file -> g2_verify_guard.sql, and this file + g2_verify_guard.sql a second time: all
-- passed). Apply in a test database first.
BEGIN;
SET search_path TO hotel;

-- 1. Link each action to F's dated schedule (run, decision time, due time).
ALTER TABLE action_plan
  ADD COLUMN IF NOT EXISTS source_run_id TEXT,
  ADD COLUMN IF NOT EXISTS decision_at   TIMESTAMP,
  ADD COLUMN IF NOT EXISTS scheduled_at  TIMESTAMP,
  ADD COLUMN IF NOT EXISTS message_no    SMALLINT;
-- Execution-time checks (consent, open action, active status) recorded with the execution.
ALTER TABLE interventions ADD COLUMN IF NOT EXISTS precheck JSONB;

-- 2. Declarative rules (decision_table_draft.md §6.1 and the action level of §3 / §4).
ALTER TABLE action_plan DROP CONSTRAINT IF EXISTS g2_sensitive_action_requires_approval;
ALTER TABLE action_plan ADD CONSTRAINT g2_sensitive_action_requires_approval CHECK (
  action_type NOT IN ('offer_change','request_deposit','price_adjustment',
                      'capacity_allocation','resale','overbooking')
  OR needs_approval = TRUE);

ALTER TABLE action_plan DROP CONSTRAINT IF EXISTS g2_booking_action_has_booking;
ALTER TABLE action_plan ADD CONSTRAINT g2_booking_action_has_booking CHECK (
  action_type NOT IN ('confirm_email','reminder','phone_call','offer_change','request_deposit')
  OR booking_id IS NOT NULL);

ALTER TABLE action_plan DROP CONSTRAINT IF EXISTS g2_date_action_has_date_room;
ALTER TABLE action_plan ADD CONSTRAINT g2_date_action_has_date_room CHECK (
  action_type NOT IN ('price_adjustment','capacity_allocation','resale','overbooking')
  OR (stay_date IS NOT NULL AND room_type IS NOT NULL));

-- G3 overbooking is disabled by the G1 policy (g1_policy.json: overbooking.enabled = false):
-- no overbooking action may exist, whatever its approval. Lift only with a tested G3 policy.
-- Fails loudly if an overbooking row already exists, instead of hiding it.
ALTER TABLE action_plan DROP CONSTRAINT IF EXISTS g2_g3_overbooking_disabled;
ALTER TABLE action_plan ADD CONSTRAINT g2_g3_overbooking_disabled CHECK (action_type <> 'overbooking');

ALTER TABLE action_plan DROP CONSTRAINT IF EXISTS g2_schedule_after_decision;
ALTER TABLE action_plan ADD CONSTRAINT g2_schedule_after_decision CHECK (
  scheduled_at IS NULL OR decision_at IS NULL OR scheduled_at >= decision_at);

-- One action per booking x type x due time; one execution per action.
CREATE UNIQUE INDEX IF NOT EXISTS g2_ux_action_schedule
  ON action_plan (booking_id, action_type, scheduled_at) WHERE scheduled_at IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS g2_ux_one_execution ON interventions (action_id);

-- 3. §6.2: an intervention only for approved or auto actions.
CREATE OR REPLACE FUNCTION g2_check_intervention_approval() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM hotel.action_plan a
    WHERE a.action_id = NEW.action_id
      AND (a.approval_status = 'approved'
           OR (a.approval_status = 'auto' AND a.needs_approval = FALSE))
  ) THEN
    RAISE EXCEPTION 'G2 approval guard: cannot execute unapproved action_id %', NEW.action_id;
  END IF;
  -- Second line of defence for G3 (the CHECK above already forbids such rows).
  IF EXISTS (SELECT 1 FROM hotel.action_plan a
             WHERE a.action_id = NEW.action_id AND a.action_type = 'overbooking') THEN
    RAISE EXCEPTION 'G2 guard: G3 overbooking is disabled (action_id %)', NEW.action_id;
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS g2_approval_guard ON interventions;
CREATE TRIGGER g2_approval_guard BEFORE INSERT ON interventions
  FOR EACH ROW EXECUTE FUNCTION g2_check_intervention_approval();

-- 4. The intervention log is append-only: no re-pointing a row to another action, no delete.
--    (Without this, a row inserted for an approved action could be moved to a pending one.)
CREATE OR REPLACE FUNCTION g2_interventions_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'G2 guard: interventions is append-only (% blocked)', TG_OP;
END;
$$;
DROP TRIGGER IF EXISTS g2_interventions_append_only ON interventions;
CREATE TRIGGER g2_interventions_append_only BEFORE UPDATE OR DELETE ON interventions
  FOR EACH ROW EXECUTE FUNCTION g2_interventions_append_only();

-- 5. §6.3: an approval decision is final (pending -> approved / rejected only), and what was
--    approved cannot be swapped afterwards.
CREATE OR REPLACE FUNCTION g2_action_plan_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.booking_id, NEW.stay_date, NEW.room_type, NEW.action_type, NEW.rule_id,
      NEW.source_run_id, NEW.decision_at, NEW.scheduled_at, NEW.message_no, NEW.created_at)
     IS DISTINCT FROM
     (OLD.booking_id, OLD.stay_date, OLD.room_type, OLD.action_type, OLD.rule_id,
      OLD.source_run_id, OLD.decision_at, OLD.scheduled_at, OLD.message_no, OLD.created_at) THEN
    RAISE EXCEPTION 'G2 guard: action identity is immutable (action_id %)', OLD.action_id;
  END IF;
  IF (NEW.approval_status, NEW.approved_by, NEW.approved_at, NEW.needs_approval)
     IS DISTINCT FROM (OLD.approval_status, OLD.approved_by, OLD.approved_at, OLD.needs_approval)
     AND (OLD.approval_status <> 'pending'
          OR NEW.approval_status NOT IN ('approved', 'rejected')
          OR NEW.needs_approval IS DISTINCT FROM OLD.needs_approval) THEN
    RAISE EXCEPTION 'G2 guard: approval decision is final (action_id %: % -> %)',
      OLD.action_id, OLD.approval_status, NEW.approval_status;
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS g2_action_plan_transition ON action_plan;
CREATE TRIGGER g2_action_plan_transition BEFORE UPDATE ON action_plan
  FOR EACH ROW EXECUTE FUNCTION g2_action_plan_transition();

COMMIT;
-- NOTE: the API / n8n must still check approval, consent, open actions and booking status at
-- execution time and authenticate the requesting user. Triggers are the last line of defence,
-- not an authentication system.
