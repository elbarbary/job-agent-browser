from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.application_agent import ApplicationRepository
from app.config import Settings
from app.tracker import format_tracker_chat, format_tracker_text, render_tracker_html, tracker_status, write_tracker_html


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
            status = tracker_status(settings)
            self.assertEqual(status["counts"]["jobs"], 1)
            self.assertEqual(status["counts"]["drafts"], 1)
            self.assertEqual(status["counts"]["submitted"], 1)
            self.assertEqual(status["jobs"][0]["state"], "submitted")
            self.assertIn("Product Engineer", format_tracker_text(status))
            self.assertIn("Submitted:", format_tracker_chat(status))
            self.assertIn("Draft answers", render_tracker_html(status))
            self.assertEqual(write_tracker_html(settings).stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
