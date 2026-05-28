from __future__ import annotations

import unittest

from app.worker import select_worker_jobs


class WorkerSelectionTests(unittest.TestCase):
    def test_autopilot_scan_can_look_past_visible_draft_window(self) -> None:
        ranked = [
            {"id": f"job-{index}", "match_score": score}
            for index, score in enumerate([100, 95, 90, 85, 80, 75, 70, 65], start=1)
        ]

        draft_jobs, autopilot_candidates = select_worker_jobs(
            ranked,
            min_score=70,
            draft_top_n=2,
            autopilot_scan_top_n=6,
        )

        self.assertEqual([job["id"] for job in draft_jobs], ["job-1", "job-2"])
        self.assertEqual(
            [job["id"] for job in autopilot_candidates],
            ["job-1", "job-2", "job-3", "job-4", "job-5", "job-6"],
        )


if __name__ == "__main__":
    unittest.main()
