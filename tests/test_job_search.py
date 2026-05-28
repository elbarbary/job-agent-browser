from __future__ import annotations

import unittest

from app.job_search import merge_ranked_jobs


class JobSearchTests(unittest.TestCase):
    def test_merge_ranked_jobs_preserves_existing_when_discovery_is_empty(self) -> None:
        existing = [{"id": "old", "url": "https://jobs.example/old", "match_score": 80}]

        self.assertEqual(merge_ranked_jobs(existing, []), existing)

    def test_merge_ranked_jobs_updates_by_url_and_sorts(self) -> None:
        existing = [
            {"id": "old", "url": "https://jobs.example/old", "match_score": 80},
            {"id": "same", "url": "https://jobs.example/same", "match_score": 50},
        ]
        discovered = [
            {"id": "same-new", "url": "https://jobs.example/same", "match_score": 90},
            {"id": "new", "url": "https://jobs.example/new", "match_score": 70},
        ]

        merged = merge_ranked_jobs(existing, discovered)

        self.assertEqual([job["id"] for job in merged], ["same-new", "old", "new"])


if __name__ == "__main__":
    unittest.main()
