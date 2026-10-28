from datetime import datetime
import unittest

from churn_platform.task_escalation import RetentionTaskSla, plan_task_escalations


NOW = datetime.fromisoformat("2026-10-28T10:00:00+08:00")


class TaskEscalationTests(unittest.TestCase):
    def test_plan_prioritises_overdue_unowned_and_inactive_tasks(self) -> None:
        def task(task_id: str, risk: str, owner: str | None, due: str, activity: str) -> RetentionTaskSla:
            return RetentionTaskSla(task_id, risk, "OPEN", owner,
                datetime.fromisoformat("2026-10-27T08:00:00+08:00"),
                datetime.fromisoformat(due), datetime.fromisoformat(activity))
        result = plan_task_escalations([
            task("overdue", "HIGH", "team-a", "2026-10-28T09:00:00+08:00", "2026-10-28T08:30:00+08:00"),
            task("unowned", "CRITICAL", None, "2026-10-28T18:00:00+08:00", "2026-10-28T09:30:00+08:00"),
            task("inactive", "WATCH", "team-b", "2026-10-28T18:00:00+08:00", "2026-10-28T04:00:00+08:00"),
        ], now=NOW)
        self.assertEqual("SLA_ATTENTION_REQUIRED", result["status"])
        self.assertEqual("overdue", result["focus_items"][0]["task_id"])
        self.assertEqual(3, result["actionable_count"])
        self.assertFalse(result["automatic_task_changes_started"])

    def test_completed_task_is_excluded(self) -> None:
        task = RetentionTaskSla("done", "STABLE", "COMPLETED", "team",
            datetime.fromisoformat("2026-10-27T08:00:00+08:00"),
            datetime.fromisoformat("2026-10-28T09:00:00+08:00"),
            datetime.fromisoformat("2026-10-28T09:00:00+08:00"))
        result = plan_task_escalations([task], now=NOW)
        self.assertEqual("SLA_ON_TRACK", result["status"])
        self.assertEqual(0, result["active_task_count"])

    def test_naive_now_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            plan_task_escalations([], now=datetime(2026, 10, 28, 10))
