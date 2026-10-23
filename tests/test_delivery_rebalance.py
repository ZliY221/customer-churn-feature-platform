import unittest

from churn_platform.delivery_rebalance import SegmentDeliveryState, plan_delivery_rebalance


class DeliveryRebalanceTests(unittest.TestCase):
    def test_prioritises_lowest_rate_with_bounded_capacity(self):
        result = plan_delivery_rebalance("campaign", [
            SegmentDeliveryState("NEW", 100, 80, 10),
            SegmentDeliveryState("LOYAL", 100, 40, 30),
            SegmentDeliveryState("RETURNING", 100, 60, 20),
        ], reserve_capacity=25, target_rate_gap=0.1)
        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual(25, result["used_capacity"])
        self.assertEqual(25, result["actions"][0]["recommended_additional_routes"])
        self.assertFalse(result["automatic_rerouting_started"])

    def test_reports_balanced_without_using_capacity(self):
        result = plan_delivery_rebalance("campaign", [
            SegmentDeliveryState("A", 10, 8, 2),
            SegmentDeliveryState("B", 10, 7, 3),
        ], reserve_capacity=5, target_rate_gap=0.1)
        self.assertEqual("BALANCED", result["status"])
        self.assertEqual(0, result["used_capacity"])

    def test_rejects_reserve_candidates_above_deferred_count(self):
        with self.assertRaises(ValueError):
            plan_delivery_rebalance("campaign", [
                SegmentDeliveryState("A", 10, 9, 2),
            ], reserve_capacity=1)
