-- Runtime verification of sql/schema.sql + sql/load.sql against the Python pipeline.
-- Run from the repo root AFTER schema.sql and load.sql:
--   psql -d <db> -v ON_ERROR_STOP=1 -f sql/verify_load.sql
-- Every check prints PASS; the first FAIL stops the script with an error.
-- Expected values come from the CSVs produced by notebooks 01-03 (see docs/CHANGELOG_A-D2_truoc_F.md).
\pset tuples_only on
SET search_path TO hotel;
SET client_min_messages TO notice;

CREATE FUNCTION pg_temp.chk(label TEXT, ok BOOLEAN) RETURNS VOID AS $$
BEGIN
    IF ok THEN RAISE NOTICE 'PASS  %', label;
    ELSE RAISE EXCEPTION 'FAIL  %', label;
    END IF;
END $$ LANGUAGE plpgsql;

-- 1. Row counts --------------------------------------------------------------------------------
SELECT pg_temp.chk('bookings = 79,330 rows',            (SELECT COUNT(*) FROM bookings) = 79330);
SELECT pg_temp.chk('cx_contact = 79,330 rows',          (SELECT COUNT(*) FROM cx_contact) = 79330);
SELECT pg_temp.chk('cx_feedback = 79,330 rows',         (SELECT COUNT(*) FROM cx_feedback) = 79330);
SELECT pg_temp.chk('fin_booking = 79,330 rows',         (SELECT COUNT(*) FROM fin_booking) = 79330);
SELECT pg_temp.chk('ops_inventory_daily = 7,227 rows',  (SELECT COUNT(*) FROM ops_inventory_daily) = 7227);
SELECT pg_temp.chk('dim_room_type = 9 rows',            (SELECT COUNT(*) FROM dim_room_type) = 9);
SELECT pg_temp.chk('v_booking_360 = 79,330 rows',       (SELECT COUNT(*) FROM v_booking_360) = 79330);

-- 2. Totals must match the Python pipeline ----------------------------------------------------
SELECT pg_temp.chk('cancelled or no-show = 33,102',     (SELECT SUM(is_canceled) FROM bookings) = 33102);
SELECT pg_temp.chk('excluded from model = 438',         (SELECT COUNT(*) FROM bookings WHERE exclude_from_model) = 438);
SELECT pg_temp.chk('gross booked value = 25,274,069.96',
                   (SELECT ABS(SUM(gross_value) - 25274069.96) < 0.01 FROM bookings));
SELECT pg_temp.chk('cancelled gross value = 10,879,659.78',
                   (SELECT ABS(SUM(gross_value) - 10879659.78) < 0.01 FROM bookings WHERE is_canceled = 1));
SELECT pg_temp.chk('simulated rebooking revenue = 4,832,439.67',
                   (SELECT ABS(SUM(rebooking_revenue) - 4832439.67) < 0.01 FROM fin_booking));
SELECT pg_temp.chk('resold room-nights: fin_booking = ops = 49,491',
                   (SELECT SUM(rebooked_room_nights) FROM fin_booking) = 49491
                   AND (SELECT SUM(resold_after_cancellation) FROM ops_inventory_daily) = 49491);
SELECT pg_temp.chk('simulated handling cost = 42,863.62',
                   (SELECT ABS(SUM(cancellation_handling_cost) - 42863.62) < 0.01 FROM fin_booking));
SELECT pg_temp.chk('occupancy Sep-2015..Aug-2017 = 70.8%',
                   (SELECT ROUND(SUM(rooms_sold)::NUMERIC / SUM(available_rooms), 3) = 0.708
                    FROM ops_inventory_daily WHERE NOT is_partial_period));
-- The view rounds the exact NUMERIC value half away from zero; pandas rounds a binary float, so 158 bookings whose exact
-- value ends in half a cent (e.g. 47.845) are 0.01 higher here than in bookings_prepared.csv: total +1.58 EUR. Not a load error.
SELECT pg_temp.chk('view syn_net_booking_value total = 14,589,617.42 + 1.58 half-cent rounding (bookings_prepared.csv)',
                   (SELECT ABS(SUM(syn_net_booking_value) - (14589617.42 + 1.58)) < 0.005 FROM v_booking_360));
SELECT pg_temp.chk('view syn_net_booking_value = exact value rounded to the cent, every row',
                   NOT EXISTS (SELECT 1 FROM v_booking_360 b JOIN fin_booking f USING (booking_id)
                               WHERE ABS(b.syn_net_booking_value
                                         - (b.gross_value * (1 - f.channel_commission_rate) - f.room_cost_per_night * b.total_nights)) > 0.005));
SELECT pg_temp.chk('conditional nulls: rebooking only for Canceled',
                   NOT EXISTS (SELECT 1 FROM v_booking_360
                               WHERE (syn_rebooking_revenue IS NOT NULL) <> (reservation_status = 'Canceled')
                                  OR (syn_rebooked_room_nights IS NOT NULL) <> (reservation_status = 'Canceled')));

-- 3. Constraints must reject bad rows (tested inside a transaction that is rolled back) -------
BEGIN;
DO $$ BEGIN
    INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status)
    VALUES (1, 'request_deposit', 'TEST', TRUE, 'auto');
    RAISE EXCEPTION 'FAIL  action needing approval was accepted with status auto';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS  rejects auto status on an action that needs approval';
END $$;
DO $$ BEGIN
    INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status)
    VALUES (1, 'request_deposit', 'TEST', TRUE, 'approved');
    RAISE EXCEPTION 'FAIL  approval without approved_by / approved_at was accepted';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS  rejects an approval without approved_by / approved_at';
END $$;
DO $$ BEGIN
    INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status, approved_by)
    VALUES (1, 'request_deposit', 'TEST', TRUE, 'approved', 'rm');
    RAISE EXCEPTION 'FAIL  approval with approved_by but no approved_at was accepted';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS  rejects an approval with only one of approved_by / approved_at';
END $$;
DO $$ BEGIN
    INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status, approved_at)
    VALUES (1, 'request_deposit', 'TEST', TRUE, 'rejected', now());
    RAISE EXCEPTION 'FAIL  rejection with approved_at but no approved_by was accepted';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS  rejects a rejection with only one of approved_by / approved_at';
END $$;
DO $$ BEGIN
    INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status, approved_by)
    VALUES (1, 'request_deposit', 'TEST', TRUE, 'pending', 'reviewer');
    RAISE EXCEPTION 'FAIL  pending action with approved_by was accepted';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS  rejects a pending action that already has approved_by';
END $$;
DO $$ BEGIN
    INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status, approved_at)
    VALUES (1, 'request_deposit', 'TEST', TRUE, 'pending', now());
    RAISE EXCEPTION 'FAIL  pending action with approved_at was accepted';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS  rejects a pending action that already has approved_at';
END $$;
DO $$ BEGIN
    INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status, approved_by, approved_at)
    VALUES (1, 'reminder', 'TEST', FALSE, 'auto', 'rm', now());
    RAISE EXCEPTION 'FAIL  auto action with approval metadata was accepted';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS  rejects an auto action that carries approval metadata';
END $$;
DO $$ BEGIN
    INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status, approved_by, approved_at, created_at)
    VALUES (1, 'request_deposit', 'TEST', TRUE, 'approved', 'rm', '2017-01-01 09:00', '2017-01-02 09:00');
    RAISE EXCEPTION 'FAIL  approved_at before created_at was accepted';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS  rejects approved_at before created_at';
END $$;
DO $$ BEGIN
    INSERT INTO pilot_assignment (pilot_id, booking_id, arm, assigned_at) VALUES ('TEST', 1, 'control', now());
    RAISE EXCEPTION 'FAIL  invalid pilot arm was accepted';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS  rejects an arm other than treatment / holdout';
END $$;
DO $$ BEGIN
    INSERT INTO pilot_assignment (pilot_id, booking_id, arm, assigned_at) VALUES ('TEST', 999999, 'treatment', now());
    RAISE EXCEPTION 'FAIL  pilot assignment for an unknown booking was accepted';
EXCEPTION WHEN foreign_key_violation THEN RAISE NOTICE 'PASS  rejects a pilot assignment for an unknown booking';
END $$;
DO $$ BEGIN
    UPDATE fin_booking SET rebooked_room_nights = -1 WHERE booking_id = 2;
    RAISE EXCEPTION 'FAIL  negative rebooked_room_nights was accepted';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS  rejects negative rebooked_room_nights';
END $$;
-- valid rows must be accepted: approved, rejected, pending and auto actions, and a pilot assignment
INSERT INTO action_plan (booking_id, action_type, rule_id, needs_approval, approval_status, approved_by, approved_at, created_at) VALUES
    (1, 'request_deposit', 'TEST', TRUE,  'approved', 'rm', '2017-01-02 10:00', '2017-01-02 09:00'),
    (1, 'overbooking',     'TEST', TRUE,  'rejected', 'gm', '2017-01-02 11:00', '2017-01-02 09:00'),
    (1, 'phone_call',      'TEST', TRUE,  'pending',  NULL, NULL,               '2017-01-02 09:00'),
    (1, 'reminder',        'TEST', FALSE, 'auto',     NULL, NULL,               '2017-01-02 09:00');
INSERT INTO pilot_assignment (pilot_id, booking_id, arm, stratum, assigned_at) VALUES ('TEST', 1, 'treatment', 'high', now());
SELECT pg_temp.chk('accepts valid approved / rejected / pending / auto actions and a valid pilot assignment',
                   (SELECT COUNT(*) FROM action_plan WHERE rule_id = 'TEST') = 4
                   AND (SELECT COUNT(*) FROM pilot_assignment WHERE pilot_id = 'TEST') = 1);
ROLLBACK;

\echo 'All checks passed.'
