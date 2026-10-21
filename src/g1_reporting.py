"""Synchronise G1 documentation with generated results, preserving other stages."""
from pathlib import Path
import re

import pandas as pd


def block(text, name, content):
    start, end = f"<!-- {name}:START -->", f"<!-- {name}:END -->"
    new = f"{start}\n{content.strip()}\n{end}"
    if start in text:
        return re.sub(re.escape(start) + r".*?" + re.escape(end),
                      lambda _: new, text, flags=re.S)
    return text.rstrip() + "\n\n" + new + "\n"


def update_documents(root, key, business_case, pilot, break_even, plan, schedule, assumptions):
    root = Path(root)
    p = key["chosen_policy"]
    annual, roi, payback = key["S4_net_per_year"], key["S4_roi_3y"], key["S4_payback_months"]
    summary = (
        f"Call tier: top {p['high_share']:.0%} by `{p['high_rank']}`; total call + reminder tier: "
        f"{p['contact_share']:.0%} of each weekly batch. Scores and dates come from the current F run. "
        f"The 2016 selection uses weekly rolling predictions, with estimator selection on matured "
        f"2015 labels and policy outcomes frozen at 2016-12-31. "
        f"Call tasks start on the decision day, at most {assumptions['call_capacity_per_day']['value']} "
        f"booking-level tasks/day within 7 calendar days, and at least 8 days before arrival. "
        f"Overflow is sent to manual review; task costs include average attempts.\n\n"
        f"2017 queue: **{len(plan):,} bookings**. Capacity-aware S4 net benefit after variable intervention "
        f"costs: **EUR {annual:,.0f}/year**; total-programme 3-year ROI: **{roi:.1%}**; "
        f"payback: **{payback:.1f} months**. ROI = net after all costs / all variable intervention, "
        f"one-off and running costs over the same horizon. These are simulated values, not measured savings.\n\n"
        "Handover: `artifacts/business_case/action_queue_g1_2017.csv`, "
        "`action_schedule_g1_2017.csv`, `g1_policy.json`. Outcome-dependent contacts and net values are "
        "isolated in `retrospective_evaluation_2017.csv`. "
        "**G3 overbooking is disabled**, including in the machine-readable rules and policy. "
        "I6 deposits remain disabled by default. Consent, current status, responses, open actions and "
        "live capacity must be checked at execution.\n\n"
        "Limitations: snapshot features/population proxy inherited from F; unresolved outcomes are omitted "
        "from the 2016 objective, while full queues determine tiers/capacity; historical and final F "
        "estimators differ. Confirm policy transfer and guest response in the randomised pilot. "
        "The separate overbooking experiment remains exploratory and cannot authorise execution.\n\n"
        "Run G1: `python src/run_notebooks.py --stage G1`. See `docs/G1_data_contract.md`. "
        "**G2 runs on this policy:** `dashboard/g1_contract.py` re-derives the G1 tiers from F scores, checks the dated "
        "schedule (milestones, call window, daily capacity) and binds the G2 state DB to F run + G1 policy; "
        "manual-review calls get no automatic action. See `docs/G2_data_contract.md`."
    )
    path = root / "README.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "Provisional risk × value × time → action table, eligibility, owners, capacity limits, approvals, stop conditions and the approval-guard specification for G2: `docs/decision_table_draft.md`. Thresholds are placeholders until F2/G1.",
        "G1 policy, dated capacity-aware actions, owners and stop conditions: `docs/decision_table_draft.md` "
        "and `artifacts/business_case/`. See the generated G1 results below; the G2 dashboard runs on this policy.")
    text = text.replace("| Expected business value | Partial | Benchmarks restated with their basis; final ROI in G1 |",
                        "| Expected business value | Done (simulated G1) | Notebook 05; generated results below; effects require pilot confirmation |")
    text = text.replace("Run notebooks in order **01 → 02 → 03 → 04**", "Run notebooks in order **01 → 02 → 03 → 04 → 05**")
    path.write_text(block(text, "G1_RESULTS", "## G1 — generated policy and results\n\n" + summary), encoding="utf-8")

    path = root / "docs/decision_table_draft.md"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"Status: .*?\n", "Status: **G1 simulated policy and dated handover**. "
                  "F defaults remain a reference; the generated G1 policy below governs the new handover. "
                  "G2 runs on it; the pilot remains required.\n", text, count=1)
    text = text.replace("`p_cancel` × booking contribution (`syn_net_booking_value`; gross value as a check)",
                        "`p_cancel` × `gross_value` for G1 call ranking; contribution basis used separately for simulated benefits")
    text = text.replace("I3 is scheduled at the decision time (attempt 1 of max 2); I5 creates no guest action.",
                        "F's default schedules I3 at the decision time. G1 replaces that date with its capacity-aware call-task schedule; "
                        "I5 creates no guest action. Use the G1 queue and schedule together.")
    text = text.replace("`t_low`, `t_high`, `N` and `D` are **provisional** placeholders.",
                        "`t_low`/`t_high` denote rank-based tiers, not fixed probabilities. "
                        "The paragraph below documents F defaults; current G1 settings are in the generated policy section.")
    text = text.replace("I5 (≤ 7 days) overrides I2/I3.", "For the high tier, I5 (≤ 7 days) overrides I3; medium/low bookings at ≤ 7 days use I1.")
    text = text.replace("| G1 | Block with release deadline within `D` days and low pickup |",
                        "| G1 | Group-rule p_cancel >= 0.5 and > 30 days to arrival at decision; confirm/release ask at arrival minus 30 days |")
    text = text.replace("| Capacity | Calls / tasks the reservations team can handle per day |",
                        "| Capacity | Booking-level call tasks per day; average attempts included in task cost |")
    text = text.replace("| G3 | High forecast cancellations and high demand on a date × room type |",
                        "| G3 | DISABLED: exploratory analysis only |")
    text = text.replace("Provisional limit per room type and night; stop if relocations occur",
                        "Disabled in G1; staff approval alone does not enable this rule")
    path.write_text(block(text, "G1_POLICY", "## G1 policy replacing the F default shares\n\n" + summary), encoding="utf-8")

    path = root / "docs/kpi_register.csv"
    kpi = pd.read_csv(path, keep_default_na=False).set_index("kpi_id")
    kpi.loc["B6", "target"] = f"Positive ITT net benefit; simulated G1 reference EUR {annual:,.0f}/year after variable costs; verify in pilot"
    kpi.loc["B6", "target_status"] = "G1 simulation; causal effect not yet measured"
    kpi.loc["B7", "formula"] = (
        "(incremental contribution - variable intervention costs - allocated one-off costs - fixed running costs) "
        "/ (variable intervention costs + allocated one-off costs + fixed running costs), all over the SAME horizon; "
        "B6 already subtracts variable costs, so do not subtract them twice")
    kpi.loc["B7", "period"] = "3-year planning horizon; pilot-period ROI reported separately with allocated fixed costs"
    kpi.loc["B7", "population_cohort"] = "programme; incremental contribution estimated by randomised assigned arm (ITT)"
    kpi.loc["B7", "target"] = f"> 0; simulated 3-year G1 reference {roi:.1%}; not a measured pilot ROI"
    kpi.loc["B7", "target_status"] = "G1 total-programme cost definition"
    kpi.loc["B7", "data_source"] = "pilot_assignment, interventions, fin_booking, programme cost ledger, assumptions.yaml"
    for name, label, metric in [("M3", "precision", "precision"), ("M4", "lift", "lift")]:
        old = kpi.loc[name, "target"].split(" G1 rolling reference:")[0]
        old = old.replace("an absolute target for the queue population must be reset in G1",
                          "queue-specific references are reported separately")
        scores = key["queue_kpi_top20"]
        kpi.loc[name, "target"] = old + (
            f" G1 rolling reference: {label} {scores[metric + '_2016']:.3f} "
            f"(resolved outcomes in the 2016 queue); current F 2017 queue {scores[metric + '_2017']:.3f}. "
            "Different estimators/cohorts: monitor by season; do not interpret as a matched comparison.")
    kpi.loc["P1", "target"] = "Contact scheduled priority tasks before their deadlines; report manual overflow and stopped/cancelled tasks separately"
    kpi.loc["P2", "target"] = "I3: within 7 calendar days from decision and >= 8 days before arrival; I2: exported milestones"
    kpi.loc[["P1", "P2"], "target_status"] = "G1 dated schedule; live capacity/consent/status checked in G2"
    call = pilot.loc[("Calls (S4 call tier)", 52)]
    rem = pilot.loc[("Reminders (S4 reminder tier)", 52)]
    kpi.loc["C1", "target"] = (
        f"Positive ITT effect with confidence intervals; assumed call effect {100 * call.assumed_effect_points:.3f} percentage points, "
        f"reminder effect {100 * rem.assumed_effect_points:.3f} points. "
        f"Required weeks under assumptions: calls {call.weeks_to_detect_assumed_effect:.1f}, reminders {rem.weeks_to_detect_assumed_effect:.1f}. "
        "Use B6 and CX guard-rails when pilot is underpowered; never claim significance from the forecast.")
    kpi.loc["C1", "target_status"] = "G1 power calculation; requires randomised holdout"
    kpi.to_csv(path)

    (root / "docs/G1_data_contract.md").write_text(
        "# G1 handover contract (schema 2)\n\n" + summary + "\n\n"
        "## Files and invariants\n\n"
        "- Queue: one row per eligible F individual booking; F score/decision date retained. "
        "`planned_messages` counts the initial I1/I2 plan; it is never reduced using a future cancellation. "
        "`first_contact` is the first dated action, blank for buffer/manual-overflow rows. "
        "No outcome-conditioned `expected_net_eur` or `messages` field is exported in the queue.\n"
        "- Schedule: booking ID, decision date, scheduled date, rule, action type and sequence. "
        "No date before decision or on/after arrival. I3 booking tasks obey the global daily cap and call window. "
        "An I3 with `manual_capacity_review` has no executable action. One call task may contain up to two "
        "attempts; actual staffing time and retry handling require execution-level checks.\n"
        "- Retrospective evaluation: actual-outcome-conditioned simulated contacts, costs and benefits. "
        "This file is evaluation-only and must not feed initial scheduling or live dispatch.\n"
        "- Policy: binds the F run, G1 shares/ranking, call schedule, rolling selection provenance, "
        "ROI definition, execution checks and `overbooking.enabled=false`.\n"
        "- Rolling selection: predictions, model/label cutoffs and SHA256-checked cache. "
        "Changing source inputs, settings or helper code invalidates the cache.\n"
        "- Regeneration updates README, decision table and KPI references from the same results. "
        "Do not combine files from different runs.\n\n"
        "## Evaluation boundaries\n\n"
        "2016 queues include all eligible weekly bookings so ranks and capacity are not filtered using future outcomes. "
        "Only labels resolved by 2016-12-31 contribute to policy value; long-lead bookings are underrepresented in that objective. "
        "The historical estimator is selected on 2015 data and cannot use final-F hyperparameters tuned on 2016. "
        "The 2017 queue uses final F and evaluates the frozen policy. F's snapshot/population limitations remain. "
        "Overbooking uses a separate exploratory retrospective experiment and stays disabled.\n",
        encoding="utf-8")
