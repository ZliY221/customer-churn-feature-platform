from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from churn_platform.operations import RiskBand, RiskPolicy, portfolio_summary, priority_customers
from churn_platform.retention_store import RetentionTaskStore


def prediction(customer: str, probability: float) -> dict[str, object]:
    return {
        "prediction_id": f"pred-{customer}",
        "customer_key": customer,
        "as_of_time": "2026-05-31T00:00:00Z",
        "churn_probability": f"{probability:.6f}",
        "churn_prediction": probability >= 0.5,
    }


class RetentionOperationsTests(unittest.TestCase):
    def test_portfolio_summary_and_priority_are_deterministic(self) -> None:
        rows = [prediction("c-low", 0.10), prediction("c-high", 0.72), prediction("c-critical", 0.91)]

        summary = portfolio_summary(rows)
        priority = priority_customers(rows, limit=2)

        self.assertEqual(3, summary["customer_count"])
        self.assertEqual(1, summary["risk_bands"]["CRITICAL"])
        self.assertEqual(1, summary["risk_bands"]["HIGH"])
        self.assertEqual(["c-critical", "c-high"], [row["customer_key"] for row in priority])
        self.assertEqual(RiskBand.WATCH, RiskPolicy().classify(0.40))

    def test_task_store_persists_lineage_and_status(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite3"
            store = RetentionTaskStore(path)
            created = store.create(
                prediction_id="pred-1",
                customer_key="customer-1",
                model_sha256="a" * 64,
                risk_band="HIGH",
                owner="retention-team",
                playbook="targeted-benefit-offer",
            )
            updated = store.update_status(created["task_id"], "COMPLETED")
            events = store.events(created["task_id"])
            store.close()

            reopened = RetentionTaskStore(path)
            self.assertEqual("COMPLETED", updated["status"])
            self.assertEqual(1, reopened.status_counts()["COMPLETED"])
            self.assertEqual("a" * 64, reopened.list()[0]["model_sha256"])
            self.assertEqual(1, len(reopened.list(risk_band="HIGH", owner="retention-team")))
            self.assertEqual([], reopened.list(risk_band="STABLE"))
            self.assertEqual(["CREATED", "STATUS_CHANGED"], [row["event_type"] for row in events])
            self.assertEqual("OPEN", events[1]["from_status"])
            self.assertEqual("COMPLETED", events[1]["to_status"])
            self.assertEqual(2, reopened.event_count())
            self.assertEqual([], reopened.owner_workloads())
            reopened.close()

    def test_owner_workloads_prioritize_high_risk_active_tasks(self) -> None:
        with TemporaryDirectory() as directory:
            store = RetentionTaskStore(Path(directory) / "tasks.sqlite3")
            store.create(prediction_id="pred-1", customer_key="one", model_sha256="a" * 64,
                         risk_band="HIGH", owner="team-b", playbook="call")
            first = store.create(prediction_id="pred-2", customer_key="two", model_sha256="a" * 64,
                                 risk_band="CRITICAL", owner="team-a", playbook="call")
            store.update_status(first["task_id"], "IN_PROGRESS")
            workloads = store.owner_workloads()
            self.assertEqual(["team-a", "team-b"], [item["owner"] for item in workloads])
            self.assertEqual(1, workloads[0]["critical_count"])
            self.assertEqual(1, workloads[0]["in_progress_count"])
            store.close()


if __name__ == "__main__":
    unittest.main()
