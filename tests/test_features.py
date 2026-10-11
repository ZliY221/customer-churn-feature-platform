from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from churn_platform.features import SnapshotConfig, build_feature_snapshot
from churn_platform.generator import GeneratorConfig, generate_events


ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 1, 31, tzinfo=UTC)


class FeatureSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = generate_events(
            GeneratorConfig(
                customer_count=24,
                days=60,
                seed=2027,
                start_time=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        self.config = SnapshotConfig(as_of_time=AS_OF)

    def test_snapshot_is_deterministic_and_sorted(self) -> None:
        first = build_feature_snapshot(self.events, self.config)
        second = build_feature_snapshot(reversed(self.events), self.config)

        self.assertEqual(first, second)
        self.assertEqual(
            [row["customer_key"] for row in first],
            sorted(row["customer_key"] for row in first),
        )
        self.assertTrue(any(row["churned_30d"] for row in first))
        self.assertTrue(any(not row["churned_30d"] for row in first))

    def test_future_events_change_label_but_never_features(self) -> None:
        customer = str(self.events[0]["customer_key"])
        history_only = [
            event
            for event in self.events
            if str(event["event_time"]) <= "2026-01-31T00:00:00Z"
        ]
        before = next(
            row
            for row in build_feature_snapshot(history_only, self.config)
            if row["customer_key"] == customer
        )
        future = deepcopy(self.events[0])
        future["event_id"] = "evt_00000000000000000000000000000000"
        future["event_type"] = "login"
        future["event_time"] = "2026-02-01T00:00:00Z"
        future["ingest_time"] = "2026-02-01T00:01:00Z"
        future["order_amount"] = None
        after = next(
            row
            for row in build_feature_snapshot([*history_only, future], self.config)
            if row["customer_key"] == customer
        )

        self.assertTrue(before["churned_30d"])
        self.assertFalse(after["churned_30d"])
        self.assertEqual(
            {key: value for key, value in before.items() if key != "churned_30d"},
            {key: value for key, value in after.items() if key != "churned_30d"},
        )

    def test_time_boundaries_and_decimal_aggregation(self) -> None:
        customer = str(self.events[0]["customer_key"])
        template = deepcopy(self.events[0])
        rows = []
        for index, (event_time, amount) in enumerate(
            [
                ("2026-01-01T00:00:00Z", "10.00"),
                ("2026-01-01T00:00:01Z", "10.01"),
                ("2026-01-31T00:00:00Z", "20.02"),
            ]
        ):
            row = deepcopy(template)
            row.update(
                event_id=f"evt_{index:032x}",
                customer_key=customer,
                event_type="order_completed",
                event_time=event_time,
                ingest_time=event_time,
                order_amount=amount,
            )
            rows.append(row)

        snapshot = build_feature_snapshot(rows, self.config)[0]

        self.assertEqual(snapshot["events_30d"], 2)
        self.assertEqual(snapshot["revenue_30d"], "30.03")
        self.assertEqual(snapshot["days_since_last_event"], 0)

    def test_cli_writes_reference_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "features.ndjson"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "churn_platform.feature_cli",
                    "--input",
                    "data/sample/customer_events.ndjson",
                    "--output",
                    str(output),
                    "--as-of-time",
                    "2026-01-31T00:00:00Z",
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
            )
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(rows), 12)
        self.assertIn("customer rows", result.stdout)
        self.assertTrue(all(row["as_of_time"] == "2026-01-31T00:00:00Z" for row in rows))

    def test_configuration_rejects_invalid_windows(self) -> None:
        with self.assertRaisesRegex(ValueError, "observation_days"):
            build_feature_snapshot(self.events, SnapshotConfig(AS_OF, observation_days=0))
        with self.assertRaisesRegex(ValueError, "timezone"):
            build_feature_snapshot(
                self.events, SnapshotConfig(datetime(2026, 1, 31), label_days=30)
            )


if __name__ == "__main__":
    unittest.main()
