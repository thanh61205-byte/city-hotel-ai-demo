# F1 validation design — time contract for the cancellation model

Status: **design agreed before training** (findings D201, D203, D204, D205 of the A–D2 review). F1 implements it; nothing here is a model result.

## 1. What the evaluation can and cannot claim

- The public extract is a **near-arrival snapshot**. Even "booking-time" inputs (ADR, party, room type, segment, arrival date, lead time) are the last recorded values. Every result is therefore a **retrospective prototype** result: it shows what a booking-level risk model can do on this extract, not the performance of a live system that scores bookings when they are entered.
- The 2017 test period is a **partially explored holdout**: notebook 03 looked at 2017 labels during EDA (disclosure at the start of D2). It was never used to fit features or tune models, but it must not be called "untouched". Re-running EDA on train only cannot undo this.

## 2. Dates

| Term | Definition used in F1 |
|---|---|
| Fit date (training cutoff) | Last date whose information may be used to **train** a model and fit its transforms. `booking_time`: fold train end, and **2016-12-31** for the final model. `pre_arrival`: window start − H − 1 day (Section 5). |
| Selection cutoff | Last date whose information may be used to **choose** the final model: hyper-parameters, calibration and decision thresholds. It equals the fit date of the final model of that branch (Section 3). |
| Score date | `booking_time`: the booking date (`booking_date`). `pre_arrival`: `arrival_date − H`, with **H = 14 days** (primary) and 30 days (sensitivity). |
| Label-known date | **Convention:** Canceled → `reservation_status_date`; Check-Out and No-Show → `arrival_date` (the guest came or did not come on that day). The extract has no log of when a check-in or no-show was actually recorded, so this is an assumption, not a verified system timestamp. |
| Label maturity rule | A row is usable at cutoff *t* (for training **or** for selection) only if its label-known date ≤ *t*. |
| Strict time rule | In the **booking-date view** and the **`pre_arrival`** branch, a model never scores a booking whose score date is on or before its fit date. The **arrival-cohort headline** is an explicit exception (Section 4): a retrospective evaluation, reported as such. |

Facts from notebook 03, Section 3.3 (eligible population, 78,892 rows):

- **9,946 of 27,396 test bookings (36.3%)** were created on or before 2016-12-31 (individual 5,275, group_block 4,671). A model fitted at year-end 2016 could not have scored them on their booking date. The arrival-cohort headline therefore keeps them as a retrospective evaluation, and the booking-date view (Section 4) excludes them.
- **218 training rows** have a final status date after 2016-12-31. All 218 are **Check-Outs** of stays that started before the cutoff. Under the label-known convention their label was settled on the arrival day, so **0 training labels are unknown at the cutoff by this convention**. These rows are kept; the maturity rule is still applied inside every fold.

## 3. Validation scheme (training, selection, calibration, threshold)

Rolling-origin folds **inside the training years** (arrival-based, expanding window). The fit date of each fold depends on the branch:

| Fold | Validate: arrivals | Fit date, `booking_time` | Fit date, `pre_arrival` H = 14 | Fit date, `pre_arrival` H = 30 |
|---|---|---|---|---|
| 1 | 2016-04-01 → 2016-06-30 | 2016-03-31 | 2016-03-17 | 2016-03-01 |
| 2 | 2016-07-01 → 2016-09-30 | 2016-06-30 | 2016-06-16 | 2016-05-31 |
| 3 | 2016-10-01 → 2016-12-31 | 2016-09-30 | 2016-09-16 | 2016-08-31 |
| Final model → test (2017-01-01 → 2017-08-31) | — | **2016-12-31** | **2016-12-17** | **2016-12-01** |

Rules inside **every** fold:

1. **Training rows:** arrivals up to the fold's fit date that pass the label maturity rule at that date. For `pre_arrival`, they must also be eligible at their own score date (Section 5).
2. **Refit every fitted transform on those training rows only:** ADR reference medians (`adr_vs_ref`), the top-15 agent list (`agent_top`), encoders, scalers, imputers. The values stored in `modeling_dataset.csv` were fitted on all 2015–2016 training arrivals. They are valid **only** for the final `booking_time` train → test model (fit date 2016-12-31), **not** for any fold and **not** for the `pre_arrival` final models, whose fit date is earlier. Rebuild them from `bookings_prepared.csv` (join on `booking_id`) for every fold and for the final `pre_arrival` models.
3. **Score** the fold's validation rows with the fold model. Out-of-fold predictions are the only input for selection.

**Selection of the final model of each branch** (hyper-parameters, calibration, thresholds):

- The evidence may only use validation rows whose **label-known date ≤ the selection cutoff** of that branch (= its final fit date).
  - `booking_time` (cutoff 2016-12-31): all three folds, in full.
  - `pre_arrival` H = 14 (cutoff 2016-12-17) and H = 30 (cutoff 2016-12-01): folds 1–2 in full; fold 3 only for validation rows whose label is known by the cutoff. Rows of fold 3 whose label becomes known after 2016-12-17 (resp. 2016-12-01) are dropped from the selection evidence, so no choice for that model depends on information it could not have had.
- The calibration map and the decision thresholds are fitted on those out-of-fold predictions, under the same cutoff.
- The final model is then **refitted** at its fit date on all rows that pass rule 1, with the frozen configuration.
- Test (2017) is scored **once** and reported with the partially-explored caveat.

If the team prefers to use the full 2016 validation for the `pre_arrival` models, that is acceptable only if it is reported as an offline evaluation in which model selection used information after some score dates. Do not describe it as a process available at the fit date.

## 4. Results to report

| View | Population | Time rule | Purpose |
|---|---|---|---|
| Arrival-cohort test (headline) | `individual`, rule A | Retrospective (exception to the strict rule) | Main retrospective result |
| Booking-date view | Test bookings created **after** 2016-12-31 only (17,450 in both populations; 15,124 individual) | Date filter only (population A still uses the snapshot block size); strict only when combined with rule B (Section 5) | Approximation of "scored at entry by a year-end model" |
| `pre_arrival` view | Bookings eligible at `arrival_date − H` | Strict (Section 5) | Secondary horizon model |
| Population sensitivity | `individual` under rules A and B (no block size) at least | As the main view | Shows the result is not an artefact of the `group_block` proxy (notebook 03, Section 2.0.1) |
| Duplicates | With and without `is_duplicate == 1` | As the main view | README §6.2 |
| `group_block` | Rules or a simple model | Retrospective | Timing and volume of released rooms, not headline accuracy |

Metrics: ROC-AUC, PR-AUC, recall, Brier score after calibration, Precision@20% and **Lift@20% = Precision@20% / event rate of the same evaluation cohort** (see `docs/kpi_register.csv`). Compare with a baseline (lead time + segment logistic regression).

## 5. Feature-set rules

- **`booking_time` (primary):** `numeric` + `categorical` lists of the population, every booking eligible at its booking date.
- **`pre_arrival` (secondary):** adds `pre_arrival_extra`. Eligible bookings at score date *s* = `arrival_date − H`: **created on or before *s*** (`booking_date ≤ s`) **and still active at *s*** (not Canceled with `reservation_status_date ≤ s`). The eligibility columns come from `bookings_prepared.csv` joined on `booking_id`; `reservation_status_date` is used **only** for this filter and for the label, never as a feature. Even then, the snapshot features hold near-arrival values, not values as of *s*; state this limitation.
- **`pre_arrival`: score date before the fit date.** For arrivals in the first *H* days of an evaluation window, *s* falls on or before the **old cutoff** (the day before the window starts), so a model fitted at that old cutoff would score the past. Counts (notebook 03, Section 3.3):

  | Window (old cutoff) | H = 14: eligible / *s* ≤ old cutoff | H = 30: eligible / *s* ≤ old cutoff |
  |---|---|---|
  | Fold 1 (2016-03-31) | 5,581 / 807 | 5,132 / 1,646 |
  | Fold 2 (2016-06-30) | 5,732 / 806 | 5,370 / 1,768 |
  | Fold 3 (2016-09-30) | 4,645 / 1,025 | 4,179 / 1,915 |
  | Test (2016-12-31) | 13,474 / 408 | 12,194 / 664 |

  **Rule (primary):** fit the `pre_arrival` model of each window at **window start − H − 1 day** (Section 3 table). Notebook 03 asserts that no eligible booking then has *s* ≤ fit date, in any window; no window row is dropped. Training rows, transforms and selection follow Section 3.
  **Sensitivity:** keep the old cutoff (the day **before** the window starts), **exclude** the bookings with *s* ≤ old cutoff (counts above, e.g. 408 / 664 in test) and report them. Using the window start itself instead would exclude 433 / 668 in test. Both variants must give the same conclusion; otherwise report both.
- **Block-size proxy:** `dup_group_size`, `dup_group_size_log`, `is_group_block` and the `group_block` rule are kept for the retrospective prototype. If F1 reports a **strict booking-time** experiment, drop or rebuild these features **and** re-derive the population with a rule that does not use `dup_group_size` (rule B). Dropping the columns from X alone leaves the dependency in the population split.
- **Never used:** `excluded_features` in `config/column_groups.json`, ops-only columns, all `syn_*` columns.

## 6. Money weights

`syn_net_booking_value` and the net-loss proxy (notebook 03, Section 2.6) depend on simulated assumptions. If F weights the loss function or ranks bookings by value, report the unweighted result too, and run the value-based ranking under the deposit-retention sensitivity range in `config/assumptions.yaml`.

## 7. Implementation status (stage F, notebook 04 v3.1, 19/10/2026)

v3.1 applies the clean re-run audit of v3 (items Fv3-01…14). "Done" below means implemented as described in the note, including its stated exception; it does not mean that the extract supports a live booking-time claim (Section 1).

| Design item | Status | Where / note |
|---|---|---|
| Three expanding folds (2016 Q2, Q3, Q4), fit dates as in §3 | Done | Notebook 04 §1.1; label maturity asserted for every training set |
| Fold-local refit of fitted transforms | Done | Top-15 agent list learned inside each pipeline (`src/f_modeling.TopAgentEncoder`); `adr_vs_ref` is not used (all price-derived inputs excluded, see below) |
| Selection, calibration and policy on out-of-fold predictions only | Done, with a limit | Model chosen by mean fold log-loss; calibration (none / sigmoid / isotonic) fitted on folds 1–2, judged on fold 3 → none kept. Fold 3 is also part of the tuning objective, so the calibration choice is cross-validated evidence, not an independent validation |
| Operating threshold | **Changed** | A frozen probability threshold does not keep a stable queue under drift (v2: 20% → 66.7%). The policy is capacity-based: top 5% / 20% of each weekly batch of new bookings (`risk_tier` high / medium), slots = floor(share × batch), ties broken by a hash of `booking_id`. A fixed cut-off is reported as a diagnostic |
| Score time of the operational output (`risk_scores`, tiers, actions) | **Changed (v3.1)** | Decision time = end of the booking week (the weekly ranking needs the whole week). Only weeks closing after the fit date / selection cutoff (2016-12-31) are scored; a booking enters the queue only if it is still eligible then (created, not cancelled, not arrived, not complimentary; consent / open action unknown). `days_to_arrival` from the decision time. The arrival-cohort headline stays a retrospective evaluation and is not written to `risk_scores` |
| Baseline (lead time + segment logistic regression) | Done | Plus constant, lead-time rule and segment × lead-band rule; paired block bootstrap by arrival week for the cohort top-k; paired whole-batch bootstrap for the weekly policy (model vs rule ranked in the same batches) |
| Booking-date view | Done, **renamed** | "Booking-date-filtered retrospective view, population A": test bookings created after 2016-12-31 (15,124). It filters dates only — population A still uses `dup_group_size` — so it is **not** called strict |
| Rule B combined with the booking-date filter | **Added (v3.1)** | Rule B population (no block-size proxy) **and** booking date after 2016-12-31, retrained on rule-B training rows; feature values are still snapshot values (§5) |
| Duplicates | Done | Retrained and evaluated without `is_duplicate == 1` |
| Population sensitivity, rule B | Done | `group_block` = Groups or Non Refund; retrained (rule A is the headline) |
| `pre_arrival` H = 14 and 30 | **Done (v3.1)** | Eligibility at `arrival − H`, fold fit dates and final fit at window start − H − 1, label maturity. Each branch selects its own configuration (Optuna on its three pre-arrival folds, `CONFIG['pre_arrival_trials']`) and calibration on validation rows whose label is known by the branch cutoff (fold-3 rows with a later label are dropped; counts in `pre_arrival_selection.csv`). The +extras model reuses the configuration selected on the base inputs |
| `group_block` track | Done (rule) | Historical rate per market segment × deposit type, stored in the bundle with the population rule; `fm.score_bookings` dispatches both tracks. The weekly sum of p is a **retrospective aggregate of bookings**, not a forecast of resellable rooms (no forecast origin, no stay date × room type) |
| Money weights (§6) | Done | Ranking by p and by p × value, on gross (real) and contribution (simulated) value; deposit sensitivity not needed for individuals (99.96% No Deposit) |
| Price-derived features | **Changed** | `adr_clean`, `adr_vs_ref`, `adr_per_guest` and `is_free_rate` are all excluded from the booking-time model (snapshot ADR, not the quoted rate; in the review experiments adding them did not improve the H2-2016 validation) |

Results: `artifacts/modeling/model_report.md`.
