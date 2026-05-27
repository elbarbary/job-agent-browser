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
                "email": "extracted-wrong@example.test",
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
                    "profile_reviewed": True,
                    "contact_email": "ada@example.test",
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
            self.assertEqual(draft["answers"]["email"], "ada@example.test")
            self.assertEqual(draft["answers"]["_email_source"], "user_confirmed_preferences")
            self.assertTrue(draft["answers"]["_profile_reviewed"])
            self.assertEqual(
                draft["answers"]["work_authorization"],
                "User-confirmed work authorization answer.",
            )
            self.assertEqual(draft["answers"]["availability"], "User-confirmed availability.")
            approval = json.loads(
                workflow.approve_for_manual_submission("abc123").read_text(encoding="utf-8")
            )
            self.assertEqual(approval["execution"], "manual_submission_only")
            submission = json.loads(
                workflow.record_autopilot_submission("abc123", {"submitted": True}).read_text(encoding="utf-8")
            )
            self.assertEqual(submission["execution"], "autopilot_submit_clicked")
            self.assertTrue(workflow.repository.has_submission("abc123"))
            self.assertFalse(workflow.repository.has_submission_attempt("abc123"))
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

    def test_unverified_submission_attempt_is_tracked_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            settings.ensure_directories()
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
            workflow = ApplicationWorkflow(settings, AuditRecorder(settings.log_dir / "runs"))
            self.assertFalse(workflow.repository.has_submission_attempt("abc123"))
            attempt = workflow.record_autopilot_attempt("abc123", {"clicked": True, "submitted": False})
            self.assertTrue(attempt.exists())
            self.assertTrue(workflow.repository.has_submission_attempt("abc123"))
            self.assertFalse(workflow.repository.has_submission("abc123"))


if __name__ == "__main__":
    unittest.main()
