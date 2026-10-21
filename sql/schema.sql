-- =============================================================================
-- Booking Cancellation Decision Support System — City Hotel, Lisbon
-- PostgreSQL schema (README §4). Run: psql -d <db> -f sql/schema.sql
-- Layers:  core (real) · cx / finance / ops (synthetic, is_synthetic = TRUE)
--          · system (written by the model, rule engine and n8n)
-- =============================================================================

DROP SCHEMA IF EXISTS hotel CASCADE;
CREATE SCHEMA hotel;
SET search_path TO hotel;

-- -----------------------------------------------------------------------------
-- DIMENSIONS
-- -----------------------------------------------------------------------------
CREATE TABLE dim_room_type (
    room_type        VARCHAR(2) PRIMARY KEY,           -- anonymised code from PMS (A..P)
    capacity_rooms   SMALLINT   NOT NULL CHECK (capacity_rooms >= 0),
    is_sellable      BOOLEAN    NOT NULL DEFAULT TRUE,
    capacity_source  TEXT       NOT NULL               -- 'derived: peak occupied rooms 2015-09..2017-08'
);


-- -----------------------------------------------------------------------------
-- CORE (real data, from data/processed/bookings_city_clean.csv)
-- -----------------------------------------------------------------------------
CREATE TABLE bookings (
    booking_id                     INTEGER PRIMARY KEY,  -- sequential 1..79,330, assigned after filtering to City Hotel
    is_canceled                    SMALLINT NOT NULL CHECK (is_canceled IN (0, 1)),
    reservation_status             VARCHAR(10) NOT NULL CHECK (reservation_status IN ('Check-Out','Canceled','No-Show')),
    reservation_status_date        DATE NOT NULL,
    booking_date                   DATE NOT NULL,
    arrival_date                   DATE NOT NULL,
    lead_time                      INTEGER NOT NULL CHECK (lead_time >= 0),
    stays_in_weekend_nights        SMALLINT NOT NULL,
    stays_in_week_nights           SMALLINT NOT NULL,
    adults                         SMALLINT NOT NULL,
    children                       SMALLINT NOT NULL,
    babies                         SMALLINT NOT NULL,
    meal                           VARCHAR(3) NOT NULL,
    country                        VARCHAR(4) NOT NULL,
    market_segment                 VARCHAR(20) NOT NULL,
    distribution_channel           VARCHAR(12) NOT NULL,
    is_repeated_guest              SMALLINT NOT NULL,
    previous_cancellations         SMALLINT NOT NULL,
    previous_bookings_not_canceled SMALLINT NOT NULL,
    reserved_room_type             VARCHAR(2) NOT NULL REFERENCES dim_room_type(room_type),
    assigned_room_type             VARCHAR(2) NOT NULL REFERENCES dim_room_type(room_type),
    booking_changes                SMALLINT NOT NULL,
    deposit_type                   VARCHAR(12) NOT NULL,
    days_in_waiting_list           SMALLINT NOT NULL,
    customer_type                  VARCHAR(16) NOT NULL,
    total_of_special_requests      SMALLINT NOT NULL,
    has_agent                      SMALLINT NOT NULL,
    has_company                    SMALLINT NOT NULL,
    dup_group_size                 SMALLINT NOT NULL,
    agent_id                       VARCHAR(8) NOT NULL,  -- 'none' = no agent
    arrival_month_num              SMALLINT NOT NULL,
    arrival_week_iso               SMALLINT NOT NULL,
    arrival_dow                    VARCHAR(10) NOT NULL,
    total_nights                   SMALLINT NOT NULL,
    total_guests                   SMALLINT NOT NULL,
    adr_clean                      NUMERIC(8,2),         -- NULL = data error
    gross_value                    NUMERIC(10,2) NOT NULL,
    is_duplicate                   SMALLINT NOT NULL,
    exclude_from_model             BOOLEAN NOT NULL
);
CREATE INDEX ix_bookings_arrival ON bookings (arrival_date);
CREATE INDEX ix_bookings_segment ON bookings (market_segment);

-- -----------------------------------------------------------------------------
-- CX (synthetic, booking level, 1:1 with bookings)
-- -----------------------------------------------------------------------------
CREATE TABLE cx_contact (
    booking_id         INTEGER PRIMARY KEY REFERENCES bookings(booking_id) ON DELETE CASCADE,
    contact_attempted  BOOLEAN NOT NULL,
    contact_channel    VARCHAR(5)  CHECK (contact_channel IN ('email','sms','phone')),
    contact_date       DATE,
    customer_response  VARCHAR(16) CHECK (customer_response IN ('confirmed','requested_change','no_response')),
    is_synthetic       BOOLEAN NOT NULL DEFAULT TRUE,
    CHECK (contact_attempted OR (contact_channel IS NULL AND contact_date IS NULL AND customer_response IS NULL))
);

CREATE TABLE cx_feedback (
    booking_id                    INTEGER PRIMARY KEY REFERENCES bookings(booking_id) ON DELETE CASCADE,
    satisfaction_score            SMALLINT CHECK (satisfaction_score BETWEEN 1 AND 5),   -- Check-Out only
    cancellation_reason_category  VARCHAR(32),                                          -- Canceled only
    cancellation_reason_text      TEXT,
    is_synthetic                  BOOLEAN NOT NULL DEFAULT TRUE
);

-- -----------------------------------------------------------------------------
-- FINANCE (synthetic, booking level, 1:1 with bookings)
-- -----------------------------------------------------------------------------
CREATE TABLE fin_booking (
    booking_id                  INTEGER PRIMARY KEY REFERENCES bookings(booking_id) ON DELETE CASCADE,
    room_cost_per_night         NUMERIC(8,2) NOT NULL CHECK (room_cost_per_night >= 0),
    channel_commission_rate     NUMERIC(4,3) NOT NULL CHECK (channel_commission_rate BETWEEN 0 AND 1),
    cancellation_handling_cost  NUMERIC(8,2),          -- is_canceled = 1 only
    rebooking_revenue           NUMERIC(10,2),         -- Canceled only; 0 if not resold (GROSS resale revenue)
    rebooked_room_nights        SMALLINT CHECK (rebooked_room_nights >= 0),   -- Canceled only; resold nights of this booking
    is_synthetic                BOOLEAN NOT NULL DEFAULT TRUE
);

-- -----------------------------------------------------------------------------
-- OPERATIONS (date x room type)
-- -----------------------------------------------------------------------------
CREATE TABLE ops_inventory_daily (
    stay_date                  DATE NOT NULL,
    room_type                  VARCHAR(2) NOT NULL REFERENCES dim_room_type(room_type),
    available_rooms            SMALLINT NOT NULL,      -- synthetic/derived capacity
    rooms_sold                 SMALLINT NOT NULL,      -- derived from real stays
    rooms_cancelled            SMALLINT NOT NULL,      -- derived from real cancellations
    resold_after_cancellation  SMALLINT NOT NULL,      -- synthetic
    occupancy_rate             NUMERIC(5,4),
    is_partial_period          BOOLEAN NOT NULL,
    is_synthetic               BOOLEAN NOT NULL DEFAULT TRUE,   -- row mixes derived + synthetic fields
    PRIMARY KEY (stay_date, room_type),
    CHECK (rooms_sold <= available_rooms),
    CHECK (resold_after_cancellation <= rooms_sold),
    CHECK (resold_after_cancellation <= rooms_cancelled)
);

-- -----------------------------------------------------------------------------
-- SYSTEM (written by later stages: model, rule engine, n8n). Empty at this stage.
-- -----------------------------------------------------------------------------
CREATE TABLE risk_scores (
    booking_id                INTEGER NOT NULL REFERENCES bookings(booking_id),
    scored_at                 TIMESTAMP NOT NULL,
    model_version             TEXT NOT NULL,
    p_cancel                  NUMERIC(5,4) NOT NULL CHECK (p_cancel BETWEEN 0 AND 1),
    risk_tier                 VARCHAR(6) CHECK (risk_tier IN ('low','medium','high')),
    expected_revenue_at_risk  NUMERIC(10,2),
    days_to_arrival           INTEGER,
    PRIMARY KEY (booking_id, scored_at)
);

-- Approval is RECORDED here (needs_approval, approval_status, approved_by, approved_at).
-- Declarative rules below: an action that needs approval can never be 'auto', and every approval /
-- rejection stores who decided and when. The guard that refuses to EXECUTE pending/rejected actions
-- (trigger on interventions + service check) is a G2 requirement: docs/decision_table_draft.md, section 6.
CREATE TABLE action_plan (
    action_id         BIGSERIAL PRIMARY KEY,
    booking_id        INTEGER REFERENCES bookings(booking_id),
    stay_date         DATE,                        -- for capacity / price actions at date x room type
    room_type         VARCHAR(2) REFERENCES dim_room_type(room_type),
    action_type       VARCHAR(24) NOT NULL CHECK (action_type IN
                      ('confirm_email','reminder','phone_call','offer_change','request_deposit',
                       'price_adjustment','capacity_allocation','resale','overbooking')),
    rule_id           TEXT NOT NULL,
    needs_approval    BOOLEAN NOT NULL,
    approval_status   VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (approval_status IN ('auto','pending','approved','rejected')),
    approved_by       TEXT,
    approved_at       TIMESTAMP,                   -- source of the approval-turnaround KPI (P3)
    created_at        TIMESTAMP NOT NULL DEFAULT now(),
    FOREIGN KEY (stay_date, room_type) REFERENCES ops_inventory_daily(stay_date, room_type),
    CHECK (booking_id IS NOT NULL OR (stay_date IS NOT NULL AND room_type IS NOT NULL)),
    CHECK (NOT needs_approval OR approval_status <> 'auto'),
    CHECK ((approval_status IN ('approved','rejected') AND approved_by IS NOT NULL AND approved_at IS NOT NULL)
        OR (approval_status IN ('auto','pending')      AND approved_by IS NULL     AND approved_at IS NULL)),
    CHECK (approved_at IS NULL OR approved_at >= created_at)
);

-- Randomised pilot assignment: the basis of every causal KPI (uplift, incremental net benefit, ROI).
-- Outcomes are compared by ARM (intention to treat), whether or not a contact actually took place.
CREATE TABLE pilot_assignment (
    pilot_id          TEXT NOT NULL,
    booking_id        INTEGER NOT NULL REFERENCES bookings(booking_id),
    arm               VARCHAR(9) NOT NULL CHECK (arm IN ('treatment','holdout')),
    stratum           TEXT,                        -- e.g. risk tier / population used for stratified randomisation
    assigned_at       TIMESTAMP NOT NULL,
    PRIMARY KEY (pilot_id, booking_id)
);

CREATE TABLE interventions (
    intervention_id   BIGSERIAL PRIMARY KEY,
    action_id         BIGINT NOT NULL REFERENCES action_plan(action_id),
    channel           VARCHAR(16),                 -- email | sms | phone | pms | task
    executed_at       TIMESTAMP NOT NULL,
    executed_by       VARCHAR(8) NOT NULL CHECK (executed_by IN ('n8n','staff')),
    outcome           TEXT,
    cost              NUMERIC(8,2)
);

-- -----------------------------------------------------------------------------
-- VIEWS
-- -----------------------------------------------------------------------------
-- One analytical row per booking: real + synthetic, synthetic columns prefixed syn_
-- (same names as data/processed/bookings_prepared.csv)
CREATE VIEW v_booking_360 AS
SELECT b.*,
       c.contact_attempted        AS syn_contact_attempted,
       c.contact_channel          AS syn_contact_channel,
       c.contact_date             AS syn_contact_date,
       c.customer_response        AS syn_customer_response,
       f.satisfaction_score       AS syn_satisfaction_score,
       f.cancellation_reason_category AS syn_cancellation_reason_category,
       f.cancellation_reason_text AS syn_cancellation_reason_text,
       fb.room_cost_per_night     AS syn_room_cost_per_night,
       fb.channel_commission_rate AS syn_channel_commission_rate,
       fb.cancellation_handling_cost AS syn_cancellation_handling_cost,
       fb.rebooking_revenue       AS syn_rebooking_revenue,
       fb.rebooked_room_nights    AS syn_rebooked_room_nights,
       ROUND(b.gross_value * (1 - fb.channel_commission_rate)
         - fb.room_cost_per_night * b.total_nights, 2) AS syn_net_booking_value   -- depends on simulated inputs
FROM bookings b
LEFT JOIN cx_contact  c  USING (booking_id)
LEFT JOIN cx_feedback f  USING (booking_id)
LEFT JOIN fin_booking fb USING (booking_id);

-- Daily hotel-level KPIs
CREATE VIEW v_ops_daily_hotel AS
SELECT stay_date,
       SUM(available_rooms)            AS available_rooms,
       SUM(rooms_sold)                 AS rooms_sold,
       SUM(rooms_cancelled)            AS rooms_cancelled,
       SUM(resold_after_cancellation)  AS resold_after_cancellation,
       ROUND(SUM(rooms_sold)::NUMERIC / NULLIF(SUM(available_rooms), 0), 4) AS occupancy_rate
FROM ops_inventory_daily
GROUP BY stay_date;
