from __future__ import annotations

import unittest

from app.config import Settings
from app.worker import _discovery_plan, select_worker_jobs


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

    def test_discovery_modes_include_alternate(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            self.assertEqual(_discovery_plan(settings, {"discovery_mode": "online"}), (True, False, "online"))
            self.assertEqual(_discovery_plan(settings, {"discovery_mode": "source_urls"}), (False, True, "source_urls"))
            self.assertEqual(_discovery_plan(settings, {"discovery_mode": "both"}), (True, True, "both"))
            self.assertEqual(_discovery_plan(settings, {"discovery_mode": "alternate"}), (True, False, "online"))
            settings.ensure_directories()
            (settings.applications_dir / "worker_status.json").write_text('{"discovery_lane": "online"}')
            self.assertEqual(_discovery_plan(settings, {"discovery_mode": "alternate"}), (False, True, "source_urls"))

    def test_worker_source_url_timeout_default_is_configured(self) -> None:
        from app.watchlist import DEFAULT_WATCHLIST

        self.assertEqual(DEFAULT_WATCHLIST["source_url_timeout_seconds"], 120)


if __name__ == "__main__":
    unittest.main()
