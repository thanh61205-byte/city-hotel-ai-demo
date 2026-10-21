# Draft decision table — risk × value × time → action

Status: **G1 simulated policy and dated handover**. F defaults remain a reference; the generated G1 policy below governs the new handover. G2 runs on it; the pilot remains required.

## 1. Inputs at decision time

| Input | Definition | Source |
|---|---|---|
| `p_cancel` | Calibrated cancellation probability | F model (`risk_scores`) |
| `value_at_risk` | `p_cancel` × `gross_value` for G1 call ranking; contribution basis used separately for simulated benefits | F model + finance table (**simulated** inputs) |
| `days_to_arrival` | `arrival_date` − decision date | PMS |
| `population` | `individual` or `group_block` (inferred proxy, notebook 03 §2.0) | notebook 03 rule |
| Capacity | Booking-level call tasks per day; average attempts included in task cost | Reservations lead (to collect) |

## 2. Eligibility (applied before any rule)

A booking is considered only if **all** hold:
1. Status is active: not cancelled, not checked in, arrival date after the decision date.
2. It passes the data-quality rules (`exclude_from_model == False`); otherwise it goes to manual review.
3. It is not complimentary (`adr_clean == 0` in segment Complementary).
4. The guest has not opted out of non-essential contact, and no other open action of the same type exists for it.

**Implemented in F (notebook 04 §7.1, `src/f_modeling.eligibility`).** Checked at the decision time (end of the booking week): created, not cancelled (status date ≤ decision time), arrival still ahead, not complimentary (Complementary with ADR 0). Condition 2 holds by construction (`modeling_dataset.csv` keeps only rows that pass the data-quality rules). Condition 4 is **not in the extract**: `consent_status` and `open_action_check` are exported as `unknown`, never as passed; the executor (n8n, G2) must check them, and re-check status and guest response, at execution time.

## 3. Individual track (guest follow-up)

`t_low`/`t_high` denote rank-based tiers, not fixed probabilities. The paragraph below documents F defaults; current G1 settings are in the generated policy section. **Set in F2 (notebook 04):** the bands are defined by capacity, not by a fixed probability: `p_cancel ≥ t_high` ⇔ `risk_tier = high` (top 5% of each weekly batch of new bookings), `t_low ≤ p_cancel < t_high` ⇔ `risk_tier = medium` (next 15%), below ⇔ `low`. The batch is ranked at the **decision time = end of the booking week**, among the bookings of that week still eligible then (§2); slots per batch = floor(share × batch size), so the shares are hard ceilings; ties are broken by a fixed hash of `booking_id`. In the simulated 2017 queue (12,102 eligible individual bookings, 24.0% cancelled) high cancelled 46.2%, medium 39.4%, low 19.8%. The 5% / 20% shares stay placeholders for the real call capacity `N`; a frozen probability cut-off is avoided because cancellation rates drift with season and year.

| Rule | Condition | Action (`action_type`) | Owner | Approval | Limit / stop condition |
|---|---|---|---|---|---|
| I1 | `p_cancel < t_low` | Standard confirmation at the decision time + reminder 7 days before arrival, only if that date is still after the decision time (`confirm_email`, `reminder`) | n8n, monitored by Reservations | auto | Stop when the guest confirms or cancels |
| I2 | `t_low ≤ p_cancel < t_high` and `days_to_arrival ≥ 8` | Personalised reminder by email/SMS at 30 and 14 days before arrival; a booking that enters the band after a milestone has passed (e.g. at 8–13 days) gets one reminder **immediately**, then follows the remaining schedule (`reminder`) | n8n, monitored by Reservations | auto | Max 2 messages; stop on any response |
| I3 | `p_cancel ≥ t_high` and `days_to_arrival ≥ 8` | Phone call from the follow-up queue, ranked by `value_at_risk` (`phone_call`) | Reservations / front office | none (internal task) | At most `N` calls per day (capacity); max 2 attempts per booking; stop on response |
| I4 | I3 contact and the guest asks to change | Offer a date / room change (`offer_change`) | Reservations | **Revenue manager** if price or discount changes | One offer per booking |
| I5 | `p_cancel ≥ t_high` and `days_to_arrival ≤ 7` | No call (too late to change much); count the booking in the date × room-type cancellation buffer (feeds G2/G3) | Revenue manager | — | — |
| I6 | `p_cancel ≥ t_high`, No Deposit, high value, and policy allows | Ask for a deposit / guarantee (`request_deposit`) | Reservations | **Revenue manager** (significant effect on the guest, GDPR Art. 22 human review) | Off by default; only under a written policy |

*Approval column, as implemented in G2:* `auto` means no approval per action. Since store schema 9 a reviewer still approves the booking's routine plan (I1–I3) before it is scheduled, in a batch or one by one (§6.5).

**Dated schedule (exported by F as `action_schedule_2017.csv`).** Every action has a `decision_at` (end of the booking week) and a `scheduled_at`, and `scheduled_at` is never before `decision_at`. Milestones that have already passed are not scheduled: an I2 booking with 15–29 days left gets one reminder immediately and one at −14; with 8–14 days left, one reminder immediately only; with 30+ days left, reminders at −30 and −14 (max 2). The I1 reminder at −7 is dropped when −7 is not after the decision time. F's default schedules I3 at the decision time. G1 replaces that date with its capacity-aware call-task schedule; I5 creates no guest action. Use the G1 queue and schedule together.

**Priority when several rules match the same booking:** For the high tier, I5 (≤ 7 days) overrides I3; medium/low bookings at ≤ 7 days use I1. Otherwise the strongest applicable action runs and weaker ones are suppressed: I6 (if policy allows and approved) > I3 > I2 > I1. I4 only follows an I3 contact. A booking never receives an automated reminder (I1/I2) on the same day as a phone call (I3).

## 4. Group / block track (capacity management)

| Rule | Condition | Action | Owner | Approval | Limit / stop condition |
|---|---|---|---|---|---|
| G1 | Group-rule p_cancel >= 0.5 and > 30 days to arrival at decision; confirm/release ask at arrival minus 30 days | Ask the agent / tour operator to confirm or release rooms | Distribution / sales | Revenue manager for the release decision | Contract terms |
| G2 | Forecast released rooms for a date × room type above the buffer | Reallocate capacity and put rooms back on sale (`capacity_allocation`, `resale`) | Revenue manager | **Required** | Never sell more than the rooms actually released |
| G3 | DISABLED: exploratory analysis only | Controlled overbooking (`overbooking`) | Revenue manager | **Required (GM for limits)** | Disabled in G1; staff approval alone does not enable this rule |
| G4 | New group contract with a history of large releases | Staged deposits / earlier release dates | Distribution / sales + Finance | **Required** | Contract negotiation |
| G5 | Price change for a date × room type driven by the buffer | `price_adjustment` | Revenue manager | **Required** | Within the hotel's rate rules |

## 5. Programme-level stop conditions

- Pause automated messages if complaints or opt-outs exceed a level agreed with CX.
- The pilot runs with a **randomised holdout**: eligible bookings are assigned to `treatment` or `holdout` before any action and recorded in `pilot_assignment` (stratified by risk tier). Effects are measured **by assigned arm** (intention to treat), never by comparing only the guests actually contacted (KPIs C1, B6). If no positive uplift is seen after the agreed pilot length, stop or redesign the rules; do not scale.
- Intervention cost per month stays within the budget set by Finance (KPI B7).

## 6. Approval guard (specification for G2)

The schema **records** approvals (`action_plan.needs_approval`, `approval_status`, `approved_by`); it does not yet **enforce** them. G2 must implement and test:

1. `needs_approval = TRUE` for `request_deposit`, `price_adjustment`, `capacity_allocation`, `resale`, `overbooking`, and for `offer_change` with a price change. **G3 `overbooking` is currently disabled by the G1 policy:** G2 refuses it whatever the approval (service, n8n guard, PostgreSQL constraint `g2_g3_overbooking_disabled`). For these actions, `approval_status` can never be `auto`.
2. An `interventions` row may be written **only if** its action has `approval_status IN ('auto', 'approved')`. Enforce this in the n8n/service layer **and** with a database check (trigger on `interventions` insert), so neither layer can be bypassed alone.
3. Rejected or pending actions are never executed; every approval or rejection stores who decided (`approved_by`) and when (`approved_at`). The schema already rejects an `auto` status on an action that needs approval and an approval without `approved_by` / `approved_at` (CHECK constraints on `action_plan`).
4. Tests: inserting an intervention for a `pending` or `rejected` action must fail; an approved action must pass; an `auto` action with `needs_approval = TRUE` must be impossible.
5. **Staff review of routine plans (G2 dashboard, store schema 9).** I1–I3 actions stay `auto` at action level (no second approval per message or call), but a booking's G1 plan (I1 confirmation and reminder, I2 reminders, I3 call) enters `action_plan` only when a reviewer approves it, alone or in a batch: `plan_reviews` keeps one final decision per booking with reviewer, reason, replay date and batch id. Approving imports the plan in the same transaction; a rejection needs a reason, and a database trigger then refuses that booking's G1 plan (manual actions remain possible). A batch only contains routine plans awaiting review that can still be carried out; sensitive actions (I4, I6, date × room-type actions) are always decided one by one under rules 1–3. The import function itself does not require a review: the dashboard's only import paths are the review screens (Risk Queue, Action Review); a direct service or SQL caller is not blocked.
6. **Automation attempts (schema 9).** Every hand-over to n8n is logged in `execution_attempts` (accepted / refused / error). Only an accepted attempt has an `interventions` row; a refused or failed one leaves the action open, shown as *Failed*, and can be retried under the same checks.

## 7. Owners (stakeholders)

| Stakeholder | Owns |
|---|---|
| General manager | Programme approval, overbooking limits, budget |
| Revenue manager | Thresholds, capacity / price / resale / overbooking decisions, approvals, business KPIs B1–B5 and B9 |
| Reservations / front office | Follow-up queue, contacts, process KPIs P1–P2 |
| Customer service / CX | Message content, opt-outs, CX KPIs C1–C2 |
| Finance | Cost and contribution assumptions, KPIs B6–B7 |
| Distribution / sales | Agents and tour operators, block contracts, KPI B8 |
| Data / analytics team | Model, calibration, model KPIs M1–M6, monitoring |
| Guests | Affected party: receive the interventions; transparency in the privacy notice |

<!-- G1_POLICY:START -->
## G1 policy replacing the F default shares

Call tier: top 15% by `p_x_gross`; total call + reminder tier: 60% of each weekly batch. Scores and dates come from the current F run. The 2016 selection uses weekly rolling predictions, with estimator selection on matured 2015 labels and policy outcomes frozen at 2016-12-31. Call tasks start on the decision day, at most 20 booking-level tasks/day within 7 calendar days, and at least 8 days before arrival. Overflow is sent to manual review; task costs include average attempts.

2017 queue: **12,102 bookings**. Capacity-aware S4 net benefit after variable intervention costs: **EUR 30,390/year**; total-programme 3-year ROI: **172.6%**; payback: **6.9 months**. ROI = net after all costs / all variable intervention, one-off and running costs over the same horizon. These are simulated values, not measured savings.

Handover: `artifacts/business_case/action_queue_g1_2017.csv`, `action_schedule_g1_2017.csv`, `g1_policy.json`. Outcome-dependent contacts and net values are isolated in `retrospective_evaluation_2017.csv`. **G3 overbooking is disabled**, including in the machine-readable rules and policy. I6 deposits remain disabled by default. Consent, current status, responses, open actions and live capacity must be checked at execution.

Limitations: snapshot features/population proxy inherited from F; unresolved outcomes are omitted from the 2016 objective, while full queues determine tiers/capacity; historical and final F estimators differ. Confirm policy transfer and guest response in the randomised pilot. The separate overbooking experiment remains exploratory and cannot authorise execution.

Run G1: `python src/run_notebooks.py --stage G1`. See `docs/G1_data_contract.md`. **G2 runs on this policy:** `dashboard/g1_contract.py` re-derives the G1 tiers from F scores, checks the dated schedule (milestones, call window, daily capacity) and binds the G2 state DB to F run + G1 policy; manual-review calls get no automatic action. See `docs/G2_data_contract.md`.
<!-- G1_POLICY:END -->
