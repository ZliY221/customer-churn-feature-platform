import unittest

from churn_platform.campaign_conflicts import CampaignEnrollment, resolve_campaign_conflicts


class CampaignConflictTests(unittest.TestCase):
    def test_keeps_high_priority_campaign_and_defers_collision(self):
        result = resolve_campaign_conflicts([
            CampaignEnrollment("c-1", "renewal", 90, 100),
            CampaignEnrollment("c-1", "winback", 60, 110),
            CampaignEnrollment("c-1", "survey", 20, 140),
        ], cooldown_hours=24)
        self.assertEqual("CONFLICTS_RESOLVED", result["status"])
        self.assertEqual(["KEEP", "DEFER", "KEEP"],
                         [row["decision"] for row in result["decisions"]])
        self.assertFalse(result["automatic_contact_started"])

    def test_clear_when_customers_do_not_overlap(self):
        result = resolve_campaign_conflicts([
            CampaignEnrollment("c-1", "a", 50, 10),
            CampaignEnrollment("c-2", "b", 50, 10),
        ])
        self.assertEqual("CLEAR", result["status"])

    def test_rejects_duplicate_enrollment(self):
        with self.assertRaises(ValueError):
            resolve_campaign_conflicts([
                CampaignEnrollment("c", "a", 50, 10),
                CampaignEnrollment("c", "a", 40, 20),
            ])
