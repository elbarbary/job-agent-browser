"""Optional private Telegram notifications for tracker updates."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .config import Settings


DEFAULT_TELEGRAM_CONFIG: dict[str, Any] = {
    "version": 1,
    "generated_at": None,
    "enabled": False,
    "bot_token": "",
    "chat_id": "",
    "notify_on_worker_run": False,
    "notes": [
        "Create a Telegram bot with BotFather, message it once, then put bot_token and chat_id here.",
        "This file is private and ignored by git.",
        "Alternatively set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.",
    ],
}


class TelegramConfigurationError(ValueError):
    """Raised when Telegram notifications are requested but not configured."""


def telegram_config_path(settings: Settings) -> Path:
    return settings.profile_dir / "telegram.json"


def write_default_telegram(settings: Settings) -> Path:
    settings.ensure_directories()
    payload = DEFAULT_TELEGRAM_CONFIG.copy()
    payload["generated_at"] = datetime.now(UTC).isoformat()
    path = telegram_config_path(settings)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def load_telegram_config(settings: Settings) -> dict[str, Any]:
    config = DEFAULT_TELEGRAM_CONFIG.copy()
    path = telegram_config_path(settings)
    if path.exists():
        config.update(json.loads(path.read_text(encoding="utf-8")))
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        config["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.getenv("TELEGRAM_CHAT_ID"):
        config["chat_id"] = os.environ["TELEGRAM_CHAT_ID"]
    if os.getenv("TELEGRAM_NOTIFY_ON_WORKER_RUN"):
        config["notify_on_worker_run"] = os.environ["TELEGRAM_NOTIFY_ON_WORKER_RUN"].casefold() == "true"
    if os.getenv("TELEGRAM_ENABLED"):
        config["enabled"] = os.environ["TELEGRAM_ENABLED"].casefold() == "true"
    return config


def telegram_ready(config: dict[str, Any]) -> bool:
    return bool(config.get("enabled") and config.get("bot_token") and config.get("chat_id"))


def send_telegram_message(settings: Settings, text: str) -> None:
    config = load_telegram_config(settings)
    if not telegram_ready(config):
        raise TelegramConfigurationError("Telegram is not enabled or missing bot_token/chat_id.")
    token = str(config["bot_token"])
    chat_id = str(config["chat_id"])
    response = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        },
        timeout=30.0,
    )
    response.raise_for_status()
