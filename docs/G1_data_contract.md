# G1 handover contract (schema 2)

Call tier: top 15% by `p_x_gross`; total call + reminder tier: 60% of each weekly batch. Scores and dates come from the current F run. The 2016 selection uses weekly rolling predictions, with estimator selection on matured 2015 labels and policy outcomes frozen at 2016-12-31. Call tasks start on the decision day, at most 20 booking-level tasks/day within 7 calendar days, and at least 8 days before arrival. Overflow is sent to manual review; task costs include average attempts.

2017 queue: **12,102 bookings**. Capacity-aware S4 net benefit after variable intervention costs: **EUR 30,390/year**; total-programme 3-year ROI: **172.6%**; payback: **6.9 months**. ROI = net after all costs / all variable intervention, one-off and running costs over the same horizon. These are simulated values, not measured savings.

Handover: `artifacts/business_case/action_queue_g1_2017.csv`, `action_schedule_g1_2017.csv`, `g1_policy.json`. Outcome-dependent contacts and net values are isolated in `retrospective_evaluation_2017.csv`. **G3 overbooking is disabled**, including in the machine-readable rules and policy. I6 deposits remain disabled by default. Consent, current status, responses, open actions and live capacity must be checked at execution.

Limitations: snapshot features/population proxy inherited from F; unresolved outcomes are omitted from the 2016 objective, while full queues determine tiers/capacity; historical and final F estimators differ. Confirm policy transfer and guest response in the randomised pilot. The separate overbooking experiment remains exploratory and cannot authorise execution.

Run G1: `python src/run_notebooks.py --stage G1`. See `docs/G1_data_contract.md`. **G2 runs on this policy:** `dashboard/g1_contract.py` re-derives the G1 tiers from F scores, checks the dated schedule (milestones, call window, daily capacity) and binds the G2 state DB to F run + G1 policy; manual-review calls get no automatic action. See `docs/G2_data_contract.md`.

## Files and invariants

- Queue: one row per eligible F individual booking; F score/decision date retained. `planned_messages` counts the initial I1/I2 plan; it is never reduced using a future cancellation. `first_contact` is the first dated action, blank for buffer/manual-overflow rows. No outcome-conditioned `expected_net_eur` or `messages` field is exported in the queue.
- Schedule: booking ID, decision date, scheduled date, rule, action type and sequence. No date before decision or on/after arrival. I3 booking tasks obey the global daily cap and call window. An I3 with `manual_capacity_review` has no executable action. One call task may contain up to two attempts; actual staffing time and retry handling require execution-level checks.
- Retrospective evaluation: actual-outcome-conditioned simulated contacts, costs and benefits. This file is evaluation-only and must not feed initial scheduling or live dispatch.
- Policy: binds the F run, G1 shares/ranking, call schedule, rolling selection provenance, ROI definition, execution checks and `overbooking.enabled=false`.
- Rolling selection: predictions, model/label cutoffs and SHA256-checked cache. Changing source inputs, settings or helper code invalidates the cache.
- Regeneration updates README, decision table and KPI references from the same results. Do not combine files from different runs.

## Evaluation boundaries

2016 queues include all eligible weekly bookings so ranks and capacity are not filtered using future outcomes. Only labels resolved by 2016-12-31 contribute to policy value; long-lead bookings are underrepresented in that objective. The historical estimator is selected on 2015 data and cannot use final-F hyperparameters tuned on 2016. The 2017 queue uses final F and evaluates the frozen policy. F's snapshot/population limitations remain. Overbooking uses a separate exploratory retrospective experiment and stays disabled.
