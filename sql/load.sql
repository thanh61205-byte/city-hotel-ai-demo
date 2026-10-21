-- Load cleaned real data + synthetic tables into schema `hotel`.
-- Run from the repo root AFTER sql/schema.sql:   psql -d <db> -f sql/load.sql
-- CSVs are produced by notebooks/01_data_audit_eda.ipynb and notebooks/02_data_architecture.ipynb.
SET search_path TO hotel;

\copy dim_room_type (room_type, capacity_rooms, is_sellable, capacity_source) FROM 'data/synthetic/dim_room_type.csv' WITH (FORMAT csv, HEADER true)

\copy bookings FROM 'data/processed/bookings_city_clean.csv' WITH (FORMAT csv, HEADER true)

\copy cx_contact (booking_id, contact_attempted, contact_channel, contact_date, customer_response, is_synthetic) FROM 'data/synthetic/cx_contact.csv' WITH (FORMAT csv, HEADER true)

\copy cx_feedback (booking_id, satisfaction_score, cancellation_reason_category, cancellation_reason_text, is_synthetic) FROM 'data/synthetic/cx_feedback.csv' WITH (FORMAT csv, HEADER true)

\copy fin_booking (booking_id, room_cost_per_night, channel_commission_rate, rebooking_revenue, rebooked_room_nights, cancellation_handling_cost, is_synthetic) FROM 'data/synthetic/fin_booking.csv' WITH (FORMAT csv, HEADER true)

\copy ops_inventory_daily (stay_date, room_type, available_rooms, rooms_sold, rooms_cancelled, resold_after_cancellation, occupancy_rate, is_partial_period, is_synthetic) FROM 'data/synthetic/ops_inventory_daily.csv' WITH (FORMAT csv, HEADER true)


-- Quick row counts
SELECT 'bookings' AS t, COUNT(*) FROM bookings
UNION ALL SELECT 'cx_contact', COUNT(*) FROM cx_contact
UNION ALL SELECT 'cx_feedback', COUNT(*) FROM cx_feedback
UNION ALL SELECT 'fin_booking', COUNT(*) FROM fin_booking
UNION ALL SELECT 'ops_inventory_daily', COUNT(*) FROM ops_inventory_daily
UNION ALL SELECT 'dim_room_type', COUNT(*) FROM dim_room_type;
