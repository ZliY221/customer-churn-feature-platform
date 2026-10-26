import unittest

from churn_platform.contact_fatigue import ContactFatigueCandidate, assess_contact_fatigue


class ContactFatigueTests(unittest.TestCase):
    def test_assessment_protects_consent_and_contact_limits(self) -> None:
        result = assess_contact_fatigue([
            ContactFatigueCandidate("c-1", 0, 1, 100, 0, True, "sms"),
            ContactFatigueCandidate("c-2", 1, 3, 12, 0, True, "phone"),
            ContactFatigueCandidate("c-3", 0, 1, 100, 0, False, "email"),
            ContactFatigueCandidate("c-4", 1, 3, 100, 2, True, "sms"),
        ])
        self.assertEqual({"eligible": 1, "cooldown": 1, "review": 1, "protected": 1}, result["counts"])
        self.assertEqual("WAIT_FOR_COOLDOWN", result["customers"][1]["recommended_action"])
        self.assertFalse(result["automatic_contact_started"])

    def test_weekly_contact_limit_starts_cooldown(self) -> None:
        result = assess_contact_fatigue([
            ContactFatigueCandidate("c-1", 2, 4, 100, 0, True, "sms")
        ])
        self.assertEqual("WEEKLY_CONTACT_LIMIT_REACHED", result["customers"][0]["reasons"][0])

    def test_inconsistent_contact_history_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            assess_contact_fatigue([
                ContactFatigueCandidate("c-1", 3, 2, 100, 0, True, "sms")
            ])
