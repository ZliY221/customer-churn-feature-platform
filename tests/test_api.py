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
            reassigned = client.patch(
                f"/v1/retention/tasks/{created.json()['task_id']}/owner",
                json={"owner": "customer-success"},
            )
            events = client.get(
                f"/v1/retention/tasks/{created.json()['task_id']}/events"
            )
            tasks = client.get("/v1/retention/tasks", params={"status": "IN_PROGRESS"})
            filtered = client.get(
                "/v1/retention/tasks",
                params={"risk_band": prediction["risk_band"], "owner": "customer-success"},
            )
            workloads = client.get("/v1/retention/workloads")
            live_events = client.get("/v1/retention/events", params={"after_id": 0})
            no_new_events = client.get(
                "/v1/retention/events",
                params={"after_id": live_events.json()["next_after_id"]},
            )

        self.assertEqual(200, console.status_code)
        self.assertIn("客户留存运营工作台", console.text)
        self.assertIn("任务时间线", console.text)
        self.assertIn("客户留存工作路径", console.text)
        self.assertIn("留存处置结果", console.text)
        self.assertIn('id="outcome-dialog"', console.text)
        self.assertIn('class="skip-link"', console.text)
        self.assertEqual(200, styles.status_code)
        self.assertIn("--coral: #d96c55", styles.text)
        self.assertIn("prefers-reduced-motion", styles.text)
        self.assertIn(".outcome-summary-grid", styles.text)
        self.assertEqual(100, summary.json()["customer_count"])
        self.assertEqual(5, priority.json()["count"])
        probabilities = [float(item["churn_probability"]) for item in priority.json()["items"]]
        self.assertEqual(sorted(probabilities, reverse=True), probabilities)
        self.assertEqual(201, created.status_code)
        self.assertEqual(prediction["recommended_playbook"], created.json()["playbook"])
        self.assertEqual(409, duplicate.status_code)
        self.assertEqual("IN_PROGRESS", updated.json()["status"])
        self.assertEqual("customer-success", reassigned.json()["owner"])
        self.assertEqual(3, events.json()["count"])
        self.assertEqual("OWNER_CHANGED", events.json()["items"][2]["event_type"])
        self.assertEqual(1, tasks.json()["count"])
        self.assertEqual(1, filtered.json()["count"])
        self.assertEqual("customer-success", filtered.json()["filters"]["owner"])
        self.assertEqual(1, workloads.json()["owner_count"])
        self.assertEqual(1, workloads.json()["active_task_count"])
        self.assertEqual("customer-success", workloads.json()["items"][0]["owner"])
        self.assertEqual(3, live_events.json()["count"])
        self.assertEqual("OWNER_CHANGED", live_events.json()["items"][-1]["event_type"])
        self.assertEqual(0, no_new_events.json()["count"])

    def test_repository_rejects_model_lineage_mismatch(self) -> None:
        model = json.loads(self.model.read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in self.predictions.read_text(encoding="utf-8").splitlines()]
        rows[0]["model_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "model lineage"):
            PredictionRepository(rows, model)

    def test_v3_retention_outcomes_close_the_observation_loop(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TestClient(
                create_app(self.model, self.predictions, task_database_path=database)
            ) as client:
                prediction = client.get(
                    "/v1/portfolio/priority", params={"limit": 1}
                ).json()["items"][0]
                task = client.post(
                    "/v1/retention/tasks",
                    json={"prediction_id": prediction["prediction_id"], "owner": "retention-team"},
                ).json()
                payload = {
                    "contact_result": "REACHED",
                    "contact_channel": "PHONE",
                    "customer_response": "POSITIVE",
                    "contacted_at": "2026-10-18T08:00:00Z",
                    "observed_at": "2026-10-25T08:00:00Z",
                    "retained_7d": True,
                    "retained_30d": None,
                    "note": "客户已恢复使用",
                }
                recorded = client.put(
                    f"/v3/retention/tasks/{task['task_id']}/outcome", json=payload
                )
                fetched = client.get(f"/v3/retention/tasks/{task['task_id']}/outcome")
                outcomes = client.get("/v3/retention/outcomes")
                summary = client.get("/v3/retention/outcomes/summary")
                portfolio = client.get("/v1/portfolio/summary")
                invalid_window = client.put(
                    f"/v3/retention/tasks/{task['task_id']}/outcome",
                    json={**payload, "observed_at": "2026-10-17T08:00:00Z"},
                )

            self.assertEqual(200, recorded.status_code)
            self.assertEqual(recorded.json(), fetched.json())
            self.assertEqual(1, outcomes.json()["count"])
            self.assertEqual(1, summary.json()["recorded_count"])
            self.assertEqual(1.0, summary.json()["retention_7d_rate"])
            self.assertEqual(summary.json(), portfolio.json()["retention_outcomes"])
            self.assertEqual(422, invalid_window.status_code)

    def test_retention_tasks_support_bulk_reassignment(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TestClient(create_app(self.model, self.predictions, task_database_path=database)) as client:
                predictions = client.get("/v1/portfolio/priority", params={"limit": 2}).json()["items"]
                tasks = [client.post("/v1/retention/tasks", json={
                    "prediction_id": prediction["prediction_id"], "owner": "retention-team"
                }).json() for prediction in predictions]
                preview = client.post("/v1/retention/tasks/bulk-preview", json={
                    "task_ids": [task["task_id"] for task in tasks], "owner": "priority-desk"
                })
                response = client.patch("/v1/retention/tasks/bulk-owner", json={
                    "task_ids": [task["task_id"] for task in tasks], "owner": "priority-desk"
                })
                repeated = client.patch("/v1/retention/tasks/bulk-owner", json={
                    "task_ids": [task["task_id"] for task in tasks], "owner": "priority-desk"
                })
                missing = client.patch("/v1/retention/tasks/bulk-owner", json={
                    "task_ids": ["missing"], "owner": "priority-desk"
                })
                empty = client.patch("/v1/retention/tasks/bulk-owner", json={
                    "task_ids": [], "owner": "priority-desk"
                })
                repeated_preview = client.post("/v1/retention/tasks/bulk-preview", json={
                    "task_ids": [task["task_id"] for task in tasks], "owner": "priority-desk"
                })
                missing_preview = client.post("/v1/retention/tasks/bulk-preview", json={
                    "task_ids": ["missing"], "status": "COMPLETED"
                })
                invalid_preview = client.post("/v1/retention/tasks/bulk-preview", json={
                    "task_ids": [task["task_id"] for task in tasks]
                })
                blank_owner_preview = client.post("/v1/retention/tasks/bulk-preview", json={
                    "task_ids": [task["task_id"] for task in tasks], "owner": "   "
                })
            self.assertEqual(2, preview.json()["changed_count"])
            self.assertEqual(0, preview.json()["unchanged_count"])
            self.assertEqual(
                2,
                sum(preview.json()["changed_by_risk_band"].values()),
            )
            self.assertEqual({"CRITICAL", "HIGH", "WATCH", "STABLE"}, set(preview.json()["changed_by_risk_band"]))
            self.assertEqual(2, preview.json()["changed_by_status"]["OPEN"])
            self.assertEqual(2, len(preview.json()["changes"]))
            self.assertEqual(["owner"], preview.json()["changes"][0]["changed_fields"])
            self.assertEqual("retention-team", preview.json()["changes"][0]["before"]["owner"])
            self.assertEqual("priority-desk", preview.json()["changes"][0]["after"]["owner"])
            self.assertTrue(preview.json()["requires_explicit_confirmation"])
            self.assertIn("HIGH_RISK_TASKS", preview.json()["confirmation_reasons"])
            self.assertEqual(2, preview.json()["impact"]["field_change_count"])
            self.assertEqual(1, preview.json()["impact"]["source_owner_count"])
            self.assertEqual("priority-desk", preview.json()["impact"]["target_owner"])
            self.assertEqual(200, response.status_code)
            self.assertEqual(2, response.json()["count"])
            self.assertEqual(2, response.json()["changed_count"])
            self.assertEqual(0, response.json()["unchanged_count"])
            self.assertEqual(0, repeated.json()["changed_count"])
            self.assertEqual(2, repeated.json()["unchanged_count"])
            self.assertEqual({"priority-desk"}, {item["owner"] for item in response.json()["items"]})
            self.assertEqual(404, missing.status_code)
            self.assertEqual(422, empty.status_code)
            self.assertEqual(0, repeated_preview.json()["changed_count"])
            self.assertEqual(2, repeated_preview.json()["unchanged_count"])
            self.assertEqual(0, sum(repeated_preview.json()["changed_by_risk_band"].values()))
            self.assertEqual([], repeated_preview.json()["changes"])
            self.assertEqual(404, missing_preview.status_code)
            self.assertEqual(422, invalid_preview.status_code)
            self.assertEqual(422, blank_owner_preview.status_code)

    def test_retention_tasks_support_bulk_status_progression(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TestClient(create_app(self.model, self.predictions, task_database_path=database)) as client:
                prediction = client.get("/v1/portfolio/priority", params={"limit": 1}).json()["items"][0]
                task = client.post("/v1/retention/tasks", json={
                    "prediction_id": prediction["prediction_id"], "owner": "retention-team"
                }).json()
                response = client.patch("/v1/retention/tasks/bulk-status", json={
                    "task_ids": [task["task_id"], task["task_id"]], "status": "IN_PROGRESS"
                })
                repeated = client.patch("/v1/retention/tasks/bulk-status", json={
                    "task_ids": [task["task_id"]], "status": "IN_PROGRESS"
                })
                invalid = client.patch("/v1/retention/tasks/bulk-status", json={
                    "task_ids": [], "status": "COMPLETED"
                })
                terminal_preview = client.post("/v1/retention/tasks/bulk-preview", json={
                    "task_ids": [task["task_id"]], "status": "COMPLETED"
                })
            self.assertEqual(200, response.status_code)
            self.assertEqual(1, response.json()["count"])
            self.assertEqual(1, response.json()["changed_count"])
            self.assertEqual(0, response.json()["unchanged_count"])
            self.assertEqual(0, repeated.json()["changed_count"])
            self.assertEqual(1, repeated.json()["unchanged_count"])
            self.assertEqual("IN_PROGRESS", response.json()["items"][0]["status"])
            self.assertEqual(422, invalid.status_code)
            self.assertIn("TERMINAL_STATUS", terminal_preview.json()["confirmation_reasons"])
            self.assertEqual("COMPLETED", terminal_preview.json()["impact"]["target_status"])

    def test_v2_bulk_command_assigns_and_starts_tasks_atomically(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TestClient(create_app(self.model, self.predictions, task_database_path=database)) as client:
                predictions = client.get("/v1/portfolio/priority", params={"limit": 2}).json()["items"]
                tasks = [client.post("/v1/retention/tasks", json={
                    "prediction_id": item["prediction_id"], "owner": "retention-team"
                }).json() for item in predictions]
                payload = {"task_ids": [item["task_id"] for item in tasks] + [tasks[0]["task_id"]],
                           "owner": "priority-desk", "status": "IN_PROGRESS",
                           "operator_note": "晚间高风险客户跟进"}
                response = client.patch("/v2/retention/tasks/bulk", json=payload)
                repeated = client.patch("/v2/retention/tasks/bulk", json=payload)
                events = client.get(f"/v1/retention/tasks/{tasks[0]['task_id']}/events")
            self.assertEqual(2, response.json()["changed_count"])
            self.assertEqual({"input_count": 3, "unique_count": 2, "duplicate_count": 1}, response.json()["selection"])
            self.assertEqual(2, response.json()["owner_changed_count"])
            self.assertEqual(2, response.json()["status_changed_count"])
            self.assertEqual({"priority-desk"}, {item["owner"] for item in response.json()["items"]})
            self.assertEqual({"IN_PROGRESS"}, {item["status"] for item in response.json()["items"]})
            self.assertEqual(0, repeated.json()["changed_count"])
            self.assertEqual(4, response.json()["audit"]["events_created_count"])
            self.assertRegex(response.json()["audit"]["receipt_id"], r"^RET-[0-9A-F]{12}$")
            self.assertEqual("晚间高风险客户跟进", response.json()["audit"]["operator_note"])
            self.assertEqual(2, sum(response.json()["impact"]["risk_band_counts"].values()))
            self.assertGreaterEqual(response.json()["impact"]["high_priority_count"], 0)
            self.assertTrue(response.json()["follow_up"]["required"])
            self.assertIn(response.json()["follow_up"]["review_window_hours"], {4, 24})
            self.assertIn(response.json()["follow_up"]["review_priority"], {"P1", "P2"})
            self.assertIn(response.json()["audit"]["receipt_id"], response.json()["handoff"]["summary"])
            self.assertEqual("priority-desk", response.json()["handoff"]["target_owner"])
            self.assertTrue(response.json()["handoff"]["requires_review"])
            self.assertIn("SCHEDULE_REVIEW", response.json()["handoff"]["checklist"])
            self.assertEqual(len(response.json()["handoff"]["checklist"]), response.json()["handoff"]["step_count"])
            self.assertEqual(response.json()["handoff"]["step_count"] * 5, response.json()["handoff"]["estimated_minutes"])
            expected_readiness = "ESCALATION_REQUIRED" if response.json()["impact"]["high_priority_count"] else "READY_FOR_FOLLOW_UP"
            expected_blockers = ["HIGH_PRIORITY_CONTACT_REQUIRED"] if response.json()["impact"]["high_priority_count"] else []
            self.assertEqual(expected_readiness, response.json()["handoff"]["readiness"])
            self.assertEqual(expected_blockers, response.json()["handoff"]["blockers"])
            self.assertIsNotNone(response.json()["audit"]["first_new_event_id"])
            self.assertEqual(2, len(response.json()["changes"]))
            self.assertEqual(
                ["owner", "status"], response.json()["changes"][0]["changed_fields"]
            )
            self.assertEqual("retention-team", response.json()["changes"][0]["before"]["owner"])
            self.assertEqual("priority-desk", response.json()["changes"][0]["after"]["owner"])
            self.assertEqual(0, repeated.json()["audit"]["events_created_count"])
            self.assertRegex(repeated.json()["audit"]["receipt_id"], r"^RET-[0-9A-F]{12}$")
            self.assertNotEqual(
                response.json()["audit"]["receipt_id"], repeated.json()["audit"]["receipt_id"]
            )
            self.assertEqual([], repeated.json()["changes"])
            self.assertEqual("REVIEW_NO_CHANGE_SELECTION", repeated.json()["follow_up"]["next_action"])
            self.assertFalse(repeated.json()["follow_up"]["required"])
            self.assertEqual("NONE", repeated.json()["follow_up"]["review_priority"])
            self.assertFalse(repeated.json()["handoff"]["requires_review"])
            self.assertEqual(["REVIEW_SELECTION"], repeated.json()["handoff"]["checklist"])
            self.assertEqual(1, repeated.json()["handoff"]["step_count"])
            self.assertEqual(5, repeated.json()["handoff"]["estimated_minutes"])
            self.assertEqual("SELECTION_REVIEW", repeated.json()["handoff"]["readiness"])
            self.assertEqual(["NO_EFFECTIVE_CHANGE"], repeated.json()["handoff"]["blockers"])
            self.assertEqual(3, events.json()["count"])


    def test_contact_policy_is_available_through_the_operations_api(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TestClient(create_app(self.model, self.predictions, task_database_path=database)) as client:
                prediction = client.get("/v1/portfolio/priority", params={"limit": 1}).json()["items"][0]
                task = client.post("/v1/retention/tasks", json={
                    "prediction_id": prediction["prediction_id"], "owner": "retention-team"
                }).json()
                response = client.get(
                    f"/v3/retention/tasks/{task['task_id']}/contact-policy",
                    params=[("consent_status", "GRANTED"), ("contacts_7d", "1"),
                            ("requested_channel", "IN_APP"),
                            ("preferred_channels", "IN_APP")],
                )
            self.assertEqual(200, response.status_code)
            self.assertTrue(response.json()["contact_allowed"])
            self.assertEqual("ALLOWED", response.json()["policy_status"])

    def test_offer_budget_is_available_through_the_operations_api(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TestClient(create_app(self.model, self.predictions, task_database_path=database)) as client:
                prediction = client.get("/v1/portfolio/priority", params={"limit": 1}).json()["items"][0]
                task = client.post("/v1/retention/tasks", json={
                    "prediction_id": prediction["prediction_id"], "owner": "retention-team"
                }).json()
                response = client.get(
                    f"/v3/retention/tasks/{task['task_id']}/offer-budget",
                    params={"customer_value": 1000, "expected_retention_uplift": 0.2,
                            "offer_cost": 50, "remaining_campaign_budget": 1000},
                )
            self.assertEqual(200, response.status_code)
            self.assertEqual("APPROVED", response.json()["status"])
            self.assertEqual(4.0, response.json()["expected_roi"])


if __name__ == "__main__":
    unittest.main()
