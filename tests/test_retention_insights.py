from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

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
