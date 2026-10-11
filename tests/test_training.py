from __future__ import annotations

from datetime import UTC, datetime
import unittest

from churn_platform.training import TemporalSplitConfig, temporal_split, train_baseline


def _rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for month, as_of in enumerate(("2026-01-31T00:00:00Z", "2026-02-28T00:00:00Z", "2026-03-31T00:00:00Z")):
        for index in range(20):
            churned = index % 2 == 0
            activity = 2 + month if churned else 20 + month
            rows.append(
                {
                    "customer_key": f"cus_{month:02d}{index:018d}",
                    "as_of_time": as_of,
                    "churned_30d": churned,
                    "events_7d": activity // 2,
                    "events_30d": activity,
                    "active_days_30d": max(1, activity // 3),
                    "login_count_30d": activity // 4,
                    "product_view_count_30d": activity // 3,
                    "cart_add_count_30d": activity // 5,
                    "order_count_30d": activity // 6,
                    "support_ticket_count_30d": index % 3,
                    "revenue_30d": f"{activity * 12.5:.2f}",
                    "days_since_last_event": 12 if churned else 1,
                }
            )
    return rows


class TemporalTrainingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = TemporalSplitConfig(
            train_end=datetime(2026, 1, 31, tzinfo=UTC),
            validation_end=datetime(2026, 2, 28, tzinfo=UTC),
        )

    def test_temporal_split_has_strict_chronological_boundaries(self) -> None:
        splits = temporal_split(reversed(_rows()), self.config)

        self.assertEqual({"train": 20, "validation": 20, "test": 20}, {key: len(value) for key, value in splits.items()})
        self.assertTrue(all(row["as_of_time"].startswith("2026-01") for row in splits["train"]))
        self.assertTrue(all(row["as_of_time"].startswith("2026-03") for row in splits["test"]))

    def test_training_is_reproducible_and_test_metrics_are_complete(self) -> None:
        splits = temporal_split(_rows(), self.config)
        first_model, first_metrics = train_baseline(splits)
        second_model, second_metrics = train_baseline(splits)

        self.assertEqual(first_model, second_model)
        self.assertEqual(first_metrics, second_metrics)
        self.assertEqual(1.0, first_metrics["test"]["roc_auc"])
        self.assertEqual(1.0, first_metrics["test"]["pr_auc"])
        self.assertIn("brier_score", first_metrics["test"])
        self.assertIn("training_data_sha256", first_model)

    def test_invalid_boundaries_and_single_class_training_fail(self) -> None:
        with self.assertRaisesRegex(ValueError, "earlier"):
            temporal_split(
                _rows(),
                TemporalSplitConfig(
                    train_end=datetime(2026, 2, 1, tzinfo=UTC),
                    validation_end=datetime(2026, 2, 1, tzinfo=UTC),
                ),
            )
        splits = temporal_split(_rows(), self.config)
        for row in splits["train"]:
            row["churned_30d"] = False
        with self.assertRaisesRegex(ValueError, "both label classes"):
            train_baseline(splits)


if __name__ == "__main__":
    unittest.main()
