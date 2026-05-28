from __future__ import annotations

import unittest

from app.browser_engine import (
    AI_PAGE_CONTEXT_SCRIPT,
    _is_arbeitnow_job_page,
    _post_submit_errors,
    _submission_verified,
)


class BrowserEngineHelperTests(unittest.TestCase):
    def test_arbeitnow_job_page_detection(self) -> None:
        self.assertTrue(
            _is_arbeitnow_job_page(
                "https://www.arbeitnow.com/jobs/companies/example/product-engineer-berlin-123"
            )
        )
        self.assertFalse(
            _is_arbeitnow_job_page(
                "https://www.arbeitnow.com/jobs/companies/example/product-engineer-berlin-123/apply"
            )
        )
        self.assertFalse(_is_arbeitnow_job_page("https://jobs.lever.co/example/123"))

    def test_submission_verification_uses_arbeitnow_success_state(self) -> None:
        self.assertTrue(
            _submission_verified(
                {
                    "success_visible": True,
                    "success_text": "Your job application has been sent successfully. Good luck!",
                }
            )
        )
        self.assertTrue(
            _submission_verified(
                {"text": "Your job application has been sent successfully. Good luck!"}
            )
        )

    def test_visible_post_submit_errors_are_field_scoped(self) -> None:
        errors = _post_submit_errors(
            {
                "visible_errors": [
                    {"id": "error-terms", "text": "The terms field is required."},
                    {"id": "error-email", "text": "The email must be valid."},
                ]
            }
        )
        self.assertEqual(
            errors,
            ["terms: The terms field is required.", "email: The email must be valid."],
        )

    def test_ai_page_context_script_exposes_browser_semantics(self) -> None:
        for expected in ("forms", "fields", "actions", "headings", "visible_errors", "risk_hint"):
            self.assertIn(expected, AI_PAGE_CONTEXT_SCRIPT)


if __name__ == "__main__":
    unittest.main()
