"""Private queue of application questions that need user-confirmed answers."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .preferences import load_preferences


def question_queue_path(settings: Settings) -> Path:
    return settings.profile_dir / "application_questions.json"


def _read_queue(settings: Settings) -> dict[str, Any]:
    path = question_queue_path(settings)
    if not path.exists():
        return {"version": 1, "questions": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_queue(settings: Settings, payload: dict[str, Any]) -> Path:
    path = question_queue_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload["updated_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _question_id(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().casefold())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def normalize_question(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^needs_user_answer:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^Required unknown field needs manual review:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^Required non-resume file upload needs manual review:\s*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def is_user_answerable_question(value: str) -> bool:
    text = normalize_question(value)
    if not text:
        return False
    # Some ATS widgets expose only implementation names such as
    # cards[uuid][field0]. Those are not reusable onboarding questions; they
    # need manual review on the filled application page.
    if re.search(r"\bcards\[[^\]]+\]\[field\d+\]", text, flags=re.IGNORECASE):
        return False
    if re.fullmatch(r"(field|input|select|dropdown)\s*\d+", text, flags=re.IGNORECASE):
        return False
    return True


def add_questions(settings: Settings, questions: list[str], *, job_id: str | None = None, job: dict[str, Any] | None = None) -> Path:
    payload = _read_queue(settings)
    existing = {str(item.get("id")): item for item in payload.get("questions", []) if isinstance(item, dict)}
    for raw in questions:
        question = normalize_question(str(raw))
        if not question or not is_user_answerable_question(question):
            continue
        item_id = _question_id(question)
        item = existing.setdefault(
            item_id,
            {
                "id": item_id,
                "question": question,
                "answer": "",
                "status": "needs_user_answer",
                "first_seen_at": datetime.now(UTC).isoformat(),
                "jobs": [],
            },
        )
        if job_id and job_id not in item["jobs"]:
            item["jobs"].append(job_id)
        if job:
            item["last_job"] = {
                "id": job.get("id") or job_id,
                "title": job.get("title"),
                "company": job.get("company"),
                "url": job.get("url"),
            }
    payload["questions"] = sorted(existing.values(), key=lambda item: str(item.get("question", "")))
    return _write_queue(settings, payload)


def unanswered_questions(settings: Settings) -> list[dict[str, Any]]:
    payload = _read_queue(settings)
    return [
        item
        for item in payload.get("questions", [])
        if isinstance(item, dict)
        and not str(item.get("answer") or "").strip()
        and is_user_answerable_question(str(item.get("question") or ""))
    ]


def visible_questions(settings: Settings) -> list[dict[str, Any]]:
    payload = _read_queue(settings)
    return [
        item
        for item in payload.get("questions", [])
        if isinstance(item, dict)
        and not str(item.get("answer") or "").strip()
        and str(item.get("status") or "needs_user_answer") != "resolved"
        and is_user_answerable_question(str(item.get("question") or ""))
    ]


def resolve_questions_for_job(settings: Settings, job_id: str) -> Path:
    payload = _read_queue(settings)
    for item in payload.get("questions", []):
        if not isinstance(item, dict):
            continue
        jobs = [str(value) for value in item.get("jobs", []) if str(value) != job_id]
        item["jobs"] = jobs
        if not jobs:
            item["status"] = "resolved"
            item["resolved_at"] = datetime.now(UTC).isoformat()
    return _write_queue(settings, payload)


def remove_unanswerable_questions(settings: Settings) -> Path:
    payload = _read_queue(settings)
    payload["questions"] = [
        item
        for item in payload.get("questions", [])
        if isinstance(item, dict) and is_user_answerable_question(str(item.get("question") or ""))
    ]
    return _write_queue(settings, payload)


def save_question_answers(settings: Settings, answers: dict[str, str]) -> Path:
    payload = _read_queue(settings)
    for item in payload.get("questions", []):
        if not isinstance(item, dict):
            continue
        answer = answers.get(str(item.get("id")))
        if answer is None:
            continue
        answer = answer.strip()
        item["answer"] = answer
        item["status"] = "answered" if answer else "needs_user_answer"
        item["answered_at"] = datetime.now(UTC).isoformat() if answer else None
    _sync_answers_to_preferences(settings, payload)
    return _write_queue(settings, payload)


def _sync_answers_to_preferences(settings: Settings, payload: dict[str, Any]) -> None:
    preferences = load_preferences(settings)
    facts = dict(preferences.get("candidate_user_confirmed_facts") or {})
    defaults = dict(facts.get("application_default_answers") or {})
    for item in payload.get("questions", []):
        if not isinstance(item, dict):
            continue
        answer = str(item.get("answer") or "").strip()
        question = str(item.get("question") or "").strip()
        if answer and question:
            defaults[question] = answer
    facts["application_default_answers"] = defaults
    preferences["candidate_user_confirmed_facts"] = facts
    path = settings.profile_dir / "job_preferences.json"
    path.write_text(json.dumps(preferences, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
