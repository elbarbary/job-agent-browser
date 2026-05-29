from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.llm_client import LocalLLMClient, LocalLLMError


class LLMClientTests(unittest.TestCase):
    def test_external_provider_status_uses_env_keys_without_storing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            settings.ensure_directories()
            (settings.profile_dir / "llm_providers.json").write_text(
                json.dumps({"active_provider": "openai", "models": {"openai": "test-model"}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}, clear=False):
                status = LocalLLMClient(settings).status()
            self.assertEqual(status["provider"], "openai")
            self.assertEqual(status["model"], "test-model")
            self.assertTrue(status["api_key_present"])
            self.assertNotIn("secret", json.dumps(status))

    def test_external_provider_requires_api_key_for_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            settings.ensure_directories()
            (settings.profile_dir / "llm_providers.json").write_text(
                json.dumps({"active_provider": "deepseek", "models": {"deepseek": "test-model"}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
                with self.assertRaises(LocalLLMError):
                    LocalLLMClient(settings).chat("hello")


if __name__ == "__main__":
    unittest.main()
