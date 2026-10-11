from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from churn_platform.drift import monitor_drift, population_stability_index, write_drift_report
from churn_platform.training import FEATURE_COLUMNS


def features(offset: int = 0) -> list[dict]:
    return [
        {column: index + offset for column in FEATURE_COLUMNS}
        for index in range(1, 21)
    ]


def predictions(probability: float, positive: bool) -> list[dict]:
    return [
        {"churn_probability": f"{probability:.6f}", "churn_prediction": positive}
        for _ in range(20)
    ]


def policy() -> dict:
    return {
        "policy_version": "1.0",
        "thresholds": {
            "max_feature_psi": 0.2,
            "max_mean_probability_shift": 0.1,
            "max_positive_rate_shift": 0.1,
        },
    }


class DriftMonitoringTests(unittest.TestCase):
    def test_identical_batches_pass_without_customer_level_output(self) -> None:
        report = monitor_drift(
            features(), features(), predictions(0.4, False), predictions(0.4, False), policy()
        )
        self.assertEqual("PASS", report["status"])
        self.assertEqual([], report["alerts"])
        self.assertNotIn("customer_key", json.dumps(report))
        self.assertEqual(64, len(report["lineage"]["reference_feature_sha256"]))

    def test_feature_and_prediction_drift_emit_stable_alerts(self) -> None:
        report = monitor_drift(
            features(), features(100), predictions(0.2, False), predictions(0.8, True), policy()
        )
        self.assertEqual("ALERT", report["status"])
        alert_ids = [alert["alert_id"] for alert in report["alerts"]]
        self.assertIn("FEATURE-PSI:events_7d", alert_ids)
        self.assertIn("PREDICTION-MEAN-SHIFT", alert_ids)
        self.assertIn("PREDICTION-POSITIVE-RATE-SHIFT", alert_ids)

    def test_constant_reference_detects_candidate_values_above_anchor(self) -> None:
        self.assertGreater(population_stability_index([1.0] * 20, [2.0] * 20), 0.2)

    def test_invalid_probabilities_and_policy_are_rejected(self) -> None:
        invalid = predictions(0.4, False)
        invalid[0]["churn_probability"] = "1.2"
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            monitor_drift(features(), features(), predictions(0.4, False), invalid, policy())
        with self.assertRaisesRegex(ValueError, "thresholds"):
            monitor_drift(features(), features(), predictions(0.4, False), predictions(0.4, False), {})

    def test_report_writer_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / name for name in ("rf.ndjson", "cf.ndjson", "rp.ndjson", "cp.ndjson")]
            datasets = (features(), features(), predictions(0.4, False), predictions(0.4, False))
            for path, rows in zip(paths, datasets):
                path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            output = root / "report.json"
            first = write_drift_report(*paths, policy_path, output)
            first_bytes = output.read_bytes()
            second = write_drift_report(*paths, policy_path, output)
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, output.read_bytes())


if __name__ == "__main__":
    unittest.main()
