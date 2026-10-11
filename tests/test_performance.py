from __future__ import annotations

import json
import unittest

from churn_platform.performance import evaluate_performance


MODEL_SHA = "a" * 64
AS_OF = "2026-05-31T00:00:00Z"


def predictions(count: int = 10) -> list[dict]:
    return [
        {
            "customer_key": f"customer_{index:03d}",
            "as_of_time": AS_OF,
            "model_sha256": MODEL_SHA,
            "churn_probability": "0.900000" if index >= count // 2 else "0.100000",
            "churn_prediction": index >= count // 2,
            "decision_threshold": "0.500000",
        }
        for index in range(count)
    ]


def labels(count: int = 10, *, inverted: bool = False) -> list[dict]:
    return [
        {
            "customer_key": f"customer_{index:03d}",
            "as_of_time": AS_OF,
            "churned_30d": (index < count // 2) if inverted else (index >= count // 2),
            "label_observed_at": "2026-07-01T00:00:00Z",
        }
        for index in range(count)
    ]


def policy() -> dict:
    return {
        "policy_version": "1.0",
        "thresholds": {
            "minimum_label_coverage": 0.8,
            "minimum_labeled_rows": 4,
            "minimum_roc_auc": 0.6,
            "minimum_pr_auc": 0.3,
            "minimum_f1": 0.4,
            "maximum_brier_score": 0.3,
        },
    }


class PerformanceMonitoringTests(unittest.TestCase):
    def test_separated_predictions_pass_and_report_only_aggregates(self) -> None:
        report = evaluate_performance(predictions(), labels(), policy())
        self.assertEqual("PASS", report["status"])
        self.assertEqual(1.0, report["metrics"]["roc_auc"])
        self.assertNotIn("customer_key", json.dumps(report))
        self.assertEqual(64, len(report["lineage"]["label_sha256"]))

    def test_low_coverage_is_insufficient_instead_of_scoring_partial_labels(self) -> None:
        report = evaluate_performance(predictions(), labels(2), policy())
        self.assertEqual("INSUFFICIENT_DATA", report["status"])
        self.assertIsNone(report["metrics"])
        self.assertIn("PERFORMANCE-LABEL-COVERAGE", [row["alert_id"] for row in report["alerts"]])

    def test_degraded_predictions_trigger_metric_alerts(self) -> None:
        report = evaluate_performance(predictions(), labels(inverted=True), policy())
        self.assertEqual("ALERT", report["status"])
        self.assertIn("PERFORMANCE-ROC-AUC", [row["alert_id"] for row in report["alerts"]])
        self.assertIn("PERFORMANCE-BRIER", [row["alert_id"] for row in report["alerts"]])

    def test_duplicate_and_immature_labels_are_rejected(self) -> None:
        duplicated = labels()
        duplicated.append(dict(duplicated[0]))
        with self.assertRaisesRegex(ValueError, "label identities"):
            evaluate_performance(predictions(), duplicated, policy())
        immature = labels()
        immature[0]["label_observed_at"] = "2026-06-15T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "30-day outcome"):
            evaluate_performance(predictions(), immature, policy())


if __name__ == "__main__":
    unittest.main()
