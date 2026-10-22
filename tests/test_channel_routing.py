import unittest

from churn_platform.channel_routing import (
    ChannelCandidate,
    ChannelCapacity,
    route_campaign_channels,
)


class ChannelRoutingTests(unittest.TestCase):
    def test_routes_by_risk_preference_and_capacity(self):
        result = route_campaign_channels("oct-retention", [
            ChannelCandidate("c-1", 0.9, ("SMS", "EMAIL")),
            ChannelCandidate("c-2", 0.8, ("SMS", "EMAIL")),
            ChannelCandidate("c-3", 0.7, ("SMS",)),
        ], [
            ChannelCapacity("SMS", 1, 0.6),
            ChannelCapacity("EMAIL", 1, 0.4),
        ])
        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual(["SMS", "EMAIL", None], [row["channel"] for row in result["assignments"]])
        self.assertEqual(1, result["deferred_count"])
        self.assertFalse(result["automatic_contact_started"])

    def test_higher_response_channel_wins_within_preferences(self):
        result = route_campaign_channels("campaign", [
            ChannelCandidate("c-1", 0.5, ("EMAIL", "SMS")),
        ], [ChannelCapacity("EMAIL", 2, 0.2), ChannelCapacity("SMS", 2, 0.7)])
        self.assertEqual("SMS", result["assignments"][0]["channel"])

    def test_duplicate_channel_is_rejected(self):
        with self.assertRaises(ValueError):
            route_campaign_channels("campaign", [ChannelCandidate("c-1", 0.5, ("SMS",))], [
                ChannelCapacity("SMS", 1, 0.5), ChannelCapacity("SMS", 2, 0.6),
            ])
