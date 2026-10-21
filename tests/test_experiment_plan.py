import unittest

from churn_platform.experiment_plan import plan_retention_experiment


class RetentionExperimentPlanTests(unittest.TestCase):
    def test_plan_reserves_holdout_and_reports_capacity_gap(self):
        result = plan_retention_experiment(
            eligible_customers=2000, holdout_ratio=0.2,
            daily_treatment_capacity=200, campaign_days=5,
        )
        self.assertEqual(400, result["holdout_count"])
        self.assertEqual(1600, result["treatment_count"])
        self.assertEqual(600, result["capacity_gap"])
        self.assertEqual("CAPACITY_GAP", result["status"])

    def test_plan_flags_sample_that_cannot_support_a_control_group(self):
        result = plan_retention_experiment(eligible_customers=150, minimum_holdout=100)
        self.assertEqual("INSUFFICIENT_SAMPLE", result["status"])
        self.assertTrue(result["holdout_protected"])

    def test_plan_rejects_unsafe_holdout_ratio(self):
        with self.assertRaises(ValueError):
            plan_retention_experiment(eligible_customers=1000, holdout_ratio=0.75)
