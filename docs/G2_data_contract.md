# G2 data contract (F v3.1 + G1 policy)

The dashboard runs on **one F run and the G1 policy built on it**: `dashboard/data_contract.py` (`load_outputs`) loads and checks F first, then the G1 handover against it (`dashboard/g1_contract.py`). The G2 state DB is bound to `<F run>+g1-<sha256[:8] of g1_policy.json>`.

## Source 1: one F run
- **Run:** `f-v3.1-2026-10-19_20261020-101915`, model XGBoost (no calibration), fit on individual arrivals ≤ 2016-12-31. Checked by `run_id` in every file.
- **Read by:** `dashboard/data_contract.py` (`load_f_outputs`), directly from the repo (no copy). The loader's cache key is the modification time and size of these files, so when notebook 04 rewrites them the next interaction reloads and re-validates them (sidebar **Reload F outputs** forces it):
  - `artifacts/modeling/action_queue_2017.csv` — the **operational weekly queue**: each 2017 booking scored at the end of its booking week (Sunday), only if still eligible then (created, not cancelled, not arrived, not complimentary). 13,156 rows = 12,102 individual (model) + 1,054 group / block (rule).
  - `artifacts/modeling/action_schedule_2017.csv` — 22,164 dated actions (`decision_at ≤ scheduled_at`).
  - `artifacts/modeling/output_consistency.csv` — F's cross-file checks; the build stops if one fails.
  - `data/processed/bookings_prepared.csv` — `deposit_type`, `reserved_room_type`, `syn_net_booking_value` (join on `booking_id`, one-to-one).
- **Not used by the dashboard:** `test_predictions_2017.csv` (retrospective arrival cohort) and notebook 04's `test_metrics.csv` / `deciles_test.csv`: they cover arrivals after today. **Outcomes** (`is_canceled` from `bookings_prepared.csv`) are read only for bookings that arrived before today: Overview (look back), Performance and **Model monitoring**, which recomputes ROC-AUC, PR-AUC, Brier, top-20% precision / recall / lift and deciles on those bookings with the score of their weekly batch (`dashboard/model_view.py`). The prepared history (`dashboard/seed_history.py`) also reads them, only to derive guest answers that never contradict the outcome. None of this reaches the queue, schedule or recommendations. Tests: `tests/test_g2_model_view.py`, `tests/test_g2_seed_history.py`.
- **Checks at load:** one `run_id` across manifest, queue and schedule; every row of `output_consistency.csv` passed; one-to-one join; no post-outcome field; tiers and rules consistent. Any failure stops the app with the reason. Tests: `tests/test_g2_data.py`.

## Source 2: the G1 policy (notebook 05, schema 2)
- `artifacts/business_case/g1_policy.json` — shares (`high_share` 0.15 by `p_x_gross`, `contact_share` 0.60), call capacity (20 / day), call window (start day 0, 7 days, ≥ 8 days before arrival), `stage_f_run`, `overbooking.enabled = false`.
- `artifacts/business_case/action_queue_g1_2017.csv` — one row per F individual booking: `tier_g1`, `rule_g1`, `schedule_status` (`scheduled`, `manual_capacity_review`, `buffer_only`), `first_contact`, `planned_messages`.
- `artifacts/business_case/action_schedule_g1_2017.csv` — dated actions; I3 calls placed by G1's capacity-limited scheduler.
- **Not used:** `retrospective_evaluation_2017.csv` (outcome-dependent; evaluation only).

**Checks at load (`g1_contract.load_g1`)** — none trusts G1's own result:
1. `stage_f_run` equals the F manifest run; schema 2; G3 overbooking disabled.
2. Same booking IDs, `p_cancel`, decision date, arrival and days to arrival as F's individual queue; no outcome-dependent column (`messages`, `expected_net_eur`, ...).
3. Every `tier_g1` re-derived from F's scores with the frozen policy (F's ranking and booking-id hash tie-break, floor of the shares); every `rule_g1` from tier and days to arrival; `buffer_only` = I5; manual review only for I3.
4. Schedule: I1 / I2 exactly at the policy milestones (I1 −7, I2 −30 / −14, immediate if passed, max 2); one call per scheduled I3, within the window, ≥ 8 days before arrival, ≤ capacity per day; none for manual review or I5.

**What G2 shows:** individual rows take `risk_tier = tier_g1`, `rule = rule_g1`, `schedule_status`; F's defaults stay as `f_risk_tier` / `f_rule`. Group rows (rule G2) are F's. The schedule is G1's. At execution the store also refuses more phone calls on a business day than the policy capacity.

**Closing without contact (state schema 7, kept in 8):** `state_store.close_action` logs an append-only `action_closures` row (who, why, business date) for an open auto / approved action that can no longer run as planned, typically a G1 call past its window. A closed action never executes (service check + trigger), is not open for the "one open action" and "call goes first" rules, and is not a call attempt; executed or pending actions cannot be closed (reject a pending one). Without it an expired I3 call left its booking with no call, no fallback reminder and no way to close the call.

**Execution policy (state schema 6, kept in 7):** the store persists `call_schedule` and the disabled overbooking flag for its lifetime. A new G1-bound store requires this policy; reopening may reuse the persisted policy but cannot change it. All creation paths use the F + G1 policy key. Actions keep `known_from`, the original F score date: a manual call cannot restart the policy window by using a later decision date. Call creation and execution require offsets `start_day` through `window_days - 1` from that score date and at least `min_days_before_arrival` days remaining. Expired calls remain unexecuted with a manual-review error. G3 is blocked at creation and execution even after approval. I4 offers additionally store an immutable `basis_event_id` referencing the exact change request they answer. The default filename ends in `_v6.sqlite`, preserving older logs; explicit older DB paths are refused. Schema-5 logs are not automatically migrated because their offers lack this event binding.

## Queue fields (as loaded by the dashboard)

| Field | Example | Meaning / origin | Notes |
|---|---|---|---|
| `booking_id` | `22288` | City Hotel booking id (C1) | Unique in the queue; join key |
| `track` | `individual model` / `group rule` | Population (notebook 03 §2.0) | Group rows are not scored by the model |
| `score_time` | `2017-01-01` | Decision time = end of the booking week | All actions are dated from here |
| `arrival_date`, `days_to_arrival` | `2017-01-10`, `9` | At decision time | Always > 0 |
| `p_cancel` | `0.367` | F probability (group: rule rate) | Not calibrated (F chose `none`) |
| `risk_tier` | `low` (`high` / `medium` / `low`) | **G1 capacity shares per weekly batch:** top 15% high by p × gross, next by p up to 60% total contact; slots = floor(share × batch) | F's original tier stays in `f_risk_tier`; empty for group rows |
| `rule` | `I1` (`I1`, `I2`, `I3`, `I5`, `G2`) | Decision table rule | I5 = high tier with ≤ 7 days: no guest action |
| `owner`, `approval`, `needs_approval` | `n8n, monitored by Reservations`, `auto`, `False` | Decision table | Group actions always need approval |
| `scheduled_actions` | `confirm_email 2017-01-01; reminder 2017-01-03` | Text summary of the G1 schedule | Details in `action_schedule_g1_2017.csv` |
| `expected_revenue_at_risk` | `29.18` | **F:** `p_cancel × gross_value` | EUR, historical value |
| `syn_net_value_at_risk` | `16.80` | **Decision table §1:** `p_cancel × syn_net_booking_value` | **Synthetic** costs; see open point 1 |
| `consent_status`, `open_action_check` | `unknown` | Not in the data | Checked by staff / n8n at execution, never assumed passed |
| `created_by_decision` … `not_complimentary` | `True` | F eligibility flags at decision time | All True by construction |

**Removed (post-outcome):** `outcome_label_known_later`, `is_canceled`, `reservation_status(_date)`. `validate_queue` refuses a table that contains them.

**Added by G2:** `room_type` (= `reserved_room_type`), `deposit_type`, `syn_net_booking_value` from `bookings_prepared.csv`, and `syn_net_value_at_risk`.

## Operational schedule fields (`action_schedule_g1_2017.csv`)
`booking_id, decision_at, scheduled_at, rule, action_type, schedule_reason, message_no`; the loader adds `run_id = <F run>+<G1 policy id>`. I1: confirmation at decision time + reminder 7 days before arrival if still ahead. I2: reminders at −30 / −14 (one immediately if a milestone has passed), max 2. I3: one call task within offsets 0–6 of the decision date, at least 8 days before arrival and at most 20 tasks per day across bookings. Manual-capacity-review bookings, I5 and G2 have no schedule row.

## Action event sent to n8n (demo)
```json
{
  "mode": "demo_no_guest_contact", "as_of": "2017-01-01",
  "action_id": 12, "booking_id": 22288, "stay_date": null, "room_type": null,
  "action_type": "confirm_email", "rule_id": "I1", "scheduled_at": "2017-01-01",
  "decision_at": "2017-01-01", "known_from": "2017-01-01", "arrival_date": "2017-01-08",
  "source_run_id": "<F run>+<G1 policy id>",
  "execution_policy": {
    "overbooking": {"enabled": false},
    "call_schedule": {"start_day": 0, "window_days": 7, "min_days_before_arrival": 8}
  },
  "approval_status": "auto", "needs_approval": false, "outcome": "no_response",
  "already_executed": false, "guest_response": null, "guest_response_action_type": null,
  "offer_basis_event_id": null, "latest_response_event_id": null,
  "idempotency_key": "<F run>+<G1 policy id>:12",
  "precheck": {"consent_confirmed": true, "no_open_conflict": true, "booking_active": true}
}
```
The dashboard sends it only after every local execution check has passed (approval, not executed yet, due, before arrival, no guest response, no same-day call / message clash, staff checks); a blocked action never reaches n8n. Checks, the webhook call and the log are one transaction under the store's write lock, so while n8n is called no other session can execute or close the action or take the last call slot; `idempotency_key` (store run : action id) lets a production workflow drop a repeated request. `already_executed` and `guest_response` come from the local store so n8n's guard can refuse too. No guest name, address or contact. The status in the payload is self-reported: a production backend must read it from the database and authenticate the caller.

For calls, n8n also requires the stored call policy and valid original score / decision / arrival dates and rechecks the actual replay date against both call deadlines. Missing policy or dates are refused. G3 is disabled in this workflow version regardless of the submitted approval or policy flag. This remains a local demo: production must retrieve authoritative policy and action dates by action ID rather than trusting webhook input.

For I4, both the local execution check and n8n require the latest response to be `requested_change` and `guest_response_action_type` to be `phone_call`. `offer_basis_event_id` comes from the offer's immutable `basis_event_id`; `latest_response_event_id` is the latest response event known on the replay date. They must identify the same event. A newer change request requires a new offer and approval, even if it arrives on the same day or after a confirmation. The old unexecuted offer no longer blocks a replacement; an executed offer still counts toward the booking's limit. For non-offer actions `offer_basis_event_id` is null; `latest_response_event_id` is null when no response is known. Same-day responses are ordered by the shared persisted `response_events.event_id`, not by response source or wall-clock seconds. The loader checks the actual I1/I2 reminder dates and required reminders against the dated policy above, in addition to action type, counts and run identity.

## Integration acceptance criteria
1. One `run_id` across queue, schedule and manifest; F's `output_consistency.csv` all passed. *(tested)*
2. `booking_id` unique in the queue; joins one-to-one with `bookings_prepared.csv`. *(tested)*
3. F's source tiers pass the F ceilings floor(5% × n) and floor(20% × n); G2's operational G1 tiers are re-derived with floor(15% × n) high and floor(60% × n) total contact. *(tested)*
4. No post-outcome field reaches the dashboard data. *(tested)*
5. The database refuses an intervention for a `pending` / `rejected` action, refuses `auto` on a sensitive action, and refuses re-pointing or deleting a logged intervention. *(tested on SQLite and, with `G2_TEST_PG_DSN`, on the PostgreSQL store of `dashboard/pg_backend.py`; the `hotel` migration is checked by `sql/g2_verify_guard.sql`, 19/19 on PostgreSQL 16)*
6. Synthetic financial data stays labelled; no measured uplift or ROI is claimed.
7. Two sessions at once cannot exceed the daily call limit, cannot both close and execute one action, and cannot send one action to n8n twice; direct SQL cannot insert an overbooking action or a call over the limit. *(tested on SQLite and PostgreSQL 16: `tests/test_g2_concurrency.py`)*

## Checks the loader re-derives (does not trust stored results)
- Operational G1 schedule: I1 = one confirmation at the decision time + at most one reminder; I2 = one or two reminders; I3 = one call within the G1 window and daily capacity, or no call for manual capacity review; I5 / G2 = none. No duplicate (booking, type, date); every action before arrival; `message_no` 1..n. The original F schedule is validated separately before applying G1.
- Cancellation buffer for decisions is computed **as of today** (`score_time ≤ today < arrival_date`); the dashboard no longer shows a whole-year total.

## Open points for F / G1
1. **Value at risk:** F exports `p × gross_value`; the decision table defines `p × syn_net_booking_value`. Both are shown under different names until the group picks one.
2. **`offer_change` approval:** always required here (the table asks it only when price changes; the schema has no price-change flag).
3. G1's operational assumptions set **call capacity N = 20/day**, a 7-day call window and the group release-request milestone at 30 days. These need validation in the pilot. I6 remains off by default and its "high value" has no threshold: the demo requires the written-policy clause instead of inventing one. G3 remains disabled pending a tested operational policy.
