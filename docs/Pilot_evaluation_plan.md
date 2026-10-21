> Current model and policy: `artifacts/modeling/manifest.json` and `artifacts/business_case/g1_policy.json`. This document defines a future pilot; no real hotel pilot has been completed.

# Pilot evaluation: distinguish planned effects from observed effects

Status: a plan, not a completed pilot or evidence that interventions work. Existing `cx_contact` is synthetic, outcome-dependent and subject to survivorship bias; it cannot estimate the effect of contacting guests.

Current conditional scenarios are produced by notebook 05 (`artifacts/business_case/`) and `python src/scenario_capacity.py` (`artifacts/scenarios_capacity/`). Their effects and costs are assumptions in `config/assumptions.yaml`; sensitivity and break-even outputs do not establish causal treatment effects.

## Before collecting outcomes

1. Define one real decision time, the individual eligibility rule, active-booking filter, contact opt-out rules and deduplication of open actions. Resolve snapshot feature availability with the hotel's PMS history.
2. Freeze the model version, scoring inputs, queue capacity and one intervention policy. A 20% retrospective arrival-month selection is not an operational daily queue.
3. Choose the population to which the effect will apply: eligible priority bookings, not all guests. Randomize these bookings to the new policy or usual handling before any intervention, stratifying by risk band and arrival period. If several bookings belong to the same party, assign them together to avoid contamination.
4. Define the primary outcome, a minimum business-relevant effect, required sample size and follow-up period before starting. Those values require the hotel's baseline, volume and costs; they are not filled with invented pilot data.

## Records

Use existing `pilot_assignment` for assignment and `interventions` for action logs. Record assignment time, model/run version, score time, frozen score, eligibility snapshot, assigned arm, actual contact attempts, response, intervention cost, outcome maturity and realized contribution. Extend the operational schema for missing fields before launch; current tables alone do not capture this complete contract.

## Analysis

Compare cancellation/stay rates by assigned arm, including failed contacts and non-responders in their original arm. Report effect size and uncertainty, not only whether a point estimate is favorable. If randomization is at party level, account for that clustering. Compare incremental net contribution after contact costs, discounts, retained deposits and resale effects without double-counting revenue. Report complaints and opt-outs as guardrails. Do not infer causality from before/after rates or contacted-versus-uncontacted comparisons.

## What the course project can deliver without a real hotel pilot

Deliver this protocol and an explicitly labeled scenario model. Compare a declared baseline, simple-rule targeting and model targeting at equal capacity; include zero-effect, unfavorable and favorable assumptions, cost sensitivity and break-even conditions. State all numerical effect and cost assumptions and source or justify them before interpreting their plausibility. Such a model answers “under these assumptions, would the program pay off?” It does not answer “did contacting guests reduce cancellations?” No observed intervention improvement is claimed in the final offline F1–F3 results.
