from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.profile_review import build_profile_review, mark_profile_reviewed


class ProfileReviewTests(unittest.TestCase):
    def test_profile_review_report_and_confirmation_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            settings.ensure_directories()
            (settings.profile_dir / "candidate_profile.json").write_text(
                json.dumps(
                    {
                        "name": "Ada Example",
                        "email": "extracted@example.test",
                        "phone": "needs_user_answer",
                        "location": "Example City",
                        "education": ["Example University"],
                        "work_experience": [],
                        "projects": ["Browser agent"],
                        "skills": ["Python"],
                        "links": [],
                        "constraints_questions_needing_user_confirmation": ["salary"],
                    }
                ),
                encoding="utf-8",
            )
            (settings.profile_dir / "job_preferences.json").write_text(
                json.dumps(
                    {
                        "candidate_user_confirmed_facts": {
                            "profile_reviewed": False,
                            "contact_email": "confirmed@example.test",
                            "availability": "right away",
                        }
                    }
                ),
                encoding="utf-8",
            )

            report_path = build_profile_review(settings)
            report = report_path.read_text(encoding="utf-8")
            self.assertEqual(report_path.stat().st_mode & 0o777, 0o600)
            self.assertIn("Extracted email: extracted@example.test", report)
            self.assertIn("Confirmed contact email: confirmed@example.test", report)
            self.assertIn("Profile reviewed: no", report)

            preferences_path = mark_profile_reviewed(settings)
            preferences = json.loads(preferences_path.read_text(encoding="utf-8"))
            facts = preferences["candidate_user_confirmed_facts"]
            self.assertTrue(facts["profile_reviewed"])
            self.assertIn("profile_reviewed_at", facts)
            self.assertEqual(preferences_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
