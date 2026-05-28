"""Optional private WhatsApp Cloud API notifications for tracker updates."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .config import Settings


DEFAULT_WHATSAPP_CONFIG: dict[str, Any] = {
    "version": 1,
    "generated_at": None,
    "enabled": False,
    "access_token": "",
    "phone_number_id": "",
    "recipient_phone": "",
    "graph_api_version": "v25.0",
    "notify_on_worker_run": False,
    "send_mode": "text",
    "template_name": "hello_world",
    "template_language": "en_US",
    "notes": [
        "Outbound-only WhatsApp Cloud API notifications. No public webhook is created by this app.",
        "This file is private and ignored by git. Do not commit access tokens.",
        "Text messages may require the recipient to message your WhatsApp Business number first, opening Meta's customer-service window.",
        "Set send_mode=template and configure template_name/template_language if you want to send an approved Meta template instead.",
    ],
}


class WhatsAppConfigurationError(ValueError):
    """Raised when WhatsApp notifications are requested but not configured."""


def whatsapp_config_path(settings: Settings) -> Path:
    return settings.profile_dir / "whatsapp.json"


def write_default_whatsapp(settings: Settings) -> Path:
    settings.ensure_directories()
    payload = DEFAULT_WHATSAPP_CONFIG.copy()
    payload["generated_at"] = datetime.now(UTC).isoformat()
    path = whatsapp_config_path(settings)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def load_whatsapp_config(settings: Settings) -> dict[str, Any]:
    config = DEFAULT_WHATSAPP_CONFIG.copy()
    path = whatsapp_config_path(settings)
    if path.exists():
        config.update(json.loads(path.read_text(encoding="utf-8")))
    env_map = {
        "WHATSAPP_ACCESS_TOKEN": "access_token",
        "WHATSAPP_PHONE_NUMBER_ID": "phone_number_id",
        "WHATSAPP_RECIPIENT_PHONE": "recipient_phone",
        "WHATSAPP_GRAPH_API_VERSION": "graph_api_version",
        "WHATSAPP_SEND_MODE": "send_mode",
        "WHATSAPP_TEMPLATE_NAME": "template_name",
        "WHATSAPP_TEMPLATE_LANGUAGE": "template_language",
    }
    for env_name, key in env_map.items():
        if os.getenv(env_name):
            config[key] = os.environ[env_name]
    if os.getenv("WHATSAPP_NOTIFY_ON_WORKER_RUN"):
        config["notify_on_worker_run"] = os.environ["WHATSAPP_NOTIFY_ON_WORKER_RUN"].casefold() == "true"
    if os.getenv("WHATSAPP_ENABLED"):
        config["enabled"] = os.environ["WHATSAPP_ENABLED"].casefold() == "true"
    return config


def whatsapp_ready(config: dict[str, Any]) -> bool:
    return bool(
        config.get("enabled")
        and config.get("access_token")
        and config.get("phone_number_id")
        and config.get("recipient_phone")
    )


def send_whatsapp_message(settings: Settings, text: str) -> None:
    config = load_whatsapp_config(settings)
    if not whatsapp_ready(config):
        raise WhatsAppConfigurationError(
            "WhatsApp is not enabled or missing access_token/phone_number_id/recipient_phone."
        )
    version = str(config.get("graph_api_version") or "v25.0").strip().lstrip("/")
    phone_number_id = str(config["phone_number_id"]).strip()
    payload = _message_payload(config, text[:3900])
    response = httpx.post(
        f"https://graph.facebook.com/{version}/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {config['access_token']}", "Content-Type": "application/json"},
        json=payload,
        timeout=30.0,
    )
    response.raise_for_status()


def _message_payload(config: dict[str, Any], text: str) -> dict[str, Any]:
    recipient = str(config["recipient_phone"]).strip().replace("+", "")
    send_mode = str(config.get("send_mode") or "text").casefold()
    base: dict[str, Any] = {"messaging_product": "whatsapp", "to": recipient}
    if send_mode == "template":
        base.update(
            {
                "type": "template",
                "template": {
                    "name": str(config.get("template_name") or "hello_world"),
                    "language": {"code": str(config.get("template_language") or "en_US")},
                },
            }
        )
        return base
    base.update({"type": "text", "text": {"preview_url": False, "body": text}})
    return base
