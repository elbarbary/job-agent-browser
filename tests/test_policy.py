from __future__ import annotations

import unittest

from app.policy import PolicyViolation, RiskClass, assert_action_allowed, redact_mapping


class PolicyTests(unittest.TestCase):
    def test_submission_requires_confirmation(self) -> None:
        with self.assertRaises(PolicyViolation):
            assert_action_allowed(RiskClass.JOB_SUBMIT)
        assert_action_allowed(RiskClass.JOB_SUBMIT, confirmed=True)

    def test_payment_remains_blocked_even_if_confirmed(self) -> None:
        with self.assertRaises(PolicyViolation):
            assert_action_allowed(RiskClass.PAYMENT, confirmed=True)

    def test_secret_inputs_are_redacted(self) -> None:
        result = redact_mapping({"email": "candidate@example.com", "smtp_password": "hidden"})
        self.assertEqual(result["email"], "candidate@example.com")
        self.assertEqual(result["smtp_password"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
