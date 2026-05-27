from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.application_agent import ApplicationRepository, ApplicationWorkflow
from app.config import Settings
from app.webabi.recorder import AuditRecorder


class ApplicationWorkflowTests(unittest.TestCase):
    def test_draft_and_manual_approval_never_claim_auto_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            settings.ensure_directories()
            profile = {
                "name": "Ada Example",
                "email": "ada@example.test",
                "phone": None,
                "location": None,
                "skills": ["Python Linux"],
                "projects": [],
                "work_experience": [],
                "education": [],
                "certifications": [],
                "links": [],
                "constraints_questions_needing_user_confirmation": [],
            }
            profile_path = settings.profile_dir / "candidate_profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            preferences = {
                "candidate_user_confirmed_facts": {
                    "work_authorization_summary": "User-confirmed work authorization answer.",
                    "availability": "User-confirmed availability.",
                    "salary_target": "needs_user_answer",
                }
            }
            (settings.profile_dir / "job_preferences.json").write_text(
                json.dumps(preferences), encoding="utf-8"
            )
            ApplicationRepository(settings).save_jobs(
                [
                    {
                        "id": "abc123",
                        "title": "Python Engineer",
                        "company": "Example",
                        "location": "Remote",
                        "url": "https://jobs.lever.co/example/abc123",
                        "description": "Required: Python",
                        "source": "test",
                    }
                ]
            )
            recorder = AuditRecorder(settings.log_dir / "runs")
            workflow = ApplicationWorkflow(settings, recorder)
            draft = json.loads(workflow.draft("abc123").read_text(encoding="utf-8"))
            self.assertEqual(draft["status"], "draft_only_no_submission")
            self.assertEqual(
                draft["answers"]["work_authorization"],
                "User-confirmed work authorization answer.",
            )
            self.assertEqual(draft["answers"]["availability"], "User-confirmed availability.")
            approval = json.loads(
                workflow.approve_for_manual_submission("abc123").read_text(encoding="utf-8")
            )
            self.assertEqual(approval["execution"], "manual_submission_only")
            audit = recorder.path.read_text(encoding="utf-8")
            self.assertIn("draft_saved_no_submission", audit)
            self.assertIn("approved_manual_submission_required", audit)

    def test_legacy_sponsorship_fact_key_is_generic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            settings.ensure_directories()
            (settings.profile_dir / "candidate_profile.json").write_text(
                json.dumps(
                    {
                        "skills": ["Python"],
                        "projects": [],
                        "work_experience": [],
                        "education": [],
                        "certifications": [],
                        "links": [],
                        "constraints_questions_needing_user_confirmation": [],
                    }
                ),
                encoding="utf-8",
            )
            (settings.profile_dir / "job_preferences.json").write_text(
                json.dumps(
                    {
                        "candidate_user_confirmed_facts": {
                            "nationality": "Exampleland",
                            "needs_work_sponsorship_outside_exampleland": True,
                        }
                    }
                ),
                encoding="utf-8",
            )
            ApplicationRepository(settings).save_jobs(
                [
                    {
                        "id": "abc123",
                        "title": "Python Engineer",
                        "company": "Example",
                        "location": "Remote",
                        "url": "https://jobs.lever.co/example/abc123",
                        "description": "Required: Python",
                        "source": "test",
                    }
                ]
            )
            recorder = AuditRecorder(settings.log_dir / "runs")
            draft = json.loads(
                ApplicationWorkflow(settings, recorder).draft("abc123").read_text(encoding="utf-8")
            )
            self.assertEqual(
                draft["answers"]["work_authorization"],
                "Exampleland citizen; requires employer visa/work authorization sponsorship for roles outside Exampleland.",
            )


if __name__ == "__main__":
    unittest.main()
