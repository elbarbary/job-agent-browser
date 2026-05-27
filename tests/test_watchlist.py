from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.watchlist import load_watchlist, write_default_watchlist


class WatchlistTests(unittest.TestCase):
    def test_default_watchlist_is_private_and_non_submitting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            path = write_default_watchlist(settings)
            watchlist = load_watchlist(settings)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertGreater(len(watchlist["queries"]), 0)
            self.assertTrue(watchlist["public_feeds_enabled"])
            self.assertFalse(watchlist["queries_enabled"])
            self.assertEqual(watchlist["auto_draft_top_n"], 5)
            self.assertEqual(watchlist["min_auto_draft_score"], 45)
            self.assertIn("never submits", " ".join(watchlist["notes"]).lower())


if __name__ == "__main__":
    unittest.main()
