from __future__ import annotations

import unittest

from app.config import Settings
from app.worker import (
    DEFAULT_AUTOPILOT_JOB_TIMEOUT_SECONDS,
    DEFAULT_WORKER_CYCLE_TIMEOUT_SECONDS,
    _new_worker_status,
    _update_worker_status,
    _discovery_plan,
    _draft_path,
    _existing_job_ids,
    select_worker_jobs,
)


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

    def test_autopilot_scan_skips_already_handled_jobs(self) -> None:
        ranked = [
            {"id": f"job-{index}", "match_score": score}
            for index, score in enumerate([100, 95, 90, 85, 80, 75, 70, 65], start=1)
        ]

        draft_jobs, autopilot_candidates = select_worker_jobs(
            ranked,
            min_score=70,
            draft_top_n=2,
            autopilot_scan_top_n=4,
            handled_job_ids={"job-1", "job-2", "job-3"},
        )

        self.assertEqual([job["id"] for job in draft_jobs], ["job-1", "job-2"])
        self.assertEqual([job["id"] for job in autopilot_candidates], ["job-4", "job-5", "job-6", "job-7"])

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
            self.assertEqual(
                _discovery_plan(settings, {"discovery_mode": "alternate", "source_urls": ["https://jobs.example.test/1"]}),
                (False, True, "source_urls"),
            )
            self.assertEqual(_discovery_plan(settings, {"discovery_mode": "alternate", "source_urls": []}), (True, False, "online"))

    def test_worker_source_url_timeout_default_is_configured(self) -> None:
        from app.watchlist import DEFAULT_WATCHLIST

        self.assertEqual(DEFAULT_WATCHLIST["source_url_timeout_seconds"], 120)

    def test_worker_status_helpers_include_heartbeat_and_completion_timestamps(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            settings.ensure_directories()
            status = _new_worker_status(
                watchlist={"discovery_mode": "alternate"},
                phase="starting",
                in_progress=True,
                started_at="2026-01-01T00:00:00+00:00",
            )
            self.assertEqual(status["phase"], "starting")
            self.assertIsNone(status["finished_at"])
            _update_worker_status(settings, status, phase="complete", in_progress=False)
            written = json.loads((settings.applications_dir / "worker_status.json").read_text(encoding="utf-8"))
            self.assertEqual(written["phase"], "complete")
            self.assertFalse(written["in_progress"])
            self.assertIsNotNone(written["updated_at"])
            self.assertIsNotNone(written["finished_at"])

    def test_worker_cycle_timeout_default_is_bounded(self) -> None:
        self.assertGreaterEqual(DEFAULT_WORKER_CYCLE_TIMEOUT_SECONDS, 60)

    def test_autopilot_job_timeout_default_is_bounded(self) -> None:
        self.assertGreaterEqual(DEFAULT_AUTOPILOT_JOB_TIMEOUT_SECONDS, 30)

    def test_existing_job_ids_reads_json_stems(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "abc.json").write_text("{}")
            (directory / "ignore.txt").write_text("{}")

            self.assertEqual(_existing_job_ids(directory), {"abc"})

    def test_draft_path_points_to_private_drafts_directory(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))

            self.assertEqual(
                _draft_path(settings, "abc123"),
                settings.applications_dir / "drafts" / "abc123.json",
            )


if __name__ == "__main__":
    unittest.main()
