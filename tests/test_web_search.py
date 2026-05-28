from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app.config import ConfigurationError, Settings
from app.web_search import _is_external_https_url, build_job_search_terms


class WebSearchTests(unittest.TestCase):
    def test_search_terms_target_job_hosts(self) -> None:
        terms = build_job_search_terms("AI product engineer", "Switzerland")
        self.assertIn("site:jobs.lever.co", terms)
        self.assertIn("site:jobs.ashbyhq.com", terms)
        self.assertIn("Switzerland", terms)

    def test_rejects_local_and_non_https_urls(self) -> None:
        self.assertFalse(_is_external_https_url("http://jobs.lever.co/example"))
        self.assertFalse(_is_external_https_url("https://127.0.0.1/private"))
        self.assertFalse(_is_external_https_url("https://localhost/private"))
        self.assertTrue(_is_external_https_url("https://jobs.lever.co/example/role"))

    def test_searxng_must_be_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(
                "JOB_AGENT_WEB_SEARCH_PROVIDER=searxng\n"
                "JOB_AGENT_SEARXNG_URL=http://example.com:8080\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(ConfigurationError):
                    Settings.load(Path(tmp))


if __name__ == "__main__":
    unittest.main()
