from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.whatsapp_notifier import (
    _message_payload,
    load_whatsapp_config,
    whatsapp_ready,
    write_default_whatsapp,
)


class WhatsAppNotifierTests(unittest.TestCase):
    def test_default_whatsapp_config_is_private_and_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.load(Path(tmp))
            path = write_default_whatsapp(settings)
            config = load_whatsapp_config(settings)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(whatsapp_ready(config))
            self.assertFalse(config["enabled"])

    def test_environment_can_enable_whatsapp(self) -> None:
        env = {
            "WHATSAPP_ENABLED": "true",
            "WHATSAPP_ACCESS_TOKEN": "token",
            "WHATSAPP_PHONE_NUMBER_ID": "123",
            "WHATSAPP_RECIPIENT_PHONE": "+201001234567",
            "WHATSAPP_NOTIFY_ON_WORKER_RUN": "true",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env, clear=False):
            config = load_whatsapp_config(Settings.load(Path(tmp)))
            self.assertTrue(whatsapp_ready(config))
            self.assertTrue(config["notify_on_worker_run"])

    def test_text_payload_strips_plus_and_disables_preview(self) -> None:
        payload = _message_payload(
            {"recipient_phone": "+201001234567", "send_mode": "text"},
            "status update",
        )
        self.assertEqual(payload["to"], "201001234567")
        self.assertEqual(payload["type"], "text")
        self.assertFalse(payload["text"]["preview_url"])

    def test_template_payload_uses_configured_template(self) -> None:
        payload = _message_payload(
            {
                "recipient_phone": "+201001234567",
                "send_mode": "template",
                "template_name": "hello_world",
                "template_language": "en_US",
            },
            "ignored for templates",
        )
        self.assertEqual(payload["type"], "template")
        self.assertEqual(payload["template"]["name"], "hello_world")


if __name__ == "__main__":
    unittest.main()
