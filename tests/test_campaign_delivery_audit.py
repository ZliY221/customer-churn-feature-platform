import unittest

from churn_platform.campaign_delivery_audit import DeliveryAssignment, audit_campaign_delivery


class CampaignDeliveryAuditTests(unittest.TestCase):
    def test_flags_material_segment_gap(self):
        result = audit_campaign_delivery("campaign", [
            DeliveryAssignment("a-1", "NEW", "ROUTED", "SMS"),
            DeliveryAssignment("a-2", "NEW", "ROUTED", "EMAIL"),
            DeliveryAssignment("b-1", "LOYAL", "DEFERRED_CHANNEL"),
            DeliveryAssignment("b-2", "LOYAL", "ROUTED", "SMS"),
        ], maximum_rate_gap=0.2)
        self.assertEqual("REVIEW", result["status"])
        self.assertEqual(0.5, result["observed_rate_gap"])
        self.assertFalse(result["automatic_delivery_started"])

    def test_balanced_assignments_report_channel_counts(self):
        result = audit_campaign_delivery("campaign", [
            DeliveryAssignment("a", "A", "ROUTED", "IN_APP"),
            DeliveryAssignment("b", "B", "ROUTED", "IN_APP"),
        ])
        self.assertEqual("BALANCED", result["status"])
        self.assertEqual({"IN_APP": 1}, result["segments"][0]["channel_counts"])

    def test_rejects_routed_assignment_without_channel(self):
        with self.assertRaises(ValueError):
            audit_campaign_delivery("campaign", [DeliveryAssignment("a", "A", "ROUTED")])
