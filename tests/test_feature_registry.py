from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import unittest

from churn_platform.feature_registry import FeatureRegistry, compare_feature_sets


ROOT = Path(__file__).parents[1]


class FeatureRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FeatureRegistry.from_file(ROOT / "config" / "feature-registry.json")
        self.row = {
            "customer_key": "cus_0001",
            "as_of_time": "2026-10-01T00:00:00Z",
            "events_7d": 2,
            "events_30d": 8,
            "active_days_30d": 4,
            "login_count_30d": 2,
            "product_view_count_30d": 3,
            "cart_add_count_30d": 1,
            "order_count_30d": 1,
            "support_ticket_count_30d": 1,
            "revenue_30d": "58.30",
            "days_since_last_event": 1,
        }

    def test_registry_matches_model_feature_contract(self) -> None:
        document = self.registry.to_public_dict()
        self.assertEqual("1.0.0", document["registry_version"])
        self.assertEqual(10, document["feature_count"])
        self.assertEqual(64, len(document["registry_sha256"]))

    def test_identical_feature_sets_pass(self) -> None:
        report = compare_feature_sets([self.row], [deepcopy(self.row)], self.registry)
        self.assertEqual("PASS", report["status"])
        self.assertEqual(0, report["mismatch_count"])

    def test_mismatch_report_uses_hash_instead_of_customer_key(self) -> None:
        online = deepcopy(self.row)
        online["events_7d"] = 3
        report = compare_feature_sets([self.row], [online], self.registry)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, report["mismatch_count"])
        self.assertNotIn("cus_0001", str(report))
        self.assertEqual("events_7d", report["mismatch_examples"][0]["feature"])

    def test_decimal_tolerance_is_explicit(self) -> None:
        online = deepcopy(self.row)
        online["revenue_30d"] = "58.3000004"
        report = compare_feature_sets(
            [self.row], [online], self.registry, decimal_tolerance=Decimal("0.000001")
        )
        self.assertEqual("PASS", report["status"])

    def test_missing_and_unexpected_identities_are_counted(self) -> None:
        other = deepcopy(self.row)
        other["customer_key"] = "cus_0002"
        report = compare_feature_sets([self.row], [other], self.registry)
        self.assertEqual(1, report["missing_online_count"])
        self.assertEqual(1, report["unexpected_online_count"])
        self.assertEqual(16, len(report["missing_online_identity_hashes"][0]))
