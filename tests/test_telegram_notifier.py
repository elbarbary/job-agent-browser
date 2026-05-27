from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.telegram_notifier import load_telegram_config, telegram_ready, write_default_telegram


class TelegramNotifierTests(unittest.TestCase):
    def test_default_telegram_config_is_private_and_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            path = write_default_telegram(settings)
            config = load_telegram_config(settings)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(telegram_ready(config))

    def test_environment_can_enable_telegram(self) -> None:
        old = {key: os.environ.get(key) for key in ("TELEGRAM_ENABLED", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
        try:
            os.environ["TELEGRAM_ENABLED"] = "true"
            os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"
            os.environ["TELEGRAM_CHAT_ID"] = "456"
            with tempfile.TemporaryDirectory() as tmp:
                config = load_telegram_config(Settings.load(Path(tmp)))
            self.assertTrue(telegram_ready(config))
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
