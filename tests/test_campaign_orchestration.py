import unittest

from churn_platform.campaign_orchestration import CampaignCandidate, orchestrate_campaign


class CampaignOrchestrationTests(unittest.TestCase):
    def test_orchestration_protects_holdout_and_bounds_contact_queue(self):
        candidates = [
            CampaignCandidate(f"c-{index}", risk_score=1 - index / 10,
                              contact_allowed=index != 5)
            for index in range(6)
        ]
        result = orchestrate_campaign("retention-oct", candidates,
                                      holdout_ratio=0.2, treatment_capacity=2)
        self.assertEqual(1, result["counts"]["HOLDOUT"])
        self.assertEqual(2, result["counts"]["CONTACT_READY"])
        self.assertEqual(1, result["counts"]["EXCLUDED"])
        self.assertEqual(2, result["counts"]["DEFERRED_CAPACITY"])
        self.assertEqual("PARTIAL", result["status"])
        self.assertFalse(result["automatic_contact_started"])

    def test_orchestration_is_deterministic(self):
        candidates = [CampaignCandidate(f"c-{index}", 0.5, True) for index in range(10)]
        first = orchestrate_campaign("stable", candidates)
        second = orchestrate_campaign("stable", list(reversed(candidates)))
        self.assertEqual(first, second)

    def test_duplicate_customer_is_rejected(self):
        with self.assertRaises(ValueError):
            orchestrate_campaign("campaign", [
                CampaignCandidate("same", 0.5, True),
                CampaignCandidate("same", 0.8, True),
            ])
