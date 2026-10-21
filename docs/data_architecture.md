# Data architecture — City Hotel Lisbon cancellation DSS

PostgreSQL schema `hotel` (full DDL with types, keys and CHECK constraints: `sql/schema.sql`; load script: `sql/load.sql`).
Field meanings: `docs/data_dictionary.csv`. Model-feature meaning and availability time: `docs/feature_availability.csv`.

**Layers.** REAL = hotel PMS extract (Antonio et al., 2019) · DERIVED = computed only from real data · SYNTHETIC = simulated from `config/assumptions.yaml` (`is_synthetic = TRUE`) · SYSTEM = written later by the model, rule engine and n8n.

## 1. Entity-relationship diagram

```mermaid
erDiagram
    dim_room_type ||--o{ bookings : "reserved / assigned room type"
    dim_room_type ||--o{ ops_inventory_daily : "room_type"
    bookings ||--|| cx_contact : "booking_id (1:1)"
    bookings ||--|| cx_feedback : "booking_id (1:1)"
    bookings ||--|| fin_booking : "booking_id (1:1)"
    bookings }o--o{ ops_inventory_daily : "stay nights x room type (derived join)"
    bookings ||--o{ risk_scores : "booking_id"
    bookings ||--o{ action_plan : "booking-level actions"
    ops_inventory_daily ||--o{ action_plan : "date x room-type actions"
    action_plan ||--o{ interventions : "action_id"
    bookings ||--o{ pilot_assignment : "booking_id (randomised arm)"

    dim_room_type {
        varchar room_type PK "REAL: PMS room code"
        smallint capacity_rooms "DERIVED: peak occupied rooms"
        boolean is_sellable
    }
    bookings {
        integer booking_id PK "DERIVED: 1..79,330"
        smallint is_canceled "REAL: target"
        varchar reservation_status "REAL: outcome, ops-only"
        date booking_date "DERIVED"
        date arrival_date "DERIVED"
        integer lead_time "REAL"
        varchar market_segment "REAL"
        varchar deposit_type "REAL"
        varchar reserved_room_type FK "REAL"
        varchar assigned_room_type FK "REAL: ops-only"
        numeric adr_clean "DERIVED"
        numeric gross_value "DERIVED"
        smallint dup_group_size "DERIVED"
        boolean exclude_from_model "DERIVED: DQ flag"
    }
    cx_contact {
        integer booking_id PK, FK
        boolean contact_attempted "SYNTHETIC"
        varchar contact_channel "SYNTHETIC"
        date contact_date "SYNTHETIC"
        varchar customer_response "SYNTHETIC: outcome-conditioned"
    }
    cx_feedback {
        integer booking_id PK, FK
        smallint satisfaction_score "SYNTHETIC: Check-Out only"
        varchar cancellation_reason_category "SYNTHETIC: Canceled only"
        text cancellation_reason_text "SYNTHETIC: Canceled only"
    }
    fin_booking {
        integer booking_id PK, FK
        numeric room_cost_per_night "SYNTHETIC"
        numeric channel_commission_rate "SYNTHETIC"
        numeric cancellation_handling_cost "SYNTHETIC: is_canceled only"
        numeric rebooking_revenue "SYNTHETIC: Canceled only, gross"
        smallint rebooked_room_nights "SYNTHETIC: Canceled only"
    }
    ops_inventory_daily {
        date stay_date PK
        varchar room_type PK, FK
        smallint available_rooms "DERIVED"
        smallint rooms_sold "DERIVED"
        smallint rooms_cancelled "DERIVED"
        smallint resold_after_cancellation "SYNTHETIC"
        numeric occupancy_rate "DERIVED"
    }
    risk_scores {
        integer booking_id PK, FK
        timestamp scored_at PK
        text model_version "SYSTEM"
        numeric p_cancel "SYSTEM"
        varchar risk_tier "SYSTEM"
        numeric expected_revenue_at_risk "SYSTEM"
    }
    action_plan {
        bigint action_id PK
        integer booking_id FK "booking-level actions"
        date stay_date FK "date x room-type actions"
        varchar room_type FK
        varchar action_type "SYSTEM"
        boolean needs_approval "SYSTEM"
        varchar approval_status "SYSTEM"
        text approved_by "SYSTEM"
        timestamp approved_at "SYSTEM"
        timestamp created_at "SYSTEM"
    }
    pilot_assignment {
        text pilot_id PK
        integer booking_id PK, FK
        varchar arm "SYSTEM: treatment / holdout"
        text stratum "SYSTEM"
        timestamp assigned_at "SYSTEM"
    }
    interventions {
        bigint intervention_id PK
        bigint action_id FK
        varchar channel "SYSTEM"
        timestamp executed_at "SYSTEM"
        varchar executed_by "SYSTEM: n8n / staff"
        numeric cost "SYSTEM"
    }
```

## 2. Tables, grain and keys

| Table | Layer | Grain | Primary key | Rows (this build) |
|---|---|---|---|---|
| `dim_room_type` | DERIVED | room type | `room_type` | 9 |
| `bookings` | REAL (+ derived columns) | booking | `booking_id` | 79,330 |
| `cx_contact` | SYNTHETIC | booking (1:1) | `booking_id` | 79,330 |
| `cx_feedback` | SYNTHETIC | booking (1:1) | `booking_id` | 79,330 |
| `fin_booking` | SYNTHETIC | booking (1:1) | `booking_id` | 79,330 |
| `ops_inventory_daily` | DERIVED + SYNTHETIC | night × room type | `(stay_date, room_type)` | 7,227 |
| `risk_scores` · `action_plan` · `interventions` · `pilot_assignment` | SYSTEM | score / action / execution / pilot arm | surrogate or composite keys | 0 (filled in F/G) |

**Join paths.** Booking-level tables join 1:1 on `booking_id`. A booking joins to `ops_inventory_daily` through its stay nights (`stay_date ∈ [arrival_date, arrival_date + total_nights)`), using `assigned_room_type` for rooms sold and `reserved_room_type` for cancellations. Views: `v_booking_360` (one analytical row per booking, synthetic columns prefixed `syn_`) and `v_ops_daily_hotel` (hotel-level daily KPIs).

## 3. Data flow

```mermaid
flowchart LR
    A[hotel_bookings.csv<br/>119,390 rows, 2 hotels] -->|NB01: filter City Hotel,<br/>assign booking_id, audit, clean| B[bookings_city_clean.csv<br/>79,330 rows + DQ flags]
    B -->|NB02: assumptions.yaml| C[data/synthetic/*.csv<br/>CX · Finance · Ops · dim_room_type]
    B --> D
    C -->|NB03 D1: validate, standardise,<br/>join 1:1 on booking_id| D[bookings_prepared.csv<br/>real + syn_* columns]
    D -->|NB03 D2: EDA, populations,<br/>time-aware features, split| E[modeling_dataset.csv<br/>78,892 rows]
    E --> F[F: cancellation model]
    B --> G[(PostgreSQL schema hotel)]
    C --> G
    F -.->|risk_scores| G
    F --> G1[G1: notebook 05 policy + dated schedule<br/>artifacts/business_case]
    G1 --> H[G2: Streamlit dashboard + SQLite demo store<br/>+ n8n demo workflow, implemented]
    G -.->|planned shared store| H
```

## 4. Integration and privacy

Covered in notebook 02, Section 8: source systems, keys, grains, snapshot timing, real-time inventory, data quality, synthetic-vs-real labelling, the approval loop (recorded in `action_plan` with who/when; the execution guard is implemented in G2: service, SQLite triggers and `sql/g2_approval_guard_postgres.sql`), the randomised pilot assignment used for causal KPIs, and GDPR (lawful basis, minimisation, retention, Art. 22 human review, transparency, fairness, security).
