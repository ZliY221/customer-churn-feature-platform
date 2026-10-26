import unittest

from churn_platform.retention_value_plan import (
    RetentionValueCandidate,
    plan_retention_value,
)


class RetentionValuePlanTests(unittest.TestCase):
    def test_plan_prioritises_positive_adjusted_value_within_budget(self) -> None:
        result = plan_retention_value("renewal", [
            RetentionValueCandidate("c-1", 0.9, 0.20, 0.03, 1000, 30, True, "sms"),
            RetentionValueCandidate("c-2", 0.8, 0.10, 0.02, 500, 50, True, "phone"),
            RetentionValueCandidate("c-3", 0.7, 0.20, 0.02, 800, 20, False, "email"),
        ], campaign_budget=40, maximum_offers=2)
        self.assertEqual("RETENTION_VALUE_PLAN_READY", result["status"])
        self.assertEqual(["c-1"], [item["customer_key"] for item in result["selected"]])
        self.assertEqual(30, result["planned_cost"])
        self.assertFalse(result["automatic_contact_started"])

    def test_uncertain_or_unprofitable_offers_are_excluded(self) -> None:
        result = plan_retention_value("renewal", [
            RetentionValueCandidate("c-1", 0.8, 0.03, 0.03, 1000, 1, True, "sms"),
            RetentionValueCandidate("c-2", 0.8, 0.10, 0.01, 100, 20, True, "sms"),
        ], campaign_budget=100, maximum_offers=2)
        reasons = {item["customer_key"]: item["exclusion_reasons"] for item in result["deferred"]}
        self.assertIn("UPLIFT_BELOW_THRESHOLD", reasons["c-1"])
        self.assertIn("NON_POSITIVE_EXPECTED_VALUE", reasons["c-2"])

    def test_duplicate_customer_keys_are_rejected(self) -> None:
        candidate = RetentionValueCandidate("c-1", 0.8, 0.1, 0.01, 500, 10, True, "sms")
        with self.assertRaises(ValueError):
            plan_retention_value("x", [candidate, candidate], campaign_budget=100, maximum_offers=2)
