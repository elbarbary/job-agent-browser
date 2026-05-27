from __future__ import annotations

import unittest

from app.job_profile import RankedJob, match_job


class JobProfileTests(unittest.TestCase):
    def test_unknown_application_answers_remain_unknown(self) -> None:
        profile = {
            "name": "Ada Example",
            "email": "ada@example.test",
            "phone": None,
            "location": None,
            "skills": ["Python Terraform Linux"],
            "projects": [],
            "work_experience": [],
            "education": [],
            "certifications": [],
            "links": [],
            "constraints_questions_needing_user_confirmation": [],
        }
        job = RankedJob(
            id="job-1",
            title="Python Infrastructure Engineer",
            company="Example",
            location="Remote",
            url="https://jobs.lever.co/example/job-1",
            description="Required: Python and Terraform",
        )
        ranked = match_job(job, profile)
        self.assertGreater(ranked.match_score, 0)
        self.assertEqual(ranked.suggested_application_answers["work_authorization"], "needs_user_answer")

    def test_preferences_boost_target_location_and_sponsorship(self) -> None:
        profile = {
            "skills": ["Python LLM product"],
            "projects": [],
            "work_experience": [],
            "education": [],
            "certifications": [],
        }
        job = RankedJob(
            id="job-2",
            title="AI Product Engineer",
            company="Example",
            location="Example City, Target Country",
            url="https://jobs.lever.co/example/job-2",
            description="We support visa sponsorship and relocation for AI product engineers.",
        )
        ranked = match_job(
            job,
            profile,
            {
                "preferred_keywords": ["ai", "product"],
                "priority_order": [
                    {
                        "name": "Target Country with sponsorship",
                        "locations": ["target country", "example city"],
                        "weight": 40,
                    }
                ],
            },
        )
        self.assertTrue(any("Target Country" in reason for reason in ranked.why_it_matches))
        self.assertGreaterEqual(ranked.match_score, 40)

    def test_preferences_do_not_make_unmatched_job_look_strong(self) -> None:
        profile = {
            "skills": ["Python LLM product"],
            "projects": [],
            "work_experience": [],
            "education": [],
            "certifications": [],
        }
        job = RankedJob(
            id="job-3",
            title="Account Executive",
            company="Example",
            location="Example City, Target Country",
            url="https://example.com/job-3",
            description="Remote sales role with visa sponsorship.",
        )
        ranked = match_job(
            job,
            profile,
            {
                "preferred_keywords": ["remote", "visa sponsorship"],
                "priority_order": [
                    {
                        "name": "Target Country with sponsorship",
                        "locations": ["target country", "example city"],
                        "weight": 40,
                    }
                ],
            },
        )
        self.assertLessEqual(ranked.match_score, 35)
        self.assertTrue(any("low_cv_match" in risk for risk in ranked.risks_uncertainties))


if __name__ == "__main__":
    unittest.main()
