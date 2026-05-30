from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.application_agent import ApplicationRepository
from app.config import Settings
from app.question_queue import add_questions
from app.tracker import (
    format_manual_queue,
    format_tracker_chat,
    format_tracker_text,
    render_autopilot_html,
    render_onboarding_html,
    render_provider_html,
    render_questions_html,
    render_tracker_html,
    render_worker_html,
    render_search_html,
    render_manual_queue_html,
    tracker_status,
    write_tracker_html,
)


class TrackerTests(unittest.TestCase):
    def test_tracker_combines_jobs_drafts_and_submissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            settings.ensure_directories()
            ApplicationRepository(settings).save_jobs(
                [
                    {
                        "id": "job-1",
                        "title": "Product Engineer",
                        "company": "Example",
                        "location": "Remote",
                        "url": "https://jobs.example.test/job-1",
                        "match_score": 91,
                        "risks_uncertainties": ["needs_user_answer: salary"],
                    },
                    {
                        "id": "job-2",
                        "title": "AI Product Manager",
                        "company": "Example",
                        "location": "Remote",
                        "url": "https://jobs.example.test/job-2",
                        "match_score": 88,
                        "risks_uncertainties": [],
                    }
                ]
            )
            (settings.applications_dir / "drafts" / "job-1.json").write_text(
                json.dumps({"answers": {"name": "Ada Example"}}),
                encoding="utf-8",
            )
            (settings.applications_dir / "submissions" / "job-1.json").write_text(
                json.dumps({"submitted_at": "2026-01-01T00:00:00+00:00"}),
                encoding="utf-8",
            )
            (settings.applications_dir / "status_overrides").mkdir(parents=True, exist_ok=True)
            (settings.applications_dir / "status_overrides" / "job-1.json").write_text(
                json.dumps({"status": "skipped"}),
                encoding="utf-8",
            )
            (settings.applications_dir / "prepared" / "job-2.json").write_text(
                json.dumps(
                    {
                        "prepared_at": "2026-01-03T00:00:00+00:00",
                        "result": {"manual_review_url": "https://example.test/review"},
                    }
                ),
                encoding="utf-8",
            )
            (settings.applications_dir / "submission_attempts" / "job-2.json").write_text(
                json.dumps({"attempted_at": "2026-01-02T00:00:00+00:00"}),
                encoding="utf-8",
            )
            status = tracker_status(settings)
            self.assertEqual(status["counts"]["jobs"], 2)
            self.assertEqual(status["counts"]["drafts"], 1)
            self.assertEqual(status["counts"]["prepared"], 1)
            self.assertEqual(status["counts"]["manual_submit_queue"], 1)
            self.assertEqual(status["counts"]["submitted"], 0)
            self.assertEqual(status["counts"]["unverified_submit_clicks"], 0)
            self.assertEqual(status["counts"]["skipped"], 1)
            self.assertEqual(status["counts"]["broken_links"], 0)
            self.assertEqual(status["jobs"][0]["state"], "skipped")
            self.assertEqual(status["jobs"][1]["state"], "prepared_manual_submit")
            self.assertIn("Product Engineer", format_tracker_text(status))
            self.assertIn("Manual queue:", format_tracker_chat(status))
            self.assertIn("Submitted:", format_tracker_chat(status))
            self.assertIn("Review prepared application", render_tracker_html(status))
            self.assertIn("Manual submit options", render_tracker_html(status))
            self.assertIn("Edit local status", render_tracker_html(status))
            self.assertIn("Mark submitted", render_tracker_html(status))
            self.assertIn("Broken link", render_tracker_html(status))
            self.assertIn("manual queue", render_tracker_html(status))
            self.assertIn("Onboarding", render_tracker_html(status))
            self.assertIn("AI Providers", render_tracker_html(status))
            self.assertIn("Autopilot", render_tracker_html(status))
            self.assertIn("Worker", render_tracker_html(status))
            self.assertIn("Web Search", render_tracker_html(status))
            self.assertIn("Questions", render_tracker_html(status))
            self.assertIn("Manual submit options", render_manual_queue_html(status))
            self.assertIn("Upload CV", render_onboarding_html(settings, status))
            self.assertIn("Extra default answers", render_onboarding_html(settings, status))
            self.assertIn("Local Ollama", render_provider_html(settings, status))
            self.assertIn("AI fills, I submit", render_autopilot_html(settings, status))
            self.assertIn("Worker Control", render_worker_html(settings, status))
            self.assertIn("Local Web Search", render_search_html(settings, status))
            self.assertIn("Discovery Sources", render_search_html(settings, status))
            self.assertIn("Question Queue", render_questions_html(settings, status))
            self.assertIn("Retry status", render_questions_html(settings, status))
            self.assertIn("Go back and fill jobs with answered questions", render_questions_html(settings, status))
            self.assertIn("Draft answers", render_tracker_html(status))
            self.assertIn("Manual Submit Queue", format_manual_queue(status))
            self.assertIn("--prepare", format_manual_queue(status))
            self.assertEqual(write_tracker_html(settings).stat().st_mode & 0o777, 0o600)

    def test_questions_page_hides_answered_and_opaque_internal_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            settings.ensure_directories()
            add_questions(settings, ["needs_user_answer: desired salary is not stated"], job_id="job-1")
            add_questions(
                settings,
                ["Required select field needs manual review: cards[3da58b41-acf5-40a1-945e-c7f047ef8050][field0]"],
                job_id="job-2",
            )
            html = render_questions_html(settings, tracker_status(settings))
            self.assertIn("desired salary", html)
            self.assertNotIn("cards[", html)


if __name__ == "__main__":
    unittest.main()
