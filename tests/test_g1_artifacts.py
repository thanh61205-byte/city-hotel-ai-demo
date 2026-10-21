"""Integration checks for generated G1 handover and temporal provenance."""
import hashlib
import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/business_case"


@unittest.skipUnless((OUT / "g1_policy.json").exists(), "Run stage G1 to generate artifacts")
class G1ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = pd.read_csv(OUT / "action_queue_g1_2017.csv",
                              parse_dates=["decision_at", "arrival_date", "first_contact"])
        cls.schedule = pd.read_csv(OUT / "action_schedule_g1_2017.csv",
                                  parse_dates=["decision_at", "scheduled_at"])
        cls.policy = json.loads((OUT / "g1_policy.json").read_text())
        cls.keys = json.loads((OUT / "key_numbers.json").read_text())

    def test_f_identity_scores_and_tier_capacity(self):
        f = pd.read_csv(ROOT / "artifacts/modeling/action_queue_2017.csv", parse_dates=["score_time"])
        f = f.loc[f.track.eq("individual model")].set_index("booking_id")
        q = self.plan.set_index("booking_id")
        self.assertEqual(set(q.index), set(f.index))
        self.assertFalse(q.index.duplicated().any())
        np.testing.assert_allclose(q.p_cancel, f.loc[q.index, "p_cancel"], atol=1e-12)
        self.assertTrue(q.decision_at.eq(f.loc[q.index, "score_time"]).all())
        manifest = json.loads((ROOT / "artifacts/modeling/manifest.json").read_text())
        self.assertEqual(self.policy["stage_f_run"], manifest["run_id"])
        for _, batch in q.groupby("decision_at"):
            expected_high = int(np.floor(len(batch) * self.policy["policy"]["high_share"] + 1e-9))
            expected_contact = int(np.floor(len(batch) * self.policy["policy"]["contact_share"] + 1e-9))
            self.assertEqual(batch.tier_g1.eq("high").sum(), expected_high)
            self.assertEqual(batch.tier_g1.ne("low").sum(), expected_contact)

    def test_temporal_provenance_and_label_cutoffs(self):
        path = OUT / "rolling_selection"
        scores = pd.read_csv(path / "predictions.csv", parse_dates=["decision_at", "fit_cutoff", "selection_cutoff"])
        batches = pd.read_csv(path / "batches.csv", parse_dates=[
            "decision_at", "fit_cutoff", "max_train_arrival", "max_train_outcome_date"])
        self.assertTrue(scores.decision_at.gt(scores.fit_cutoff).all())
        self.assertTrue(scores.decision_at.gt(scores.selection_cutoff).all())
        self.assertTrue(batches.max_train_arrival.le(batches.fit_cutoff).all())
        self.assertTrue(batches.max_train_outcome_date.le(batches.fit_cutoff).all())
        self.assertTrue(scores.selection_cutoff.eq(pd.Timestamp("2015-12-31")).all())
        audit = json.loads((path / "audit.json").read_text())
        for name, digest in audit["output_sha256"].items():
            self.assertEqual(hashlib.sha256((path / name).read_bytes()).hexdigest(), digest)

    def test_dated_schedule_and_queue_agree(self):
        schedule = self.schedule
        q = self.plan.set_index("booking_id")
        self.assertTrue(schedule.scheduled_at.ge(schedule.decision_at).all())
        arrival = schedule.booking_id.map(q.arrival_date)
        self.assertTrue(schedule.scheduled_at.lt(arrival).all())
        calls = schedule.loc[schedule.action_type.eq("phone_call")]
        self.assertLessEqual(calls.groupby("scheduled_at").size().max(), self.policy["call_capacity_per_day"])
        self.assertTrue((arrival.loc[calls.index] - calls.scheduled_at).dt.days.ge(8).all())
        self.assertTrue((calls.scheduled_at - calls.decision_at).dt.days.lt(7).all())
        first = schedule.groupby("booking_id").scheduled_at.min().reindex(q.index)
        pd.testing.assert_series_equal(first, q.first_contact, check_names=False)
        counts = schedule.loc[schedule.action_type.ne("phone_call")].groupby("booking_id").size().reindex(q.index, fill_value=0)
        np.testing.assert_array_equal(counts, q.planned_messages)
        overflow = q.index[q.schedule_status.eq("manual_capacity_review")]
        self.assertFalse(schedule.booking_id.isin(overflow).any())

    def test_no_outcome_columns_in_handover(self):
        forbidden = {"is_canceled", "reservation_status_date", "expected_net_eur", "messages",
                     "net", "benefit", "actual_calls"}
        self.assertFalse(forbidden.intersection(self.plan.columns))
        self.assertFalse(forbidden.intersection(self.schedule.columns))
        reminders = self.plan.loc[self.plan.rule_g1.eq("I2")]
        np.testing.assert_array_equal(reminders.planned_messages,
                                      np.where(reminders.days_to_arrival.ge(15), 2, 1))

    def test_evaluation_costs_and_roi_reconcile(self):
        scenarios = pd.read_csv(OUT / "scenarios_2017.csv", index_col=0)
        evals = pd.read_csv(OUT / "retrospective_evaluation_2017.csv")
        row = scenarios.loc["S4 G1 policy"]
        for column, field in [("net", "net_eur"), ("cost", "variable_cost_eur"),
                              ("benefit", "benefit_eur"), ("actual_calls", "actual_calls")]:
            self.assertAlmostEqual(evals["retrospective_" + column].sum(), row[field], places=7)
        bc = pd.read_csv(OUT / "business_case.csv", index_col=0).loc["S4 G1 policy"]
        self.assertAlmostEqual(bc.net_3y_eur / bc.total_cost_3y_eur, bc.roi_3y)
        expected_cost = bc.one_off_cost_eur + 3 * (bc.running_cost_eur_per_year + bc.annual_variable_cost_eur)
        self.assertAlmostEqual(expected_cost, bc.total_cost_3y_eur)
        self.assertAlmostEqual(bc.roi_3y, self.keys["S4_roi_3y"])
        self.assertAlmostEqual(bc.annual_net_benefit_eur,
                               self.keys["call_capacity_check"]["S4_net_per_year_calls_from_decision_day"])

    def test_disabled_rules_and_documents_match(self):
        self.assertFalse(self.policy["overbooking"]["enabled"])
        self.assertFalse(self.keys["overbooking"]["enabled"])
        rules = pd.read_csv(OUT / "decision_rules_g1.csv").set_index("rule")
        self.assertFalse(rules.loc["G3", "enabled"])
        self.assertFalse(rules.loc["I6", "enabled"])
        self.assertTrue(rules.loc["G3", "disabled_reason"])
        kpi = pd.read_csv(ROOT / "docs/kpi_register.csv").set_index("kpi_id")
        roi_text = f"{self.keys['S4_roi_3y']:.1%}"
        self.assertIn(roi_text, kpi.loc["B7", "target"])
        self.assertIn("fixed running costs", kpi.loc["B7", "formula"])
        for name in ["README.md", "docs/G1_data_contract.md", "docs/decision_table_draft.md"]:
            self.assertIn(roi_text, (ROOT / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
