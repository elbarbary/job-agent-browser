from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.autopilot import (
    AUTHORIZATION_PHRASE,
    autopilot_enabled,
    decide_autopilot_for_job,
    host_allowed,
    load_autopilot,
    write_default_autopilot,
)
from app.config import Settings


class AutopilotTests(unittest.TestCase):
    def test_default_autopilot_is_private_and_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            path = write_default_autopilot(settings)
            config = load_autopilot(settings)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(autopilot_enabled(config))
            self.assertEqual(config["allowed_submit_hosts"], [])

    def test_standing_authorization_and_host_allowlist_are_required(self) -> None:
        config = {
            "enabled": True,
            "submit_without_per_job_confirmation": True,
            "standing_authorization": AUTHORIZATION_PHRASE,
            "allowed_submit_hosts": ["jobs.example.test"],
            "min_match_score": 80,
        }
        self.assertTrue(autopilot_enabled(config))
        self.assertTrue(host_allowed("https://jobs.example.test/role", config))
        self.assertFalse(host_allowed("https://other.example.test/role", config))

    def test_decision_blocks_low_score_unknown_identity_and_unlisted_host(self) -> None:
        decision = decide_autopilot_for_job(
            {
                "url": "https://unlisted.example.test/role",
                "match_score": 10,
            },
            {
                "name": "needs_user_answer",
                "email": "ada@example.test",
            },
            {
                "enabled": True,
                "submit_without_per_job_confirmation": True,
                "standing_authorization": AUTHORIZATION_PHRASE,
                "allowed_submit_hosts": ["jobs.example.test"],
                "min_match_score": 80,
            },
        )
        self.assertFalse(decision.allowed)
        self.assertGreaterEqual(len(decision.reasons), 3)


if __name__ == "__main__":
    unittest.main()
