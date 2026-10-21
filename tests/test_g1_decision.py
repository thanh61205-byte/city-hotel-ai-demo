import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import g1_decision as gd


def queue(n=45):
    q = pd.DataFrame({"booking_id": np.arange(1, n + 1),
                      "decision_at": pd.Timestamp("2017-01-01"),
                      "booking_date": pd.Timestamp("2016-12-30"),
                      "arrival_date": pd.Timestamp("2017-02-06"), "days": 36,
                      "market_segment": "Online TA", "gross_value": 300.,
                      "p": .5, "p_x_gross": 150.,
                      "is_canceled": 0, "reservation_status_date": pd.Timestamp("2017-02-07")})
    return q


class G1DecisionTests(unittest.TestCase):
    def test_future_outcomes_do_not_change_handover(self):
        q = queue()
        tiers = np.array(["medium"] * 25 + ["high"] * 20)
        before = gd.handover_plan(q, tiers)
        q["is_canceled"] = 1
        q["reservation_status_date"] = pd.Timestamp("2017-01-06")
        q["syn_net_booking_value"] = -999999
        after = gd.handover_plan(q, tiers)
        for a, b in zip(before, after):
            pd.testing.assert_frame_equal(a, b)
        self.assertTrue(before[0].loc[:24, "planned_messages"].eq(2).all())

    def test_capacity_is_global_and_stable_under_shuffle(self):
        q = queue(150)
        q.loc[100:, "decision_at"] = pd.Timestamp("2017-01-02")
        plan, schedule = gd.handover_plan(q, ["high"] * len(q))
        self.assertLessEqual(schedule.groupby("scheduled_at").size().max(), 20)
        shuffled = q.sample(frac=1, random_state=17)
        other = gd.handover_plan(shuffled, ["high"] * len(q))[0]
        pd.testing.assert_frame_equal(plan.set_index("booking_id").sort_index(),
                                      other.set_index("booking_id").sort_index())
        self.assertTrue(schedule.scheduled_at.ge(schedule.decision_at).all())
        self.assertTrue((schedule.scheduled_at - schedule.decision_at).dt.days.lt(7).all())

    def test_short_window_overflow_goes_to_manual_review(self):
        q = queue(45)
        q["arrival_date"] = pd.Timestamp("2017-01-09")
        q["days"] = 8
        plan, schedule = gd.handover_plan(q, ["high"] * len(q))
        self.assertEqual(len(schedule), 20)
        self.assertEqual(plan.schedule_status.eq("manual_capacity_review").sum(), 25)
        self.assertTrue(plan.loc[plan.schedule_status.eq("manual_capacity_review"), "first_contact"].isna().all())

    def test_reminder_boundaries_match_f_contract(self):
        q = queue(6)
        q["days"] = [7, 8, 14, 15, 30, 36]
        q["arrival_date"] = q.decision_at + pd.to_timedelta(q.days, "D")
        plan, schedule = gd.handover_plan(q, ["medium"] * 6)
        self.assertEqual(plan.planned_messages.tolist(), [1, 1, 1, 2, 2, 2])
        dates = gd.contact_dates(q, ["medium"] * 6)
        for i in range(1, 6):
            expected = [x for x in [dates.at[i, "reminder_1_at"], dates.at[i, "reminder_2_at"]] if pd.notna(x)]
            self.assertEqual(schedule.loc[schedule.booking_id.eq(i + 1), "scheduled_at"].tolist(), expected)

    def test_training_requires_both_arrival_and_outcome_known(self):
        q = queue(4)
        q["arrival_date"] = pd.to_datetime(["2016-12-31", "2017-01-01", "2016-12-29", "2016-12-28"])
        q["reservation_status_date"] = pd.to_datetime(["2016-12-31", "2016-12-30", "2017-01-01", None])
        self.assertEqual(gd.matured(q, "2016-12-31").booking_id.tolist(), [1])

    def test_total_programme_roi_includes_variable_cost_once(self):
        actual = gd.programme_case(100, 20, 30, 10, 3)
        self.assertEqual(actual["net_3y_eur"], 240)
        self.assertEqual(actual["total_cost_3y_eur"], 120)
        self.assertEqual(actual["roi_3y"], 2)
        self.assertEqual(actual["year1_net_eur"], 60)

    def test_nonpositive_cash_has_no_payback(self):
        self.assertTrue(np.isnan(gd.programme_case(5, 2, 30, 10, 3)["payback_months"]))


if __name__ == "__main__":
    unittest.main()
