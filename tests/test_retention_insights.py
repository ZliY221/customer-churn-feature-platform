from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import UTC, datetime, timedelta

from churn_platform.retention_store import RetentionTaskStore


class RetentionInsightTests(unittest.TestCase):
    def test_strategy_summary_groups_playbooks(self):
        with TemporaryDirectory() as directory:
            store = RetentionTaskStore(Path(directory) / "tasks.sqlite3")
            first = store.create(prediction_id="p1", customer_key="c1", model_sha256="a" * 64, risk_band="CRITICAL", owner="team", playbook="priority-call")
            store.create(prediction_id="p2", customer_key="c2", model_sha256="a" * 64, risk_band="WATCH", owner="team", playbook="message")
            store.update_status(first["task_id"], "COMPLETED")
            items = store.strategy_summary()
            self.assertEqual(["priority-call", "message"], [item["strategy"] for item in items])
            self.assertEqual(1, items[0]["completed_count"])
            store.close()

    def test_sla_summary_separates_overdue_and_due_soon(self):
        with TemporaryDirectory() as directory:
            store = RetentionTaskStore(Path(directory) / "tasks.sqlite3")
            first = store.create(prediction_id="p1", customer_key="c1", model_sha256="a" * 64, risk_band="HIGH", owner="team", playbook="call")
            second = store.create(prediction_id="p2", customer_key="c2", model_sha256="a" * 64, risk_band="WATCH", owner="team", playbook="message")
            now = datetime(2026, 10, 19, 12, tzinfo=UTC)
            store.connection.execute("UPDATE retention_tasks SET created_at = ? WHERE task_id = ?", ((now - timedelta(hours=25)).isoformat(), first["task_id"]))
            store.connection.execute("UPDATE retention_tasks SET created_at = ? WHERE task_id = ?", ((now - timedelta(hours=21)).isoformat(), second["task_id"]))
            store.connection.commit()
            summary = store.task_sla_summary(observed_at=now)
            self.assertEqual(1, summary["overdue_count"])
            self.assertEqual(1, summary["due_soon_count"])
            self.assertEqual(0, summary["on_track_count"])
            store.close()

    def test_strategy_effectiveness_uses_only_mature_observations(self):
        with TemporaryDirectory() as directory:
            store = RetentionTaskStore(Path(directory) / "tasks.sqlite3")
            task = store.create(prediction_id="p1", customer_key="c1", model_sha256="a" * 64, risk_band="HIGH", owner="team", playbook="priority-call")
            store.upsert_outcome(task["task_id"], contact_result="REACHED", contact_channel="PHONE", customer_response="POSITIVE", contacted_at="2026-10-01T00:00:00Z", retained_7d=True)
            item = store.strategy_effectiveness()[0]
            self.assertEqual(1.0, item["retention_7d_rate"])
            self.assertIsNone(item["retention_30d_rate"])
            store.close()

    def test_model_effectiveness_keeps_lineage_and_risk_band(self):
        with TemporaryDirectory() as directory:
            store = RetentionTaskStore(Path(directory) / "tasks.sqlite3")
            task = store.create(prediction_id="p1", customer_key="c1", model_sha256="b" * 64, risk_band="CRITICAL", owner="team", playbook="call")
            store.upsert_outcome(task["task_id"], contact_result="REACHED", contact_channel="PHONE", customer_response="POSITIVE", contacted_at="2026-09-01T00:00:00Z", retained_30d=True)
            item = store.model_effectiveness()[0]
            self.assertEqual("b" * 64, item["model_sha256"])
            self.assertEqual("CRITICAL", item["risk_band"])
            self.assertEqual(1.0, item["retention_30d_rate"])
            store.close()

    def test_priority_queue_orders_critical_before_watch(self):
        with TemporaryDirectory() as directory:
            store = RetentionTaskStore(Path(directory) / "tasks.sqlite3")
            store.create(prediction_id="p1", customer_key="c1", model_sha256="a" * 64, risk_band="WATCH", owner="team", playbook="message")
            store.create(prediction_id="p2", customer_key="c2", model_sha256="a" * 64, risk_band="CRITICAL", owner="team", playbook="call")
            self.assertEqual("CRITICAL", store.priority_queue()[0]["risk_band"])
            store.close()

    def test_next_action_keeps_human_review(self):
        with TemporaryDirectory() as directory:
            store = RetentionTaskStore(Path(directory) / "tasks.sqlite3")
            task = store.create(prediction_id="p1", customer_key="c1", model_sha256="a" * 64, risk_band="CRITICAL", owner="team", playbook="priority-call")
            action = store.next_action(task["task_id"])
            self.assertEqual("PHONE", action["recommended_channel"])
            self.assertTrue(action["requires_review"])
            store.close()
