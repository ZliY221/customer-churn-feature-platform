from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from churn_platform.retention_store import RetentionTaskStore


class RetentionOutcomeStoreTests(unittest.TestCase):
    def test_outcome_upsert_keeps_identity_and_updates_summary(self) -> None:
        with TemporaryDirectory() as directory:
            store = RetentionTaskStore(Path(directory) / "tasks.sqlite3")
            task = store.create(
                prediction_id="pred-outcome",
                customer_key="customer-a",
                model_sha256="a" * 64,
                risk_band="HIGH",
                owner="retention-team",
                playbook="phone-call",
            )
            first = store.upsert_outcome(
                task["task_id"],
                contact_result="REACHED",
                contact_channel="PHONE",
                customer_response="POSITIVE",
                contacted_at="2026-10-18T08:00:00Z",
                retained_7d=True,
                note="客户确认继续使用",
            )
            updated = store.upsert_outcome(
                task["task_id"],
                contact_result="REACHED",
                contact_channel="PHONE",
                customer_response="POSITIVE",
                contacted_at="2026-10-18T08:00:00Z",
                observed_at="2026-11-17T08:00:00Z",
                retained_7d=True,
                retained_30d=False,
                note="30 天观察已完成",
            )
            summary = store.outcome_summary()
            events = store.events(task["task_id"])

            self.assertEqual(first["outcome_id"], updated["outcome_id"])
            self.assertTrue(updated["retained_7d"])
            self.assertFalse(updated["retained_30d"])
            self.assertEqual(1, summary["recorded_count"])
            self.assertEqual(1.0, summary["retention_7d_rate"])
            self.assertEqual(0.0, summary["retention_30d_rate"])
            self.assertEqual(2, sum(event["event_type"] == "OUTCOME_RECORDED" for event in events))
            store.close()

    def test_outcome_requires_existing_task_and_supported_values(self) -> None:
        with TemporaryDirectory() as directory:
            store = RetentionTaskStore(Path(directory) / "tasks.sqlite3")
            with self.assertRaises(KeyError):
                store.upsert_outcome(
                    "missing",
                    contact_result="REACHED",
                    contact_channel="PHONE",
                    customer_response="POSITIVE",
                    contacted_at="2026-10-18T08:00:00Z",
                )
            task = store.create(
                prediction_id="pred-invalid-outcome",
                customer_key="customer-b",
                model_sha256="b" * 64,
                risk_band="WATCH",
                owner="retention-team",
                playbook="message",
            )
            with self.assertRaisesRegex(ValueError, "unsupported contact result"):
                store.upsert_outcome(
                    task["task_id"],
                    contact_result="UNKNOWN_RESULT",
                    contact_channel="PHONE",
                    customer_response="UNKNOWN",
                    contacted_at="2026-10-18T08:00:00Z",
                )
            store.close()


if __name__ == "__main__":
    unittest.main()
