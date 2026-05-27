"""Private user-confirmed job preferences for ranking and filtering."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings


DEFAULT_USER_PREFERENCES: dict[str, Any] = {
    "version": 1,
    "generated_at": None,
    "source": "user_editable_template",
    "target_roles": [],
    "preferred_keywords": [],
    "priority_order": [
        {
            "name": "First priority",
            "locations": [],
            "requires_sponsorship": "needs_user_answer",
            "weight": 40,
        },
        {
            "name": "Second priority",
            "locations": [],
            "requires_sponsorship": "needs_user_answer",
            "weight": 28,
        },
        {
            "name": "Remote",
            "locations": ["remote"],
            "requires_sponsorship": "needs_user_answer",
            "weight": 18,
        },
    ],
    "candidate_user_confirmed_facts": {
        "profile_reviewed": False,
        "contact_email": "needs_user_answer",
        "nationality": "needs_user_answer",
        "home_country": "needs_user_answer",
        "work_authorization_summary": "needs_user_answer",
        "needs_work_sponsorship_outside_home_country": "needs_user_answer",
        "salary_target": "needs_user_answer",
        "availability": "needs_user_answer",
        "eligible_role_types": [],
        "target_product_roles": "needs_user_answer",
    },
    "hard_rules": [
        "Do not apply where the candidate is clearly ineligible for sponsorship.",
        "Do not invent work authorization, salary, certifications, education, or experience.",
        "If a form asks for salary expectation, exact work authorization status, start date, or relocation details not already stated, mark needs_user_answer.",
    ],
}


def preferences_path(settings: Settings) -> Path:
    return settings.profile_dir / "job_preferences.json"


def write_default_preferences(settings: Settings) -> Path:
    settings.ensure_directories()
    payload = DEFAULT_USER_PREFERENCES.copy()
    payload["generated_at"] = datetime.now(UTC).isoformat()
    path = preferences_path(settings)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def load_preferences(settings: Settings) -> dict[str, Any]:
    path = preferences_path(settings)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
