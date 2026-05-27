"""Private profile review helpers for gating unattended submissions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .cv_store import load_profile
from .preferences import load_preferences


CONFIRM_PROFILE_PHRASE = "CONFIRM PROFILE"


def profile_review_path(settings: Settings) -> Path:
    return settings.profile_dir / "profile_review.md"


def build_profile_review(settings: Settings) -> Path:
    settings.ensure_directories()
    profile = load_profile(settings)
    preferences = load_preferences(settings)
    facts = preferences.get("candidate_user_confirmed_facts") or {}
    report = _render_profile_review(settings, profile, facts)
    path = profile_review_path(settings)
    path.write_text(report, encoding="utf-8")
    path.chmod(0o600)
    return path


def mark_profile_reviewed(settings: Settings) -> Path:
    settings.ensure_directories()
    path = settings.profile_dir / "job_preferences.json"
    preferences = load_preferences(settings) or {}
    facts = preferences.setdefault("candidate_user_confirmed_facts", {})
    facts["profile_reviewed"] = True
    facts["profile_reviewed_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(preferences, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    build_profile_review(settings)
    return path


def _render_profile_review(settings: Settings, profile: dict[str, Any], facts: dict[str, Any]) -> str:
    legacy_home_country = _legacy_sponsorship_home_country(facts)
    home_country = facts.get("home_country") or legacy_home_country
    sponsorship = _sponsorship_value(facts, legacy_home_country)
    work_authorization = facts.get("work_authorization_summary") or _derived_work_authorization(facts, home_country)
    lines = [
        "# Candidate Profile Review",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "Review these private facts before enabling unattended submissions.",
        "If anything is wrong, edit `candidate_profile.json` or `job_preferences.json`, then rerun this command.",
        "",
        "## Review Status",
        "",
        f"- Profile reviewed: {_yes_no(facts.get('profile_reviewed') is True)}",
        f"- Reviewed at: {facts.get('profile_reviewed_at') or 'not yet reviewed'}",
        "",
        "## Identity",
        "",
        f"- Name: {_value(profile.get('name'))}",
        f"- Extracted email: {_value(profile.get('email'))}",
        f"- Confirmed contact email: {_value(facts.get('contact_email') or facts.get('email'))}",
        f"- Phone: {_value(profile.get('phone'))}",
        f"- Location: {_value(profile.get('location'))}",
        "",
        "## User-Confirmed Application Facts",
        "",
        f"- Nationality: {_value(facts.get('nationality'))}",
        f"- Home country: {_value(home_country)}",
        f"- Work authorization: {_value(work_authorization)}",
        f"- Sponsorship outside home country: {_value(sponsorship)}",
        f"- Salary target: {_value(facts.get('salary_target'))}",
        f"- Availability: {_value(facts.get('availability'))}",
        f"- Eligible role types: {_value(facts.get('eligible_role_types'))}",
        "",
        "## Extraction Checks",
        "",
        _section(None, _extraction_checks(profile, facts, legacy_home_country)),
        "",
        "## Extracted CV Sections",
        "",
        _section("Education", profile.get("education")),
        _section("Work Experience", profile.get("work_experience")),
        _section("Projects", profile.get("projects")),
        _section("Skills", profile.get("skills")),
        _section("Languages", profile.get("languages")),
        _section("Certifications", profile.get("certifications")),
        _section("Links", profile.get("links")),
        "",
        "## Questions Still Needing User Confirmation",
        "",
        _section(None, profile.get("constraints_questions_needing_user_confirmation")),
        "",
        "## To Unlock Autopilot",
        "",
        "Only after the facts above are correct, run:",
        "",
        "```bash",
        ".venv/bin/python -m app.main confirm-profile",
        "```",
        "",
        f"Then type `{CONFIRM_PROFILE_PHRASE}` when prompted.",
        "",
        "Private source files:",
        f"- {settings.profile_dir / 'candidate_profile.json'}",
        f"- {settings.profile_dir / 'job_preferences.json'}",
        f"- {settings.profile_dir / 'cv_extracted.md'}",
        "",
    ]
    return "\n".join(lines)


def _section(title: str | None, values: Any) -> str:
    lines: list[str] = []
    if title:
        lines.extend([f"### {title}", ""])
    if not values:
        lines.append("- needs_user_answer")
    elif isinstance(values, list):
        lines.extend(f"- {_value(item)}" for item in values[:50])
    else:
        lines.append(f"- {_value(values)}")
    return "\n".join(lines)


def _value(value: Any) -> str:
    if value in (None, "", []):
        return "needs_user_answer"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(_value(item) for item in value) if value else "needs_user_answer"
    return str(value)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _legacy_sponsorship_home_country(facts: dict[str, Any]) -> str | None:
    prefix = "needs_work_sponsorship_outside_"
    for key, value in facts.items():
        if key == "needs_work_sponsorship_outside_home_country":
            continue
        if value is True and isinstance(key, str) and key.startswith(prefix):
            return key.removeprefix(prefix).replace("_", " ").title()
    return None


def _sponsorship_value(facts: dict[str, Any], legacy_home_country: str | None) -> Any:
    value = facts.get("needs_work_sponsorship_outside_home_country")
    if value not in (None, "", "needs_user_answer"):
        return value
    if legacy_home_country:
        return f"yes (from legacy key outside {legacy_home_country})"
    return value


def _derived_work_authorization(facts: dict[str, Any], home_country: Any) -> str | None:
    nationality = facts.get("nationality")
    if nationality in (None, "", "needs_user_answer") or home_country in (None, "", "needs_user_answer"):
        return None
    needs_sponsorship = facts.get("needs_work_sponsorship_outside_home_country") is True
    needs_sponsorship = needs_sponsorship or _legacy_sponsorship_home_country(facts) is not None
    if not needs_sponsorship:
        return None
    return (
        f"{nationality} citizen; requires employer visa/work authorization sponsorship "
        f"for roles outside {home_country}."
    )


def _extraction_checks(
    profile: dict[str, Any],
    facts: dict[str, Any],
    legacy_home_country: str | None,
) -> list[str]:
    checks: list[str] = []
    extracted_email = profile.get("email")
    confirmed_email = facts.get("contact_email") or facts.get("email")
    if confirmed_email and extracted_email and confirmed_email != extracted_email:
        checks.append("contact_email override differs from extracted email; confirmed email will be used.")
    if not confirmed_email:
        checks.append("missing confirmed contact_email in job_preferences.json.")
    if not profile.get("name"):
        checks.append("missing candidate name in candidate_profile.json.")
    if legacy_home_country and not facts.get("home_country"):
        checks.append("legacy sponsorship key found; set home_country for clearer review output.")
    if legacy_home_country and facts.get("needs_work_sponsorship_outside_home_country") in (None, "needs_user_answer"):
        checks.append("legacy sponsorship key found; set needs_work_sponsorship_outside_home_country=true.")
    if facts.get("salary_target") in (None, "", "needs_user_answer"):
        checks.append("salary_target is unknown; salary fields will remain needs_user_answer.")
    if facts.get("profile_reviewed") is not True:
        checks.append("profile is not marked reviewed; autopilot remains blocked by the profile gate.")
    return checks or ["no obvious extraction warnings from this lightweight check."]
