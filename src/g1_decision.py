"""Time-audited policy inputs and outcome-independent schedules for stage G1.

Snapshot features/population retain the limitations documented by stage F.
Only retrospective evaluation, never the planner, may inspect future outcomes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

import f_modeling as fm


def matured(frame, cutoff):
    """Conservative training labels: arrival AND recorded outcome known by cutoff."""
    cutoff = pd.Timestamp(cutoff)
    return frame.loc[
        frame["arrival_date"].le(cutoff)
        & frame["reservation_status_date"].le(cutoff)
        & frame["booking_date"].le(cutoff)
    ].copy()


def rolling_policy_scores(root: Path, settings: dict):
    """Select an estimator using 2015 only, then refit before each 2016 decision.

    Never imports the final F estimator/parameters selected with 2016 labels.
    All 2016 weekly batches are scored before their outcomes are read for policy
    selection. Unresolved labels at the policy freeze date are explicitly marked.
    """
    root = Path(root)
    paths = ["data/processed/modeling_dataset.csv", "data/processed/bookings_prepared.csv",
             "config/column_groups.json", "src/f_modeling.py", "src/g1_decision.py"]
    fingerprint = hashlib.sha256(json.dumps(settings, sort_keys=True).encode())
    for rel in paths:
        fingerprint.update((root / rel).read_bytes())
    signature = fingerprint.hexdigest()
    cache = root / "artifacts/business_case/rolling_selection"
    cache.mkdir(parents=True, exist_ok=True)
    audit_path = cache / "audit.json"
    if audit_path.exists():
        audit = json.loads(audit_path.read_text())
        if audit.get("signature") == signature:
            hashes = audit.get("output_sha256", {})
            if hashes and all((cache / name).exists() and
                              hashlib.sha256((cache / name).read_bytes()).hexdigest() == digest
                              for name, digest in hashes.items()):
                return pd.read_csv(cache / "predictions.csv", parse_dates=[
                    "decision_at", "fit_cutoff", "selection_cutoff"]), audit

    groups = json.loads((root / "config/column_groups.json").read_text())
    numeric = [x for x in groups["individual_model"]["numeric"]
               if x not in {"adr_clean", "adr_vs_ref", "adr_per_guest", "is_free_rate"}]
    categorical = [x for x in groups["individual_model"]["categorical"] if x != "agent_top"]
    features = numeric + categorical + ["agent_id"]
    frame = pd.read_csv(root / "data/processed/modeling_dataset.csv",
                        parse_dates=["booking_date", "arrival_date"])
    raw = pd.read_csv(root / "data/processed/bookings_prepared.csv",
                     usecols=["booking_id", "agent_id", "reservation_status", "reservation_status_date"],
                     parse_dates=["reservation_status_date"], dtype={"agent_id": str})
    frame = frame.merge(raw, on="booking_id", validate="one_to_one")
    frame = frame.loc[frame.population.eq("individual")].copy()
    frame["decision_at"] = fm.week_end(frame.booking_date)
    selection_cutoff = pd.Timestamp(settings["model_selection_cutoff"])
    train_cutoff = pd.Timestamp(settings["validation_train_cutoff"])
    train = matured(frame, train_cutoff)
    validation = matured(frame, selection_cutoff)
    validation = validation.loc[validation.decision_at.gt(train_cutoff)
                                & validation.decision_at.le(selection_cutoff)].copy()
    validation = validation.loc[fm.eligibility(validation, validation.decision_at).eligible_known_checks]
    if train.is_canceled.nunique() != 2 or validation.is_canceled.nunique() != 2:
        raise ValueError("Insufficient matured 2015 labels for temporal model selection")

    candidates = []
    for spec in settings["candidates"]:
        pipe = fm.build_pipeline(spec["model"], numeric, categorical, spec["params"], seed=42)
        fm.fit_pipeline(pipe, train, features, train_cutoff)
        probability = pipe.predict_proba(validation[features])[:, 1]
        candidates.append({**spec, "log_loss_2015": float(log_loss(validation.is_canceled, probability))})
    selected = min(candidates, key=lambda x: x["log_loss_2015"])
    freeze = pd.Timestamp(settings["policy_selection_cutoff"])
    queued = frame.loc[frame.decision_at.between("2016-01-01", freeze)].copy()
    queued = queued.loc[fm.eligibility(queued, queued.decision_at).eligible_known_checks]
    outputs, batches = [], []
    for decision_at, batch in queued.groupby("decision_at", sort=True):
        cutoff = decision_at - pd.Timedelta(days=1)
        if decision_at <= selection_cutoff:
            raise ValueError("Model selection was not complete before decision")
        training = matured(frame, cutoff)
        pipe = fm.build_pipeline(selected["model"], numeric, categorical, selected["params"], seed=42)
        fm.fit_pipeline(pipe, training, features, cutoff)
        probability = pipe.predict_proba(batch[features])[:, 1]
        resolved = batch.arrival_date.le(freeze) & batch.reservation_status_date.le(freeze)
        outputs.append(pd.DataFrame({
            "booking_id": batch.booking_id.to_numpy(), "p_cancel": probability,
            "decision_at": decision_at, "fit_cutoff": cutoff,
            "selection_cutoff": selection_cutoff, "outcome_known_at_policy_freeze": resolved.to_numpy(),
        }))
        batches.append({"decision_at": str(decision_at.date()), "fit_cutoff": str(cutoff.date()),
                        "train_rows": len(training), "score_rows": len(batch),
                        "max_train_arrival": str(training.arrival_date.max().date()),
                        "max_train_outcome_date": str(training.reservation_status_date.max().date())})
        print(f"G1 rolling {decision_at.date()}: train={len(training)}, queue={len(batch)}", flush=True)
    scores = pd.concat(outputs, ignore_index=True)
    assert scores.decision_at.gt(scores.fit_cutoff).all()
    assert scores.decision_at.gt(scores.selection_cutoff).all()
    scores.to_csv(cache / "predictions.csv", index=False)
    pd.DataFrame(batches).to_csv(cache / "batches.csv", index=False)
    audit = {
        "signature": signature, "selected_model": selected, "candidates": candidates,
        "model_selection_cutoff": str(selection_cutoff.date()),
        "policy_selection_cutoff": str(freeze.date()), "validation_rows": len(validation),
        "scored_rows": len(scores), "batches": len(batches),
        "unresolved_outcomes_at_policy_freeze": int((~scores.outcome_known_at_policy_freeze).sum()),
        "limitation": "Retrospective snapshot features and population proxy inherited from F; "
                      "historical model selected on 2015 differs from final F model selected on 2016.",
        "output_sha256": {name: hashlib.sha256((cache / name).read_bytes()).hexdigest()
                          for name in ["predictions.csv", "batches.csv"]},
    }
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return scores, audit


def contact_dates(queue, tiers, capacity=20, start_day=0, window_days=7):
    """Dates depend only on known dates, scores, tiers and immutable tie-breaks.

    Capacity is booking-level call tasks (average attempts costed per task).
    An unschedulable I3 stays pending manual review; it is never silently contacted.
    """
    if capacity < 1 or not 0 <= start_day < window_days:
        raise ValueError("Invalid capacity or call window")
    q = queue
    tiers = np.asarray(tiers)
    result = pd.DataFrame(index=q.index)
    result["rule"] = [fm.plan_rule(t, int(d))[0] for t, d in zip(tiers, q.days)]
    result["call_at"] = pd.Series(pd.NaT, index=q.index, dtype="datetime64[ns]")
    selected = q.loc[result.rule.eq("I3"), ["booking_id", "decision_at", "arrival_date", "p_x_gross"]].copy()
    selected["_tie"] = fm.id_tie_breaker(selected.booking_id)
    selected = selected.sort_values(["decision_at", "p_x_gross", "_tie", "booking_id"],
                                    ascending=[True, False, True, True])
    used = {}
    for row in selected.itertuples():
        for offset in range(start_day, window_days):
            day = row.decision_at + pd.Timedelta(days=offset)
            if (row.arrival_date - day).days < 8:
                break
            if used.get(day, 0) < capacity:
                result.at[row.Index, "call_at"] = day
                used[day] = used.get(day, 0) + 1
                break
    rem = result.rule.eq("I2")
    result["reminder_1_at"] = (q.arrival_date - pd.Timedelta(days=30)).where(q.days.ge(30), q.decision_at).where(rem)
    result["reminder_2_at"] = (q.arrival_date - pd.Timedelta(days=14)).where(rem & q.days.ge(15))
    result["planned_messages"] = result[["reminder_1_at", "reminder_2_at"]].notna().sum(axis=1)
    result["schedule_status"] = np.where(result.rule.eq("I3") & result.call_at.isna(),
                                         "manual_capacity_review", "scheduled")
    result.loc[result.rule.eq("I5"), "schedule_status"] = "buffer_only"
    return result


def handover_plan(queue, tiers, capacity=20, start_day=0, window_days=7):
    dates = contact_dates(queue, tiers, capacity, start_day, window_days)
    plan = queue[["booking_id", "decision_at", "booking_date", "arrival_date", "days",
                  "market_segment", "gross_value", "p", "p_x_gross"]].copy()
    plan["tier_g1"] = np.asarray(tiers)
    plan["rule_g1"] = dates.rule
    plan["schedule_status"] = dates.schedule_status
    actions = []
    for row in plan.itertuples():
        if row.rule_g1 == "I3":
            day = dates.at[row.Index, "call_at"]
            if pd.notna(day):
                actions.append({"booking_id": row.booking_id, "decision_at": row.decision_at,
                                "scheduled_at": day, "rule": "I3", "action_type": "phone_call",
                                "schedule_reason": "capacity-limited call task; recheck status at execution",
                                "message_no": 1})
        else:
            actions.extend(fm.schedule_actions(row.booking_id, row.decision_at, row.arrival_date, row.rule_g1))
    schedule = pd.DataFrame(actions, columns=["booking_id", "decision_at", "scheduled_at", "rule",
                                            "action_type", "schedule_reason", "message_no"])
    first = schedule.groupby("booking_id").scheduled_at.min()
    message_count = schedule.loc[schedule.action_type.ne("phone_call")].groupby("booking_id").size()
    plan["first_contact"] = plan.booking_id.map(first)
    plan["planned_messages"] = plan.booking_id.map(message_count).fillna(0).astype(int)
    plan["consent_status"] = "unknown"
    plan["open_action_check"] = "unknown"
    plan = plan.rename(columns={"days": "days_to_arrival", "p": "p_cancel",
                                "p_x_gross": "expected_revenue_at_risk"})
    return plan, schedule


def programme_case(annual_net, annual_variable, one_off, annual_running, years):
    """Total-programme ROI: net after ALL costs divided by ALL costs."""
    total_cost = one_off + years * (annual_running + annual_variable)
    net = years * (annual_net - annual_running) - one_off
    return {"annual_net_benefit_eur": annual_net, "annual_variable_cost_eur": annual_variable,
            "running_cost_eur_per_year": annual_running, "one_off_cost_eur": one_off,
            "year1_net_eur": annual_net - annual_running - one_off, f"net_{years}y_eur": net,
            f"total_cost_{years}y_eur": total_cost, f"roi_{years}y": net / total_cost if total_cost else np.nan,
            "payback_months": one_off / ((annual_net - annual_running) / 12)
            if annual_net > annual_running else np.nan}


if __name__ == "__main__":
    # Preparing scores in a separate process releases training allocations before
    # the notebook loads its finance and room-night tables (important on low RAM).
    import yaml
    project = Path(__file__).resolve().parents[1]
    assumptions = yaml.safe_load((project / "config/assumptions.yaml").read_text(encoding="utf-8"))
    _, audit = rolling_policy_scores(project, assumptions["g1"]["rolling_selection"])
    print(f"Rolling selection ready: {audit['scored_rows']} bookings, {audit['batches']} batches")
