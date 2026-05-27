"""Private opt-in configuration and guardrails for automatic submissions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Settings


AUTHORIZATION_PHRASE = "I AUTHORIZE LOCAL AUTOPILOT SUBMISSIONS"
UNKNOWN_VALUE = "needs_user_answer"


DEFAULT_AUTOPILOT: dict[str, Any] = {
    "version": 1,
    "generated_at": None,
    "enabled": False,
    "submit_without_per_job_confirmation": False,
    "standing_authorization": "",
    "min_match_score": 80,
    "max_submissions_per_run": 1,
    "headless": True,
    "allowed_submit_hosts": [],
    "resume_path": "",
    "block_file_uploads": True,
    "block_required_checkboxes": True,
    "block_unknown_required_fields": True,
    "notes": [
        f"Set standing_authorization exactly to: {AUTHORIZATION_PHRASE}",
        "Keep this file private. It represents standing permission to submit eligible applications.",
        "Autopilot submits only simple forms where required fields can be answered from CV/profile/preferences.",
        "Autopilot blocks file uploads, required checkboxes, payments, destructive actions, and unknown required answers.",
    ],
}


@dataclass(frozen=True)
class AutopilotDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def autopilot_path(settings: Settings) -> Path:
    return settings.profile_dir / "autopilot.json"


def write_default_autopilot(settings: Settings) -> Path:
    settings.ensure_directories()
    payload = DEFAULT_AUTOPILOT.copy()
    payload["generated_at"] = datetime.now(UTC).isoformat()
    path = autopilot_path(settings)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def load_autopilot(settings: Settings) -> dict[str, Any]:
    path = autopilot_path(settings)
    if not path.exists():
        return DEFAULT_AUTOPILOT.copy()
    return json.loads(path.read_text(encoding="utf-8"))


def autopilot_enabled(config: dict[str, Any]) -> bool:
    return (
        config.get("enabled") is True
        and config.get("submit_without_per_job_confirmation") is True
        and config.get("standing_authorization") == AUTHORIZATION_PHRASE
    )


def host_allowed(url: str, config: dict[str, Any]) -> bool:
    allowed = {str(host).casefold() for host in config.get("allowed_submit_hosts") or []}
    if not allowed:
        return False
    host = (urlparse(url).hostname or "").casefold()
    return host in allowed


def is_known(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip() != UNKNOWN_VALUE
    if isinstance(value, list):
        return any(is_known(item) for item in value)
    return True


def decide_autopilot_for_job(
    job: dict[str, Any],
    answers: dict[str, Any],
    config: dict[str, Any],
) -> AutopilotDecision:
    reasons: list[str] = []
    if not autopilot_enabled(config):
        reasons.append("autopilot is not enabled with standing authorization")
    if int(job.get("match_score", 0)) < int(config.get("min_match_score", 80)):
        reasons.append("job match score is below min_match_score")
    if not host_allowed(str(job.get("url", "")), config):
        reasons.append("job host is not in allowed_submit_hosts")
    if not is_known(answers.get("name")):
        reasons.append("candidate name is unknown")
    if not is_known(answers.get("email")):
        reasons.append("candidate email is unknown")
    return AutopilotDecision(allowed=not reasons, reasons=reasons)
