from __future__ import annotations

import json
import unittest

from churn_platform.comparison import compare_models


AS_OF = "2026-05-31T00:00:00Z"


def prediction_rows(model_hash: str, negative_score: float, positive_score: float) -> list[dict]:
    rows = []
    for index in range(10):
        score = negative_score if index < 5 else positive_score
        rows.append(
            {
                "customer_key": f"customer_{index:03d}",
                "as_of_time": AS_OF,
                "model_sha256": model_hash,
                "churn_probability": f"{score:.6f}",
                "churn_prediction": score >= 0.5,
                "decision_threshold": "0.500000",
            }
        )
    return rows


def label_rows() -> list[dict]:
    return [
        {
            "customer_key": f"customer_{index:03d}",
            "as_of_time": AS_OF,
            "churned_30d": index >= 5,
            "label_observed_at": "2026-07-01T00:00:00Z",
        }
        for index in range(10)
    ]


PERFORMANCE_POLICY = {
    "policy_version": "1.0",
    "thresholds": {
        "minimum_label_coverage": 1.0,
        "minimum_labeled_rows": 10,
        "minimum_roc_auc": 0.5,
        "minimum_pr_auc": 0.5,
        "minimum_f1": 0.0,
        "maximum_brier_score": 0.5,
    },
}
COMPARISON_POLICY = {
    "policy_version": "1.0",
    "thresholds": {
        "minimum_roc_auc_improvement": 0.01,
        "minimum_pr_auc_improvement": 0.01,
        "minimum_f1_improvement": 0.01,
        "maximum_brier_score_regression": 0.0,
    },
}


class ModelComparisonTests(unittest.TestCase):
    def test_better_challenger_is_recommended_without_customer_rows(self) -> None:
        report = compare_models(
            prediction_rows("a" * 64, 0.4, 0.4),
            prediction_rows("b" * 64, 0.1, 0.9),
            label_rows(),
            PERFORMANCE_POLICY,
            COMPARISON_POLICY,
        )
        self.assertEqual("PROMOTE_CHALLENGER", report["recommendation"])
        self.assertEqual("b" * 64, report["selected_model_sha256"])
        self.assertNotIn("customer_key", json.dumps(report))

    def test_degraded_challenger_keeps_champion(self) -> None:
        report = compare_models(
            prediction_rows("a" * 64, 0.1, 0.9),
            prediction_rows("b" * 64, 0.9, 0.1),
            label_rows(),
            PERFORMANCE_POLICY,
            COMPARISON_POLICY,
        )
        self.assertEqual("KEEP_CHAMPION", report["recommendation"])
        self.assertTrue(any(check["status"] == "FAIL" for check in report["checks"]))

    def test_prediction_cohorts_must_match_exactly(self) -> None:
        challenger = prediction_rows("b" * 64, 0.1, 0.9)
        challenger.pop()
        with self.assertRaisesRegex(ValueError, "identities must match"):
            compare_models(
                prediction_rows("a" * 64, 0.4, 0.4),
                challenger,
                label_rows(),
                PERFORMANCE_POLICY,
                COMPARISON_POLICY,
            )


if __name__ == "__main__":
    unittest.main()
