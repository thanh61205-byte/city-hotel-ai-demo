# Booking Cancellation Decision Support System — City Hotel, Lisbon

> **Purpose of this file.** This is the single source of truth for the project. Share it with every teammate and every AI assistant before asking for help, so everyone works from the same context. If something here conflicts with a prompt, **this README wins** unless the prompt explicitly says otherwise.

---

## 0. TL;DR

- **Tải về và chạy thử / Run the demo:** [`RUN_DEMO.md`](RUN_DEMO.md) — hướng dẫn cài đặt, Streamlit, n8n, demo 5 phút và xử lý lỗi cho máy mới. Windows: double-click `run_demo.bat`; dashboard only: `dashboard/run_windows.bat`. macOS / Linux: `bash run_demo.sh`. Uses the saved F/G1 outputs; no model training required (Python 3.11+, Node.js 24.x for n8n).
- **Course:** Advanced Business Analytics (FTU, Year 4), final project. The deliverable is a **consulting-style data-driven strategy**, structured by the **PDMT framework** (Problem – Data – Method – Tools), with a partial working implementation.
- **Business:** one company only: **City Hotel ("H2"), Lisbon, Portugal**. Rows where `hotel == "Resort Hotel"` are always excluded.
- **Problem:** the hotel struggles to predict cancellation risk, handle bookings proactively, and optimise revenue and room occupancy together.
- **Solution:** an ML cancellation-risk model with revenue-impact estimates; rule-based decisions on follow-up, price adjustment, capacity allocation and resale; n8n automation for reminders, inventory updates, tasks and staff alerts (key decisions need approval); and a dashboard.
- **Real data:** `hotel_bookings.csv`. **Synthetic data:** 12 extra columns in 3 domains (CX, Finance, Operations). They go in separate tables joined by keys and are always labelled as synthetic.
- **Build for real:** data pipeline, ML model, synthetic data, dashboard. **Prototype only:** n8n workflows and the website.
- **Scope of the model:** a **retrospective prototype**. The public extract holds near-arrival values, not booking-time history (§6.2).
- **Language:** English for code, column names and docs. Currency is EUR. `random_state = 42`.
- **Golden rules:** no leakage (never use post-outcome info such as `reservation_status` or cumulative fields such as `booking_changes` as a feature); synthetic columns are never treated as real; every assumption must be grounded in Lisbon 2015–2017 conditions (§5.2).

---

## 1. Lecturer requirements (Final-Project-Description.pdf)

"Data-Driven Business Strategy Challenge". Act as data-driven business leaders or consultants. Design and justify an analytics-driven initiative for a complex, cross-functional challenge using PDMT.

| PDMT stage | Required deliverables |
|---|---|
| **1. Problem** | Strategic problem statement; high-level PDMT map; expected business value |
| **2. Data** | Data architecture diagram/schema; description of data sources and features; example extract or **simulated sample**; integration challenges; privacy/ethics |
| **3. Method** (partial implementation recommended) | Analytical design (flowchart / pseudo-pipeline); prototype analysis or mock results; partial implementation; **evaluation plan** |
| **4. Tools** | Tool choices and justification (scalability, collaboration, usability for **non-technical managers**); workflow explanation |
| **5. Pitch** | Consulting-style **slide deck** (problem framing, PDMT logic, expected ROI, implementation roadmap); several-page **executive summary** |

---

## 2. Problem (PDMT – P)

**Problem statement.** City Hotel Lisbon has a **~41.7% cancellation rate** (33,102 of 79,330 bookings, no-shows included). The cancelled bookings are worth about **€10.9M of €25.3M gross booked room revenue** (ADR × nights, Jul 2015–Aug 2017). The hotel struggles to predict cancellation risk, handle bookings proactively, and optimise revenue together with room occupancy.

**Objective.** Build a decision support system that optimises revenue and keeps room occupancy optimal through:
1. **Prediction:** the cancellation probability for each booking, and the revenue affected.
2. **Prescription:** rule-based decision analysis that proposes follow-up, price adjustment, capacity allocation (by room type) and resale of cancelled rooms.
3. **Automation:** n8n sends guest reminders, updates room inventory, creates tasks and alerts staff. **Key decisions need staff approval.**
4. **Evaluation:** cancellation rate, occupancy, revenue and intervention cost, so the strategy can be adjusted.

**Stakeholders.** Revenue manager · Front office / reservations · Customer service · Finance · Distribution / sales · General manager · Guests.

**PDMT map (high level).**
```
PROBLEM                       DATA                                 METHOD                              TOOLS
Unpredictable cancellations → Booking dataset (32 cols, real)    → ML cancel probability             → Python (pandas, scikit-learn)
Revenue impact              → Finance columns (synthetic)        → Revenue affected / at risk        → PostgreSQL
Reactive booking handling   → CX columns (synthetic)             → Rules: follow-up, price adjustment → n8n (reminders, tasks, alerts)
Sub-optimal occupancy       → Ops date × room-type data (syn.)   → Rules: capacity allocation, resale → n8n (inventory updates)
                                                                 → Occupancy & revenue scenarios     → Website + dashboard
Unclear cancel causes       → cancellation_reason text (syn.)    → TF-IDF + clustering (retrospective, not a model input) → Python (scikit-learn)
```

---

## 3. Source data

| Item | Value |
|---|---|
| File | `hotel_bookings.csv` (project root). Read-only; never modify it. |
| Origin | Antonio, N., de Almeida, A., & Nunes, L. (2019). *Hotel booking demand datasets.* Data in Brief, 22, 41–49. Extracted from the hotel's PMS. |
| Full file | 119,390 rows × 32 columns (Resort Hotel + City Hotel) |
| **Our scope** | `hotel == "City Hotel"` → **79,330 rows**, `booking_id` 1–79,330 |
| Arrival period | 2015-07-01 → 2017-08-31 |
| Target | `is_canceled` (1 = cancelled **or no-show**) |
| Feature snapshot | Values are as of the day before arrival, where possible |
| Personal data | None (anonymised; `agent` / `company` are IDs, and NaN means "not applicable") |

### 3.1 Key facts (sanity checks)
- 41.7% cancelled (40.6% cancelled + 1.2% no-show); €10.9M of €25.3M gross booked value.
- Non Refund cancels 99.8% (a pattern consistent with block / allotment bookings, not verified; assigned to the `group_block` proxy). Groups 68.9% · Offline TA/TO 42.8% · Online TA 37.4% · Direct 17.3%.
- Cancellations (status Canceled, no-shows excluded) come a median of 58 days before arrival; 12.0% within 7 days (14.5% if no-shows are counted as 0-day notice).
- Data quality: 1,208 rows with `adr = 0`, one `adr = 5400` error, 167 zero-guest and 331 zero-night rows, 25,902 exact-duplicate rows (flagged, not dropped).
- Column meanings, dtypes, null rules, keys and timing: `docs/data_dictionary.csv`. Model-feature meaning and availability time: `docs/feature_availability.csv`.

### 3.2 Cleaned dataset and column groups
`notebooks/01_data_audit_eda.ipynb` → `data/processed/bookings_city_clean.csv` (79,330 rows, nothing dropped; flags `is_duplicate`, `exclude_from_model`). Column groups live in `config/column_groups.json`. **Always load them from there.**

| Group | Columns | Rule |
|---|---|---|
| **Dropped** | `hotel`, raw week number, raw date parts, `company_id`, raw `adr` (→ `adr_clean`), `required_car_parking_spaces` (suspected leakage) | Not in the clean file |
| **Ops only** | `reservation_status`, `reservation_status_date`, `assigned_room_type`, `country`, `arrival_date`, `booking_date`, `gross_value`, flags | Analysis, capacity, resale, CX. **Never a feature.** |
| **NB01 candidates** (`nb01_candidate_columns`) | Non-outcome columns kept in the clean file | Schema / dictionary use only. **Not a training list.** |
| **Model features** | Final lists per population from notebook 03: `individual_model`, `group_model` (`numeric`, `categorical`, `pre_arrival_extra`) | Combined into feature sets via `feature_sets`; forbidden inputs listed in `excluded_features` |

---

## 4. Data architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Booking Dataset — 32 original cols + booking_id               grain: booking │
│ booking, customer, room price, distribution channel, cancellation status     │
└───────────────┬───────────────────────┬──────────────────────┬───────────────┘
                │ booking_id            │ booking_id           │ date
        ┌───────▼────────┐     ┌────────▼────────┐    ┌────────▼──────────┐
        │ CX             │     │ Finance         │    │ Operations        │
        │ contact &      │     │ cost & revenue  │    │ rooms & occupancy │
        │ satisfaction   │     │ grain: booking  │    │ date × room type  │
        └───────┬────────┘     └────────┬────────┘    └────────┬──────────┘
                └───────────────┬───────┴──────────────────────┘
                     ┌──────────▼──────────────────────────────────────┐
                     │ Integrated Analytics Layer                      │
                     │ cleaning → feature engineering → risk analysis  │
                     │ → scenario simulation                           │
                     └──────────┬──────────────────────────────────────┘
                     ┌──────────▼──────────────────────────────────────┐
                     │ Decision Support Dashboard                      │
                     │ Booking risk · Revenue at risk · Customer       │
                     │ follow-up · Room utilization · Scenario compare │
                     └─────────────────────────────────────────────────┘
```

Entity-relationship diagram (rendered on GitHub): `docs/data_architecture.md`.

### 4.1 Keys and grains
- **`booking_id`** = sequential integer **1–79,330**, assigned **after** filtering to City Hotel, in the original file order. It is the primary key of `bookings` and of the booking-level extension tables (`cx_contact`, `cx_feedback`, `fin_booking`) and the foreign key of `risk_scores` / `action_plan`. Never re-assign it after notebook 01.
- **`arrival_date`** is built from the year, month name and day-of-month columns. Booking → hotel-day joins go through the nights each booking covers.
- CX and Finance columns are booking-level (1:1 on `booking_id`). `available_rooms`, `rooms_sold` and `resold_after_cancellation` are at **date × room type** level (primary key `(stay_date, room_type)`), so capacity can be allocated by room type. `rooms_sold` is derived from non-cancelled stay nights × `assigned_room_type`.

### 4.2 Data flow
`hotel_bookings.csv` → **01** audit & clean → `bookings_city_clean.csv` → **02** architecture & synthetic tables → `data/synthetic/*.csv` → **03** integrate (`bookings_prepared.csv`: real + `syn_*` columns, booking level) → deep EDA → feature engineering (`modeling_dataset.csv`, `docs/feature_availability.csv`).

### 4.3 Implementation
- **Schema:** `sql/schema.sql` (PostgreSQL schema `hotel`): `dim_room_type`, `bookings`, `cx_contact`, `cx_feedback`, `fin_booking`, `ops_inventory_daily`, plus system tables `risk_scores`, `action_plan`, `interventions`, `pilot_assignment` (filled later; the last one holds the randomised treatment / holdout arm used for uplift and ROI) and views `v_booking_360`, `v_ops_daily_hotel`. Load with `psql -d <db> -f sql/schema.sql && psql -d <db> -f sql/load.sql`, then check with `psql -d <db> -v ON_ERROR_STOP=1 -f sql/verify_load.sql` (row counts, totals vs the Python pipeline, CHECK / FK rejection tests). The G2 approval guard is a separate migration applied **after** `verify_load.sql`: `sql/g2_approval_guard_postgres.sql`, checked by `sql/g2_verify_guard.sql` (§8.1).
- **Synthetic tables:** `data/synthetic/*.csv`, generated by notebook 02 from `config/assumptions.yaml`. Sample extract: `data/synthetic/sample_extract_bookings.csv` (prefixes `real__`, `drv__`, `syn__`).
- **Capacity:** peak occupied rooms per room type = 252 rooms (P non-sellable), ~71.3% average occupancy in 2016 (Lisbon 4–5★ 2016: 69–79%).

---

## 5. Synthetic data (12 columns, 3 domains)

| Domain | Column | Grain | Description |
|---|---|---|---|
| **A. Customer Experience** | `satisfaction_score` | booking | Post-stay satisfaction, 1–5 |
| | `cancellation_reason` | booking | Category (change of plans, price, duplicate booking, other) **plus a free-text sentence**, used for TF-IDF |
| | `contact_attempted` | booking | Whether the hotel proactively contacted the guest |
| | `contact_channel` | booking | email / SMS / phone |
| | `customer_response` | booking | confirmed / requested change / no response |
| **B. Finance** | `room_cost_per_night` | booking | Cost of servicing one room for one night |
| | `channel_commission_rate` | booking | Commission paid to the distribution channel |
| | `cancellation_handling_cost` | booking | Cost of processing one cancelled booking |
| | `rebooking_revenue` | booking | **Gross** revenue from reselling a cancelled room (plus `rebooked_room_nights`, the resold nights from the same simulation, so the resale can be costed on a contribution basis) |
| **C. Operations** | `available_rooms` | date × room type | Sellable rooms that day, per room type |
| | `rooms_sold` | date × room type | Rooms actually sold that day, per room type |
| | `resold_after_cancellation` | date × room type | Rooms resold after a cancellation, per room type |

### 5.1 Principles
- Generate only what the workflow needs. Synthetic data lives in **separate tables** (`is_synthetic`), appears as `syn_*` columns after integration, and is presented as *"simulated, based on stated assumptions"*.
- Values exist only where they logically can (e.g. satisfaction only after check-out, reasons only for cancellations, rebooking revenue only if resold). `rebooking_revenue` and `resold_after_cancellation` come from the same simulation.

### 5.2 Assumption requirements
- The booking data covers **2015–2017**, so every assumption (room cost, commission rates, staff cost, hotel capacity, resale likelihood, intervention effect, etc.) must be **grounded in the real situation in Lisbon / Portugal during 2015–2017**, backed by a credible source (industry reports, official statistics, academic papers), or derived from the dataset itself.
- If no source exists, mark the value as a **team assumption**, give a reason, and give a sensitivity range.
- Keep every parameter in one place (`config/assumptions.yaml`: value, unit, basis, source, sensitivity range; international proxies are labelled `proxy_note`). Code must read from that file, with no hard-coded numbers (behavioural thresholds and sampling seeds included).

### 5.3 Usage rules
- `satisfaction_score` is observed only after the stay. It must **never** be used to predict cancellation.
- `cancellation_reason` can't be known if the guest doesn't give one. It is synthetic, used for text analytics and intervention design only.
- `customer_response` is not a feature for the main model (if it is simulated from the outcome, it would inflate accuracy).
- **`cx_contact` must not be used to estimate the effect of contacting guests.** Contact has no causal effect in the simulation, and bookings cancelled before the planned contact are never contacted, so contacted bookings look far safer (survivorship bias). Intervention uplift comes from an explicit scenario assumption and a pilot with a holdout group.
- Finance and Ops columns feed revenue at risk, net revenue and scenario simulation, not the classifier.
- Money is compared on **one contribution basis** (revenue net of commission minus room cost): resale counts as resale revenue minus its commission and room cost, never as gross revenue (notebook 03 §2.6). The resulting net-loss figure is a **simulated proxy**, not a measured loss.

---

## 6. Method (PDMT – M)

### 6.1 Pipeline
```
raw CSV → filter City → assign booking_id → clean → feature engineering
   → time-based split → train & calibrate classifier → p_cancel
   → revenue affected → rule-based actions (follow-up · price adjustment · capacity allocation · resale)
   → staff approval → n8n execution (reminders, inventory updates, tasks, alerts)
   → intervention log → KPIs / dashboard → feedback & re-tune
```

### 6.2 Cancellation model
- **Input:** `data/processed/modeling_dataset.csv` (notebook 03) with `population` and `split`. It is **already filtered** to `exclude_from_model == False` (78,892 rows), so no further **data-quality** filter is needed (the flag column is not in this file). The `pre_arrival` **eligibility** filter (booking created and still active at the score time) is a different filter and is still applied in F1. Time-based split: train = arrivals ≤ 2016-12-31, test = 2017-01-01 → 2017-08-31. Never report a random-split result.
- **Modeling notebook (stage F, v3.1):** [`notebooks/04_modeling_evaluation.ipynb`](notebooks/04_modeling_evaluation.ipynb), with shared code in `src/f_modeling.py`. It follows `docs/F1_validation_design.md`: three expanding folds validating 2016 Q2, Q3, Q4 (fit dates 2016-03-31 / 06-30 / 09-30), selection and calibration on their pooled out-of-fold predictions, final fit on all individual arrivals ≤ 2016-12-31 (= selection cutoff), and one evaluation on 2017 per run. Learners: logistic regression and XGBoost (Optuna, objective = mean fold log-loss, recency weighting as a hyper-parameter), against a constant, lead-time, segment × lead-band and lead + segment baselines. The top-15 agent list is learned inside every fold. Two views are kept apart: the **retrospective arrival-cohort evaluation** (metrics only) and the **operational weekly-queue simulation** (bookings scored at the end of their booking week, by a model fitted and selected before that date, only if still eligible: created, not cancelled, not arrived, not complimentary; consent / open action unknown). `risk_scores.csv`, tiers, `action_queue_2017.csv` and the dated `action_schedule_2017.csv` come only from the queue, through one canonical score table (cross-file checks in `output_consistency.csv`). Ties are broken by a hash of `booking_id`; capacity slots per batch are floor(share × batch). The bundle holds the individual model **and** the group rule; `fm.score_bookings` dispatches both tracks and refuses a score time before the fit date. All outputs are written to `artifacts/modeling/runs/<run_id>/` and copied to `artifacts/modeling/` as the latest run. Earlier experimental notebooks and outputs have been removed from this distribution; only the current F results are included.
- **Results, retrospective arrival cohort (test 2017, individual, 20,399 bookings, 34.4% cancelled):** XGBoost ROC-AUC 0.736, PR-AUC (average precision) 0.556, Brier 0.192 (constant 0.230); the cohort top 20% is 58.3% cancellations (lift 1.70, +308 vs the segment × lead-time rule, paired block bootstrap), and ranking by expected loss reaches 52% of cancelled gross value with a 20% queue (random 20%). Whole hotel (individual model + `group_block` rule): ROC-AUC 0.820. Robustness: booking-date-filtered view (population A) 0.730, rule B + booked after the fit date 0.732, no duplicates 0.735, population rule B 0.740; `pre_arrival` H = 14 / 30 with their own selection 0.672 / 0.686. The weekly sum of p matches actual cancelled **bookings** with a WAPE of 5–7%: a retrospective aggregate, not a forecast of resellable rooms.
- **Results, simulated weekly queue 2017 (12,102 eligible individual bookings, 24.0% cancelled):** top 20% per weekly batch is 41.1% cancellations (lift 1.71) vs 35.3% for the rule ranked in the same batches (+139 cancellations, 95% CI 48–232). Details: `artifacts/modeling/model_report.md`, `docs/Bao_cao_F1-F3_VI.md`.
- **Scope (retrospective prototype).** Field values come from a near-arrival PMS snapshot. The dataset has no booking history, so booking-time values (e.g. the ADR quoted at booking) cannot be reconstructed. Results show what a booking-level risk model can achieve on this extract; they do not prove the performance of a live booking-time system.
- **Two populations:**
  - **`individual`** (52.7k bookings, 28–34% cancelled): main ML model (XGBoost, logistic regression as the explainable reference). Drives guest follow-up. **Its accuracy is the headline figure.**
  - **`group_block`** (26.2k bookings: `market_segment == Groups` OR `deposit_type == Non Refund` OR `dup_group_size >= 5`; ~65% cancelled, early and in batches): rules or a simple model, forecasting **when and how many** rooms return. Drives release deadlines, staged deposits, capacity reallocation and controlled overbooking. `group_block` is an inferred **proxy**, not a verified group label (281 `Group` / 1,552 `Contract` bookings stay in `individual`; sensitivity to alternative rules in notebook 03 §2.0.1). `dup_group_size` is a retrospective group size from snapshot attributes: its availability at booking entry or at the end of the booking day is **not verified**. A strict booking-time experiment must drop/rebuild the block-size features **and** re-derive the population rule.
- **Two feature sets (score times):** see `feature_sets` in `config/column_groups.json`.
  - **`booking_time` (primary):** `numeric` + `categorical` lists, **minus all price-derived inputs** (`adr_clean`, `adr_vs_ref`, `adr_per_guest`, `is_free_rate`: snapshot ADR, not the rate quoted at booking; decided in F, listed in notebook 04 `CONFIG`). Every booking is eligible at entry.
  - **`pre_arrival` (secondary):** adds `pre_arrival_extra` (waiting list, special requests). Only bookings **created and still active** (not cancelled) at the score time are eligible; the horizon and this filter are implemented in F1.
  - **Never used:** `excluded_features` (`booking_changes`, `has_booking_changes`: cumulative until check-in or cancellation; `otb_ratio_at_booking`, `room_type_capacity_share`: built from capacity estimated with 2017 stays). Meaning and availability of every feature: `docs/feature_availability.csv`.
- **Test-set caveat:** EDA in notebook 03 already looked at 2017 labels, so the 2017 test set is a **partially explored holdout**. Tune and choose thresholds on a validation period inside the training years; report test results with this caveat.
- **Time contract (F1):** `docs/F1_validation_design.md`: model-fit / score / label-known dates, rolling-origin folds in 2015–2016, fold-local refit of the ADR reference and top-agent list, and the booking-date view (36.3% of 2017 test bookings were created before the training cutoff).
- Encode categoricals inside the sklearn pipeline. Report results with and without `is_duplicate == 1`.
- **Check calibration** (probabilities are multiplied by money): a calibrator is applied only if it improves pre-test evidence (F kept raw probabilities; Platt and isotonic were worse on the most recent fold). **Metrics:** ROC-AUC, PR-AUC, precision/recall/lift at a capacity k%, Brier, % of revenue at risk captured in the top-scored bookings. Lift is computed against the event rate of the **same evaluation cohort**. **Explain** with feature importance / SHAP in business language.
- **Known drift:** agent mix (PSI 1.09; 40 agents appear only in 2017), median ADR +16.5% train → test (EUR 95 → 110.7; mean +18.8%), more Online TA / Transient, history features collapse in `group_block`.

### 6.3 Text analytics
TF-IDF on the `cancellation_reason` text → clustering (e.g. K-Means) → label the clusters → reason mix by segment/channel, used to tailor intervention messages. The text is template-generated, so this demonstrates the pipeline, not real customer reasons.

**Built:** `python src/text_clustering.py` → `artifacts/text_clustering/`. 22,501 reason texts of cancelled bookings; TF-IDF (1–2 grams, courtesy and generic booking words removed) → K-Means with k chosen by silhouette over 3–30 (k = 30, silhouette 0.94: the template phrasings separate cleanly, so clusters are read at label level). Each cluster is labelled by its dominant generator category, or `mixed / unspecified` below 60% purity (5.1% of texts); label agreement with the generator 93.7%. Outputs: reason mix by segment and channel, a message angle per reason, `fig_reason_mix_by_segment.png`. The reason is known only after the cancellation, so it is never a model feature (`tests/test_text_clustering.py` checks this).

### 6.4 Decision logic (draft)
G1 policy, dated capacity-aware actions, owners and stop conditions: `docs/decision_table_draft.md` and `artifacts/business_case/`. See the generated G1 results below; the G2 dashboard runs on this policy.

### 6.5 Scenario simulation
Compare at least: status quo · email-only automation · risk-based tiered actions (proposed) · tiered actions plus price adjustment, capacity allocation and resale/overbooking. Outputs: cancellations avoided, occupancy (by room type), ADR, RevPAR, recovered revenue, intervention cost, net benefit, ROI. Include a sensitivity analysis on the key assumptions.

**Built:** notebook 05 (S0–S5: cancellations avoided, cost, net, ROI, sensitivity; `artifacts/business_case/`) and `python src/scenario_capacity.py` (`artifacts/scenarios_capacity/`): a room-night ledger per stay date × room type for stays 01/01–31/08/2017, starting from the realised inventory (48,182 room-nights sold of 61,236, equal to `ops_inventory_daily.rooms_sold`). Expected values, illustrative:

| Scenario | Occupancy | ADR (EUR) | RevPAR (EUR) | Δ room revenue (EUR) | Contribution net (EUR) |
|---|---|---|---|---|---|
| S0 actual 2017 | 78.7% | 116.96 | 92.02 | 0 | 0 |
| S4 G1 policy (85.6 cancellations avoided, 17.4 induced) | 79.0% | 117.22 | 92.59 | +34,672 | +20,560 |
| S5 + group early release (rule G1) | 79.0% | 117.20 | 92.63 | +37,071 | +21,349 |
| S6 + price ±5% (elasticity −0.8, team assumption) | 76.8% | 121.37 | 93.16 | +69,475 | +85,618 |

S4's ledger contribution (+20,560) agrees with notebook 05's net (+20,455) by a separate route. S6 raises the rate on cells at ≥ 85% occupancy and cuts it below 60%, with `g1.price_adjustment` (elasticity −0.8, range −0.4 to −1.5; step 5%, range 3–10%) labelled as team assumptions: the data has no price variation to estimate them. Its sign rests on that guess: a raise pays only while demand is inelastic (above −1); at −1.2 or −1.5 RevPAR falls (`price_sensitivity.csv`). Part of S6's contribution is room cost saved on fewer sold nights. Outputs also by room type and month; G3 overbooking stays disabled and is not a scenario option.

---

## 7. Evaluation plan

Full KPI register (formula, cohort, period, owner, baseline, target status, source): `docs/kpi_register.csv`. Model-validation design: `docs/F1_validation_design.md`.

| Level | KPI |
|---|---|
| Model | ROC-AUC, PR-AUC, recall, Brier, Precision/Lift@20% (lift vs the same cohort's event rate), revenue-at-risk capture |
| Process | Contact rate, response rate by channel, time to first contact, approval turnaround |
| Business | Cancellation rate (esp. last-minute), occupancy rate (by room type), ADR, RevPAR, recovered revenue, intervention cost, net revenue, ROI |
| CX | Satisfaction score after contact |
| Rollout | Pilot with a holdout group to measure true uplift |

---

## 8. Tools (PDMT – T) and build scope

| Layer | Tool | Scope |
|---|---|---|
| Processing and ML | Python (pandas, numpy, scikit-learn) | **Real** |
| Text analytics | scikit-learn TF-IDF + clustering | **Real** |
| Storage | PostgreSQL (data model; G2 shared store via `G2_DB_URL`) / SQLite (local demo store) | **Real** |
| Automation | n8n (guest reminders and follow-ups, room-inventory updates, staff tasks and alerts, approval requests) | **Prototype** (`n8n/`: guard + mock dispatch, sends nothing; run end to end on a local n8n 2.43.8) |
| UI | Website (Streamlit, `dashboard/`): overview (yesterday / last 7 days, then today), risk queue with batch review, action review, execution & follow-up, capacity planning (G2 + I5), performance (outcomes of arrived bookings), model monitoring (metrics recomputed on bookings arrived before today), data scope | Dashboard **real** on F outputs. It works on one fixed day, today = 01/05/2017 (`G2_TODAY`), with the team's history up to yesterday **prepared** (approvals, contacts, guest answers derived from the real outcome; `dashboard/seed_history.py`). Nothing leaves the computer: no guest contact, no PMS / price / inventory change. Real vs prepared: `dashboard/README.md`. Booking-management and revenue-performance pages not built |

### 8.1 G2 system prototype
- **Website:** `dashboard/app.py` (entry point; pages in `dashboard/views/`, Overview first) reads the latest F run straight from `artifacts/modeling/` (`action_queue_2017.csv`, `action_schedule_2017.csv`, `manifest.json`; stops if `output_consistency.csv` has a failed check) and never uses the retrospective arrival-cohort file or any post-outcome field. Run: `python -m pip install -r requirements-g2.txt`, then `python -m streamlit run dashboard/app.py` (`requirements-g2.txt` is separate because F's manifest records the hash of `requirements.txt`). Details and demo script: `dashboard/README.md`.
- **Approval guard (`docs/decision_table_draft.md` §6):** enforced in the service and in the database. Local store: SQLite in `data/outputs/`, one file per F run + G1 policy + today's date (CHECKs + triggers, tested). Business dates are enforced (no action before F scored the booking, none on or after arrival) and so is the same-day rule (no automated message on a call day). A rewritten F output is reloaded on the next interaction. Shared store: set `G2_DB_URL=postgresql://user@host:port/db` and the dashboard keeps the same store in PostgreSQL, one schema per F run + G1 policy + store version (`dashboard/pg_backend.py`; DDL generated from the SQLite rules into `sql/g2_state_store_postgres.sql`, same CHECKs, triggers and messages). The G2 store tests run on both back ends (`G2_TEST_PG_DSN=... python -m unittest discover -s tests`; passed on PostgreSQL 16). The migration for the `hotel` data model is `sql/g2_approval_guard_postgres.sql` (same rules for `hotel.action_plan` / `hotel.interventions`, G3 refused; `sql/g2_verify_guard.sql`: 19/19 pass on PostgreSQL 16). Execution-time checks the data cannot answer (consent, open action, still active) are confirmed by staff and logged. Concurrency: one connection per browser session, every write is one transaction under the store's write lock (the n8n call included), and database triggers repeat the call limit, closed-vs-executed and G3 rules for direct SQL writers (`tests/test_g2_concurrency.py`, SQLite and PostgreSQL).
- **n8n:** `n8n/g2_approval_demo_workflow.json` (webhook → guard → mock dispatch → response); node code in `n8n/src/`, tested with Node.js and run on a local n8n 2.43.8 (imported and published by CLI; dashboard → webhook → accepted → logged as `n8n`; pending and G3 actions refused by n8n's own guard). Live test: `tests/test_g2_n8n_live.py` (skipped unless `G2_TEST_N8N_URL` is set). Steps: `n8n/README_VI.md`. Production flow design: `docs/G2_architecture.md`.
- **AI email drafts (`docs/SPEC_AI_N8N_VI.md`):** reminder, booking confirmation and reply-to-guest drafts made by n8n Cloud workflow A, reviewed and approved in the Execution page, sent as DEMO tests by workflow B (mock or the team's test inbox) with a claim protocol in PostgreSQL schema `g2_ai_v1`. Labels and test sends never write the G2 action log. Code `dashboard/ai_email/`, workflows `n8n/g2_ai_email_*_workflow.json`, deployment `docs/README_DEPLOY_CLOUD.md`, verification status `docs/AI_EMAIL_VERIFICATION_VI.md`.
- **Docs and tests:** `docs/G2_architecture.md`, `docs/G2_data_contract.md`, `docs/G2_tool_choices.md`; `python -m unittest discover -s tests -v`.

---

## 9. Deliverables checklist

Status per component: **Done** · **Partial** · **Not yet due** (later stage).

| Deliverable | Status | Evidence / what is left |
|---|---|---|
| Problem statement, stakeholders, decisions | Done | `docs/Problem Statement.docx` and its Word render `docs/Problem Statement.pdf` (current-state claims labelled as assumptions to validate) |
| PDMT map | Done (F → G1 → G2) | `docs/pdmt_map.png` redrawn from the artifacts by `python src/draw_pdmt_map.py` (F metrics, G1 policy, scenarios, G2 tools). The copy embedded in `docs/Problem Statement.docx/.pdf` is still the A–D2 version: replace the image in Word before submission |
| Expected business value | Done (simulated G1) | Notebook 05; generated results below; effects require pilot confirmation |
| KPI framework | Partial | `docs/kpi_register.csv`; model KPIs M1–M6 set in F2; business targets in G1 |
| Draft decision workflow | Partial | `docs/decision_table_draft.md` (thresholds provisional) |
| Data architecture / schema | Done | `sql/schema.sql`, `docs/data_architecture.md`; loaded and verified on PostgreSQL 16.15 (`sql/verify_load.sql`: 30 checks pass) |
| Data sources and features | Done | `docs/data_dictionary.csv`, `docs/feature_availability.csv` |
| Assumptions register | Partial | `config/assumptions.yaml` complete for A–D2; scenario parameters added in G1 |
| Simulated sample extract | Done | `data/synthetic/sample_extract_bookings.csv` |
| Integration and privacy | Done | notebook 02 §8; approval guard specified in `docs/decision_table_draft.md` §6, implemented in G2 (§8.1) |
| Analytical pipeline / evaluation plan | Done (model level) | README §6–7, `docs/F1_validation_design.md` (§7 implementation status); business KPIs measured in the G pilot |
| Cancellation model and evaluation (F1–F3) | Done (retrospective prototype) | `notebooks/04_modeling_evaluation.ipynb` v3.1, `artifacts/modeling/` (report, two-track bundle, weekly-queue risk scores, action queue and schedule). Open by design: snapshot features, consent / open-action data, room-level capacity forecast (G1) |
| Scenario simulation | Done (simulated) | Notebook 05: S0–S5, cancellations avoided, intervention cost, net benefit, ROI, sensitivity (`artifacts/business_case/`). `src/scenario_capacity.py`: S0/S4/S5/S6 room-night ledger with occupancy by room type, ADR, RevPAR, recovered revenue, contribution, price sensitivity (`artifacts/scenarios_capacity/`). Price elasticity is a labelled team assumption; overbooking analysed only (G3 disabled) |
| Text clustering (§6.3) | Done (synthetic text) | `src/text_clustering.py` → `artifacts/text_clustering/` (TF-IDF + K-Means, labelled clusters, reason mix by segment / channel, message angle); `tests/test_text_clustering.py` |
| Tool architecture, n8n prototype, dashboard | Partial (G2 prototype) | §8.1: `dashboard/`, `n8n/`, `sql/g2_*.sql`, `docs/G2_*.md`, `tests/`; runs on the G1 policy; store on SQLite or PostgreSQL; workflow run on a live local n8n. Left: revenue-performance page, scheduled n8n flow reading PostgreSQL, real channels (need consent data) |
| Pitch deck, executive summary | Not yet due | H |

---

## 10. Repository layout and conventions

```
BA-Advance-Project---Group-2/
├── README.md · Final-Project-Description.pdf
├── hotel_bookings.csv                 ← raw, read-only
├── config/   assumptions.yaml · column_groups.json
├── notebooks/ 01_data_audit_eda · 02_data_architecture · 03_preparation_eda_features · 04_modeling_evaluation · 05_decision_business_case
├── data/processed/  bookings_city_clean · bookings_prepared · modeling_dataset
├── data/synthetic/  cx_contact · cx_feedback · fin_booking · ops_inventory_daily · dim_room_type · sample_extract_bookings
├── sql/      schema.sql · load.sql · verify_load.sql · g2_approval_guard_postgres.sql · g2_verify_guard.sql
├── docs/     Problem Statement.docx · data_dictionary.csv · feature_availability.csv · data_architecture.md
│             kpi_register.csv · decision_table_draft.md · F1_validation_design.md
│             G2_architecture.md · G2_data_contract.md · G2_tool_choices.md
├── artifacts/modeling/  current stage-F outputs (bundle, risk_scores, tables, model_report.md, manifest.json)
├── artifacts/business_case/ G1 (notebook 05) · artifacts/scenarios_capacity/ §6.5 ledger · artifacts/text_clustering/ §6.3
├── requirements.txt · requirements-g2.txt · src/run_notebooks.py · src/f_modeling.py · src/scenario_capacity.py · src/text_clustering.py · src/draw_pdmt_map.py
├── dashboard/  G2 website (Streamlit) · README.md · run_windows.bat
├── n8n/        G2 workflow JSON · src/ node code · build_workflow.py
├── tests/      F / G1 / G2 tests (data contract, approval guard on SQLite and PostgreSQL, n8n nodes and live webhook, app, scenarios, text clustering)
└── data/outputs/  G2 action logs, one per day (SQLite, not committed)
```
- Run notebooks in order **01 → 02 → 03 → 04 → 05** (01 rewrites `column_groups.json`; 03 adds the final feature lists; 04 is stage F).
- **Clean run:** create a Python 3.13 environment, `pip install -r requirements.txt`, then from the repo root `python src/run_notebooks.py` (01–03), `python src/run_notebooks.py --stage F` (04, about 10 minutes because of Optuna) or `--all`. Fresh kernel per notebook, shut down after it (also on failure), outputs saved. Commit the executed notebooks with their outputs.
- Conventions: `snake_case`; ISO dates; EUR; `random_state = 42`; everything reproducible from the raw CSV.

---

## 11. Instructions for AI assistants

1. Read §0 and §5 before writing any code.
2. Start from `hotel_bookings.csv`. Filter to City Hotel **first**, then assign `booking_id` = 1…79,330 in file order (or simply use `data/processed/bookings_city_clean.csv`).
3. Model features come only from `individual_model` / `group_model` in `config/column_groups.json`, combined as described in `feature_sets`. Never use `nb01_candidate_columns`, ops-only columns or `excluded_features` as features. If unsure whether a column leaks, say so.
4. Any number you invent must follow §5.2 (Lisbon 2015–2017 basis, sourced or flagged) and go into `config/assumptions.yaml`.
5. Prefer simple, explainable methods, and explain results in business terms (EUR, rooms, %).
6. If a request is ambiguous or conflicts with this README, ask first.

---

## 12. Reference

- Antonio, N., de Almeida, A., & Nunes, L. (2019). Hotel booking demand datasets. *Data in Brief*, 22, 41–49. https://repositorio.iscte-iul.pt/bitstream/10071/16929/1/Hotelbookingdemanddatasets.pdf

<!-- G1_RESULTS:START -->
## G1 — generated policy and results

Call tier: top 15% by `p_x_gross`; total call + reminder tier: 60% of each weekly batch. Scores and dates come from the current F run. The 2016 selection uses weekly rolling predictions, with estimator selection on matured 2015 labels and policy outcomes frozen at 2016-12-31. Call tasks start on the decision day, at most 20 booking-level tasks/day within 7 calendar days, and at least 8 days before arrival. Overflow is sent to manual review; task costs include average attempts.

2017 queue: **12,102 bookings**. Capacity-aware S4 net benefit after variable intervention costs: **EUR 30,390/year**; total-programme 3-year ROI: **172.6%**; payback: **6.9 months**. ROI = net after all costs / all variable intervention, one-off and running costs over the same horizon. These are simulated values, not measured savings.

Handover: `artifacts/business_case/action_queue_g1_2017.csv`, `action_schedule_g1_2017.csv`, `g1_policy.json`. Outcome-dependent contacts and net values are isolated in `retrospective_evaluation_2017.csv`. **G3 overbooking is disabled**, including in the machine-readable rules and policy. I6 deposits remain disabled by default. Consent, current status, responses, open actions and live capacity must be checked at execution.

Limitations: snapshot features/population proxy inherited from F; unresolved outcomes are omitted from the 2016 objective, while full queues determine tiers/capacity; historical and final F estimators differ. Confirm policy transfer and guest response in the randomised pilot. The separate overbooking experiment remains exploratory and cannot authorise execution.

Run G1: `python src/run_notebooks.py --stage G1`. See `docs/G1_data_contract.md`. **G2 runs on this policy:** `dashboard/g1_contract.py` re-derives the G1 tiers from F scores, checks the dated schedule (milestones, call window, daily capacity) and binds the G2 state DB to F run + G1 policy; manual-review calls get no automatic action. See `docs/G2_data_contract.md`.
<!-- G1_RESULTS:END -->
