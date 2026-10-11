from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from churn_platform.generator import GeneratorConfig, generate_events
from churn_platform.validation import validate_event


ROOT = Path(__file__).resolve().parents[1]


class CustomerEventGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = GeneratorConfig(
            customer_count=24,
            days=60,
            seed=2027,
            start_time=datetime(2026, 1, 1, tzinfo=UTC),
        )

    def test_generation_is_deterministic_and_identifiers_are_unique(self) -> None:
        first = generate_events(self.config)
        second = generate_events(self.config)

        self.assertEqual(first, second)
        self.assertGreater(len(first), 100)
        self.assertEqual(len(first), len({event["event_id"] for event in first}))

    def test_events_pass_contract_and_are_chronological(self) -> None:
        events = generate_events(self.config)

        for event in events:
            validate_event(event)
        self.assertEqual(
            sorted(event["event_time"] for event in events),
            [event["event_time"] for event in events],
        )
        self.assertTrue(any(event["event_type"] == "order_completed" for event in events))

    def test_generated_data_contains_no_direct_identity_fields(self) -> None:
        serialized = json.dumps(generate_events(self.config))

        for forbidden in ("name", "email", "phone", "address", "latitude", "longitude"):
            self.assertNotIn(f'"{forbidden}"', serialized)
        customer_keys = {
            event["customer_key"] for event in generate_events(self.config)
        }
        self.assertTrue(all(str(key).startswith("cus_") for key in customer_keys))

    def test_invalid_generator_boundaries_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            generate_events(GeneratorConfig(customer_count=0))
        with self.assertRaisesRegex(ValueError, "timezone"):
            generate_events(GeneratorConfig(start_time=datetime(2026, 1, 1)))

    def test_cli_writes_valid_ndjson(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "events.ndjson"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "churn_platform.cli",
                    "--customers",
                    "8",
                    "--days",
                    "30",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
            )
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertGreater(len(rows), 0)
        self.assertIn("synthetic customers", result.stdout)
        for row in rows:
            validate_event(row)


if __name__ == "__main__":
    unittest.main()
