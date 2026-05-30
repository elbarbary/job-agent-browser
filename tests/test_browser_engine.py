from __future__ import annotations

import unittest

from app.browser_engine import (
    AI_PAGE_CONTEXT_SCRIPT,
    APPLICATION_NAVIGATION_SCRIPT,
    _is_arbeitnow_job_page,
    _choose_application_entry_action,
    _answer_for_field,
    _is_cover_letter_file_field,
    _is_resume_file_field,
    _planner_fill,
    _plan_form_fills,
    _post_submit_errors,
    _safe_submit_button_index,
    _submission_verified,
    _validate_required_fields,
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

    def test_application_entry_navigation_prefers_non_submit_apply(self) -> None:
        action = _choose_application_entry_action(
            [
                {"index": 1, "text": "Submit application", "href": ""},
                {"index": 2, "text": "Apply now", "href": "https://jobs.example/apply"},
            ]
        )
        self.assertIsNotNone(action)
        self.assertEqual(action["index"], 2)
        self.assertIn("field_count", APPLICATION_NAVIGATION_SCRIPT)

    def test_llm_planner_fill_can_only_use_known_answer_keys(self) -> None:
        fields = [{"index": 0, "type": "email", "tag": "input", "label": "Email", "required": True}]
        known_values = {"email": "candidate@example.com"}

        self.assertEqual(
            _planner_fill({"field_index": 0, "answer_key": "email"}, fields, known_values, {}),
            {"index": 0, "label": "Email", "value": "candidate@example.com", "kind": "text"},
        )
        self.assertIsNone(
            _planner_fill({"field_index": 0, "answer_key": "salary_expectation"}, fields, known_values, {})
        )

    def test_terms_checkbox_can_be_filled_only_when_authorized(self) -> None:
        fields = [
            {
                "index": 0,
                "type": "checkbox",
                "tag": "input",
                "label": "I agree to the privacy policy and application terms",
                "required": True,
            }
        ]
        fills, errors = _plan_form_fills(fields, {}, {"allow_application_terms_checkbox": True})
        self.assertEqual(fills[0]["kind"], "checkbox")
        self.assertEqual(errors, [])

    def test_resume_upload_does_not_target_cover_letter_or_photo(self) -> None:
        fields = [
            {"index": 0, "type": "file", "tag": "input", "label": "Photo", "required": False},
            {"index": 1, "type": "file", "tag": "input", "label": "CV or resume", "required": True},
            {"index": 2, "type": "file", "tag": "input", "label": "Cover letter", "required": True},
        ]
        self.assertFalse(_is_resume_file_field(fields[0]))
        self.assertTrue(_is_resume_file_field(fields[1]))
        self.assertFalse(_is_resume_file_field(fields[2]))
        self.assertTrue(_is_cover_letter_file_field(fields[2]))
        fills, errors = _plan_form_fills(
            fields,
            {},
            {"block_file_uploads": False, "resume_path": __file__},
        )
        self.assertEqual([fill["index"] for fill in fills], [1])
        self.assertIn("Required non-resume file upload needs manual review: Cover letter", errors)

    def test_confirmed_cover_letter_path_can_fill_cover_letter_upload(self) -> None:
        fields = [
            {"index": 0, "type": "file", "tag": "input", "label": "Cover letter", "required": True},
        ]
        fills, errors = _plan_form_fills(
            fields,
            {"cover_letter_path": __file__},
            {"block_file_uploads": False, "resume_path": __file__},
        )
        self.assertEqual([fill["index"] for fill in fills], [0])
        self.assertEqual(errors, [])

    def test_internal_select_names_are_rendered_as_human_review_items(self) -> None:
        fields = [
            {
                "index": 0,
                "type": "select-one",
                "tag": "select",
                "name": "cards[3da58b41-acf5-40a1-945e-c7f047ef8050][field0]",
                "label": "cards[3da58b41-acf5-40a1-945e-c7f047ef8050][field0]",
                "required": True,
                "options": [{"text": "Yes", "value": "yes"}, {"text": "No", "value": "no"}],
            }
        ]
        errors = _validate_required_fields(fields, [], {})
        self.assertIn("Required select field needs manual review: required dropdown with options: Yes, No", errors)

    def test_citizenship_question_is_not_filled_with_candidate_name(self) -> None:
        field = {
            "index": 0,
            "type": "text",
            "tag": "input",
            "label": "Please name all citizenships you have",
            "name": "candidate.openQuestionAnswers.1.content",
            "required": True,
        }
        self.assertIsNone(_answer_for_field(field, {"name": "Ahmed Elbarbary"}))

    def test_confirmed_defaults_can_fill_targeted_questions(self) -> None:
        salary_field = {
            "index": 0,
            "type": "text",
            "tag": "input",
            "label": "Desired annual salary incl. all allowances",
            "required": True,
        }
        self.assertEqual(
            _answer_for_field(
                salary_field,
                {
                    "salary_expectation": "90000 EUR",
                    "application_default_answers": {"desired annual salary": "90000 EUR"},
                },
            ),
            "90000 EUR",
        )

    def test_required_unknown_fields_still_block_after_planning(self) -> None:
        fields = [
            {"index": 0, "type": "text", "tag": "input", "label": "Email", "required": True},
            {"index": 1, "type": "text", "tag": "input", "label": "Desired salary", "required": True},
        ]
        errors = _validate_required_fields(
            fields,
            [{"index": 0, "label": "Email", "value": "candidate@example.com", "kind": "text"}],
            {"block_unknown_required_fields": True},
        )
        self.assertEqual(errors, ["Required field has no known answer: Desired salary"])

    def test_safe_submit_button_validation_blocks_destructive_actions(self) -> None:
        self.assertTrue(_safe_submit_button_index([{"index": 0, "text": "Submit application"}], 0))
        self.assertFalse(_safe_submit_button_index([{"index": 0, "text": "Withdraw application"}], 0))


if __name__ == "__main__":
    unittest.main()
