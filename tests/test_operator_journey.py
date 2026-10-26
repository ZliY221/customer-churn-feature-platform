import unittest

from churn_platform.operator_journey import (
    RetentionOperatorJourney,
    rehearse_retention_journeys,
)


def sample_journeys() -> list[RetentionOperatorJourney]:
    rows = []
    for index in range(100):
        case = index % 6
        rows.append(RetentionOperatorJourney(
            journey_id=f"retention-{index + 1:03d}",
            customer_key=f"customer-{index + 1:03d}",
            consent_granted=case != 0,
            contact_conflict=case == 1,
            channel_available=case != 2,
            task_overdue=case == 3,
            outcome_matured=case == 5,
        ))
    return rows


class RetentionOperatorJourneyTests(unittest.TestCase):
    def test_one_hundred_journeys_have_safe_next_actions(self):
        result = rehearse_retention_journeys(sample_journeys())
        self.assertEqual("USABILITY_REHEARSAL_PASSED", result["status"])
        self.assertEqual(100, result["journey_count"])
        self.assertEqual(0, result["unresolved_count"])
        self.assertTrue(all(row["primary_action"] for row in result["journeys"]))
        self.assertFalse(result["automatic_contact_started"])

    def test_consent_protection_precedes_other_actions(self):
        result = rehearse_retention_journeys(sample_journeys())
        first = result["journeys"][0]
        self.assertEqual("PROTECTED", first["status"])
        self.assertEqual("RECORD_CONSENT_RESTRICTION", first["primary_action"])

    def test_rejects_incomplete_rehearsal(self):
        with self.assertRaises(ValueError):
            rehearse_retention_journeys(sample_journeys()[:99])
