-- Checks for sql/g2_approval_guard_postgres.sql (decision_table_draft.md §6.4 + G2 rules).
-- Run after the migration, on a loaded database:
--   psql -d <db> -v ON_ERROR_STOP=1 -f sql/g2_verify_guard.sql
-- Everything runs in one transaction that is rolled back; nothing is left in the tables.
-- STATUS: 19/19 PASS on PostgreSQL 16 on 20/10/2026 (isolated cluster: schema -> load -> verify_load
-- -> migration -> this file, then migration + this file again), including the G3 checks of section 5.
SET search_path TO hotel;
BEGIN;

-- Expect `stmt` to fail with SQLSTATE `state` (23514 check, 23505 unique, P0001 trigger).
CREATE FUNCTION pg_temp.g2_expect(label TEXT, stmt TEXT, state TEXT) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE rejected BOOLEAN := FALSE;
BEGIN
  BEGIN
    EXECUTE stmt;
  EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = state THEN rejected := TRUE;
    ELSE RAISE EXCEPTION 'FAIL  % (got %: %)', label, SQLSTATE, SQLERRM;
    END IF;
  END;
  IF NOT rejected THEN RAISE EXCEPTION 'FAIL  % (statement was accepted)', label; END IF;
  RAISE NOTICE 'PASS  %', label;
END $$;

-- Fixtures: one auto booking action, and pending / rejected / approved date x room-type actions.
CREATE TEMP TABLE g2_fx (k TEXT PRIMARY KEY, action_id BIGINT NOT NULL) ON COMMIT DROP;
CREATE TEMP TABLE g2_cell ON COMMIT DROP AS
  SELECT stay_date, room_type FROM ops_inventory_daily ORDER BY stay_date, room_type LIMIT 1;

WITH a AS (INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status)
           VALUES (1, 'phone_call', 'G2TEST', FALSE, 'auto') RETURNING action_id)
INSERT INTO g2_fx SELECT 'auto_call', action_id FROM a;
WITH a AS (INSERT INTO action_plan (stay_date, room_type, action_type, rule_id, needs_approval, approval_status)
           SELECT stay_date, room_type, 'resale', 'G2TEST', TRUE, 'pending' FROM g2_cell RETURNING action_id)
INSERT INTO g2_fx SELECT 'pending_resale', action_id FROM a;
WITH a AS (INSERT INTO action_plan (stay_date, room_type, action_type, rule_id, needs_approval,
                                    approval_status, approved_by, approved_at)
           SELECT stay_date, room_type, 'price_adjustment', 'G2TEST', TRUE, 'rejected', 'rm', now() FROM g2_cell
           RETURNING action_id)
INSERT INTO g2_fx SELECT 'rejected_price', action_id FROM a;
WITH a AS (INSERT INTO action_plan (stay_date, room_type, action_type, rule_id, needs_approval,
                                    approval_status, approved_by, approved_at)
           SELECT stay_date, room_type, 'capacity_allocation', 'G2TEST', TRUE, 'approved', 'rm', now()
           FROM g2_cell RETURNING action_id)
INSERT INTO g2_fx SELECT 'approved_capacity', action_id FROM a;

-- 1. Execution guard (§6.2)
SELECT pg_temp.g2_expect('intervention for a pending action is refused',
  $q$INSERT INTO interventions (action_id, channel, executed_at, executed_by)
     VALUES ((SELECT action_id FROM g2_fx WHERE k = 'pending_resale'), 'task', now(), 'staff')$q$, 'P0001');
SELECT pg_temp.g2_expect('intervention for a rejected action is refused',
  $q$INSERT INTO interventions (action_id, channel, executed_at, executed_by)
     VALUES ((SELECT action_id FROM g2_fx WHERE k = 'rejected_price'), 'task', now(), 'staff')$q$, 'P0001');
INSERT INTO interventions (action_id, channel, executed_at, executed_by, outcome)
  VALUES ((SELECT action_id FROM g2_fx WHERE k = 'auto_call'), 'phone', now(), 'staff', 'no_response'),
         ((SELECT action_id FROM g2_fx WHERE k = 'approved_capacity'), 'task', now(), 'staff', 'completed');
DO $$ BEGIN RAISE NOTICE 'PASS  interventions for auto and approved actions are accepted'; END $$;
SELECT pg_temp.g2_expect('a second execution of the same action is refused',
  $q$INSERT INTO interventions (action_id, channel, executed_at, executed_by)
     VALUES ((SELECT action_id FROM g2_fx WHERE k = 'auto_call'), 'phone', now(), 'staff')$q$, '23505');

-- 2. Append-only log
SELECT pg_temp.g2_expect('an intervention cannot be re-pointed to a pending action',
  $q$UPDATE interventions SET action_id = (SELECT action_id FROM g2_fx WHERE k = 'pending_resale')
     WHERE action_id = (SELECT action_id FROM g2_fx WHERE k = 'auto_call')$q$, 'P0001');
SELECT pg_temp.g2_expect('an intervention cannot be deleted',
  $q$DELETE FROM interventions WHERE action_id = (SELECT action_id FROM g2_fx WHERE k = 'auto_call')$q$, 'P0001');

-- 3. Declarative rules (§6.1, action level)
SELECT pg_temp.g2_expect('a sensitive action cannot be auto / without approval',
  $q$INSERT INTO action_plan (stay_date, room_type, action_type, rule_id, needs_approval, approval_status)
     SELECT stay_date, room_type, 'resale', 'G2TEST', FALSE, 'auto' FROM g2_cell$q$, '23514');
SELECT pg_temp.g2_expect('a date x room-type action cannot sit on a booking only',
  $q$INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status)
     VALUES (1, 'price_adjustment', 'G2TEST', TRUE, 'pending')$q$, '23514');
SELECT pg_temp.g2_expect('a guest action needs a booking',
  $q$INSERT INTO action_plan (stay_date, room_type, action_type, rule_id, needs_approval, approval_status)
     SELECT stay_date, room_type, 'reminder', 'G2TEST', FALSE, 'auto' FROM g2_cell$q$, '23514');
SELECT pg_temp.g2_expect('an action cannot be scheduled before its decision time',
  $q$INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status,
                              decision_at, scheduled_at)
     VALUES (1, 'reminder', 'G2TEST', FALSE, 'auto', '2017-01-08', '2017-01-01')$q$, '23514');
INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status, decision_at, scheduled_at)
  VALUES (1, 'reminder', 'G2TEST', FALSE, 'auto', '2017-01-01', '2017-01-05');
SELECT pg_temp.g2_expect('the same scheduled action cannot be created twice',
  $q$INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status,
                              decision_at, scheduled_at)
     VALUES (1, 'reminder', 'G2TEST', FALSE, 'auto', '2017-01-01', '2017-01-05')$q$, '23505');

-- 4. Decisions are final; what was approved cannot change (§6.3)
SELECT pg_temp.g2_expect('rejected cannot be flipped to approved',
  $q$UPDATE action_plan SET approval_status = 'approved'
     WHERE action_id = (SELECT action_id FROM g2_fx WHERE k = 'rejected_price')$q$, 'P0001');
SELECT pg_temp.g2_expect('pending cannot become auto',
  $q$UPDATE action_plan SET needs_approval = FALSE, approval_status = 'auto'
     WHERE action_id = (SELECT action_id FROM g2_fx WHERE k = 'pending_resale')$q$, 'P0001');
SELECT pg_temp.g2_expect('an approved action cannot change type',
  $q$UPDATE action_plan SET action_type = 'resale'
     WHERE action_id = (SELECT action_id FROM g2_fx WHERE k = 'approved_capacity')$q$, 'P0001');
UPDATE action_plan SET approval_status = 'approved', approved_by = 'rm', approved_at = now()
  WHERE action_id = (SELECT action_id FROM g2_fx WHERE k = 'pending_resale');
DO $$ BEGIN RAISE NOTICE 'PASS  pending -> approved with reviewer is accepted'; END $$;

-- 5. G3 overbooking is disabled by the G1 policy, whatever the approval
SELECT pg_temp.g2_expect('a pending overbooking action is refused',
  $q$INSERT INTO action_plan (stay_date, room_type, action_type, rule_id, needs_approval, approval_status)
     SELECT stay_date, room_type, 'overbooking', 'G3', TRUE, 'pending' FROM g2_cell$q$, '23514');
SELECT pg_temp.g2_expect('an approved overbooking action is refused',
  $q$INSERT INTO action_plan (stay_date, room_type, action_type, rule_id, needs_approval,
                              approval_status, approved_by, approved_at)
     SELECT stay_date, room_type, 'overbooking', 'G3', TRUE, 'approved', 'gm', now() FROM g2_cell$q$, '23514');
SELECT pg_temp.g2_expect('a pending action cannot be turned into overbooking',
  $q$UPDATE action_plan SET action_type = 'overbooking'
     WHERE action_id = (SELECT action_id FROM g2_fx WHERE k = 'pending_resale')$q$, 'P0001');
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'g2_g3_overbooking_disabled') THEN
    RAISE EXCEPTION 'FAIL  constraint g2_g3_overbooking_disabled is missing';
  END IF;
  RAISE NOTICE 'PASS  constraint g2_g3_overbooking_disabled is installed';
END $$;

ROLLBACK;
\echo 'All G2 guard checks passed.'
