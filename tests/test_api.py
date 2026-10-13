from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from churn_platform.api import PredictionRepository, create_app
from churn_platform.demo_cli import run_demo


class PredictionApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        run_demo(cls.root)
        cls.model = cls.root / "model" / "baseline.json"
        cls.predictions = cls.root / "predictions" / "candidate.ndjson"
        cls.task_database = cls.root / "operations" / "retention-tasks.sqlite3"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_health_and_model_metadata_expose_lineage(self) -> None:
        with TestClient(create_app(self.model, self.predictions, task_database_path=self.task_database)) as client:
            health = client.get("/health")
            metadata = client.get("/v1/model")
            registry = client.get("/v1/features")
        self.assertEqual(200, health.status_code)
        self.assertEqual("ready", health.json()["status"])
        self.assertEqual(100, health.json()["prediction_count"])
        self.assertEqual(health.json()["model_sha256"], metadata.json()["model_sha256"])
        self.assertEqual(10, len(metadata.json()["feature_columns"]))
        self.assertEqual(200, registry.status_code)
        self.assertEqual(10, registry.json()["feature_count"])
        self.assertEqual(64, len(registry.json()["registry_sha256"]))

    def test_prediction_queries_are_bounded_and_not_found_is_explicit(self) -> None:
        with TestClient(create_app(self.model, self.predictions, task_database_path=self.task_database)) as client:
            page = client.get("/v1/predictions", params={"limit": 5, "offset": 2})
            first = page.json()["items"][0]
            by_id = client.get(f"/v1/predictions/{first['prediction_id']}")
            by_customer = client.get(f"/v1/customers/{first['customer_key']}/predictions")
            invalid = client.get("/v1/predictions", params={"limit": 101})
            missing = client.get("/v1/predictions/missing")
        self.assertEqual(5, len(page.json()["items"]))
        self.assertEqual(first, by_id.json())
        self.assertEqual(first["customer_key"], by_customer.json()[0]["customer_key"])
        self.assertEqual(422, invalid.status_code)
        self.assertEqual(404, missing.status_code)

    def test_operations_console_and_retention_workflow(self) -> None:
        with TestClient(
            create_app(self.model, self.predictions, task_database_path=self.task_database)
        ) as client:
            console = client.get("/console")
            styles = client.get("/console/assets/styles.css")
            summary = client.get("/v1/portfolio/summary")
            priority = client.get("/v1/portfolio/priority", params={"limit": 5})
            prediction = priority.json()["items"][0]
            created = client.post(
                "/v1/retention/tasks",
                json={"prediction_id": prediction["prediction_id"], "owner": "retention-team"},
            )
            duplicate = client.post(
                "/v1/retention/tasks",
                json={"prediction_id": prediction["prediction_id"], "owner": "retention-team"},
            )
            updated = client.patch(
                f"/v1/retention/tasks/{created.json()['task_id']}/status",
                json={"status": "IN_PROGRESS"},
            )
            events = client.get(
                f"/v1/retention/tasks/{created.json()['task_id']}/events"
            )
            tasks = client.get("/v1/retention/tasks", params={"status": "IN_PROGRESS"})
            filtered = client.get(
                "/v1/retention/tasks",
                params={"risk_band": prediction["risk_band"], "owner": "retention-team"},
            )

        self.assertEqual(200, console.status_code)
        self.assertIn("客户留存运营工作台", console.text)
        self.assertIn("任务时间线", console.text)
        self.assertEqual(200, styles.status_code)
        self.assertEqual(100, summary.json()["customer_count"])
        self.assertEqual(5, priority.json()["count"])
        probabilities = [float(item["churn_probability"]) for item in priority.json()["items"]]
        self.assertEqual(sorted(probabilities, reverse=True), probabilities)
        self.assertEqual(201, created.status_code)
        self.assertEqual(prediction["recommended_playbook"], created.json()["playbook"])
        self.assertEqual(409, duplicate.status_code)
        self.assertEqual("IN_PROGRESS", updated.json()["status"])
        self.assertEqual(2, events.json()["count"])
        self.assertEqual("STATUS_CHANGED", events.json()["items"][1]["event_type"])
        self.assertEqual(1, tasks.json()["count"])
        self.assertEqual(1, filtered.json()["count"])
        self.assertEqual("retention-team", filtered.json()["filters"]["owner"])

    def test_repository_rejects_model_lineage_mismatch(self) -> None:
        model = json.loads(self.model.read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in self.predictions.read_text(encoding="utf-8").splitlines()]
        rows[0]["model_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "model lineage"):
            PredictionRepository(rows, model)


if __name__ == "__main__":
    unittest.main()
