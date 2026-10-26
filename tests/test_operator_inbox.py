import unittest

from churn_platform.operator_inbox import build_retention_operator_inbox
from churn_platform.operator_journey import RetentionOperatorJourney


def sample_journeys() -> list[RetentionOperatorJourney]:
    return [RetentionOperatorJourney(
        journey_id=f"retention-{index + 1:03d}",
        customer_key=f"customer-{index + 1:03d}",
        consent_granted=index % 6 != 0,
        contact_conflict=index % 6 == 1,
        channel_available=index % 6 != 2,
        task_overdue=index % 6 == 3,
        outcome_matured=index % 6 == 5,
    ) for index in range(100)]


class RetentionOperatorInboxTests(unittest.TestCase):
    def test_inbox_prioritises_overdue_work_and_separates_protected_customers(self):
        result = build_retention_operator_inbox(sample_journeys(), visible_limit=12)
        self.assertEqual("RETENTION_INBOX_READY", result["status"])
        self.assertEqual(100, result["journey_count"])
        self.assertEqual(12, result["visible_count"])
        self.assertGreater(result["protected_count"], 0)
        self.assertTrue(all(item["status"] == "PRIORITY" for item in result["focus_items"]))
        self.assertFalse(result["automatic_contact_started"])

    def test_inbox_has_resume_keys_and_contact_safety(self):
        result = build_retention_operator_inbox(sample_journeys())
        self.assertEqual(len(result["focus_items"]), len({
            item["resume_key"] for item in result["focus_items"]
        }))
        self.assertTrue(all(item["primary_action"] != "RECORD_CONSENT_RESTRICTION"
                            for item in result["focus_items"]))

    def test_visible_limit_is_bounded(self):
        with self.assertRaises(ValueError):
            build_retention_operator_inbox(sample_journeys(), visible_limit=30)
