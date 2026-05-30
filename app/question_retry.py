"""Retry jobs that were blocked on now-answered queued questions."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .application_agent import ApplicationRepository
from .autopilot import host_allowed, load_autopilot
from .config import Settings
from .question_queue import question_queue_path


def retry_status_path(settings: Settings) -> Path:
    return settings.applications_dir / "question_retry_status.json"


def retry_log_path(settings: Settings) -> Path:
    return settings.log_dir / "question_retry.log"


def answered_question_job_ids(settings: Settings) -> list[str]:
    path = question_queue_path(settings)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    job_ids: list[str] = []
    for item in payload.get("questions", []):
        if not isinstance(item, dict) or not str(item.get("answer") or "").strip():
            continue
        for job_id in item.get("jobs", []):
            cleaned = str(job_id).strip()
            if cleaned:
                job_ids.append(cleaned)
    return sorted(set(job_ids))


def retryable_answered_question_jobs(settings: Settings, *, limit: int = 10) -> tuple[list[str], list[dict[str, str]]]:
    repository = ApplicationRepository(settings)
    autopilot_config = load_autopilot(settings)
    retryable: list[str] = []
    skipped: list[dict[str, str]] = []
    for job_id in answered_question_job_ids(settings):
        if len(retryable) >= limit:
            break
        if repository.has_submission(job_id):
            skipped.append({"job_id": job_id, "reason": "already submitted"})
            continue
        if repository.has_prepared(job_id):
            skipped.append({"job_id": job_id, "reason": "already prepared for manual submission"})
            continue
        try:
            job = repository.find_job(job_id)
        except Exception:  # noqa: BLE001 - stale queued questions should not stop the retry batch.
            skipped.append({"job_id": job_id, "reason": "job no longer exists in jobs.json"})
            continue
        if not host_allowed(str(job.get("url") or ""), autopilot_config):
            skipped.append({"job_id": job_id, "reason": "job host is outside the private prepare/submit allowlist"})
            continue
        retryable.append(job_id)
    return retryable, skipped


def read_retry_status(settings: Settings) -> dict[str, Any]:
    path = retry_status_path(settings)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_retry_status(settings: Settings, payload: dict[str, Any]) -> Path:
    path = retry_status_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload["updated_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def start_answered_question_retry(settings: Settings, *, limit: int = 10) -> dict[str, Any]:
    job_ids, skipped = retryable_answered_question_jobs(settings, limit=limit)
    if not job_ids:
        write_retry_status(
            settings,
            {
                "status": "blocked",
                "started_at": datetime.now(UTC).isoformat(),
                "limit": limit,
                "job_ids": [],
                "completed": [],
                "failed": [],
                "skipped": skipped,
            },
        )
        return {"ok": False, "output": "No retryable answered-question jobs are available right now."}
    python = settings.root / ".venv/bin/python"
    log_path = retry_log_path(settings)
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    status = {
        "status": "queued",
        "started_at": datetime.now(UTC).isoformat(),
        "limit": limit,
        "job_ids": job_ids,
        "completed": [],
        "failed": [],
        "skipped": skipped,
        "pid": None,
    }
    write_retry_status(settings, status)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [str(python), "-m", "app.main", "retry-answered-questions", "--limit", str(limit)],
            cwd=settings.root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    status["status"] = "running"
    status["pid"] = process.pid
    write_retry_status(settings, status)
    return {
        "ok": True,
        "output": f"Started retry for {len(job_ids)} job(s). Progress appears on the Questions page.",
        "status": status,
    }


def run_answered_question_retry(settings: Settings, *, limit: int = 10) -> dict[str, Any]:
    settings.ensure_directories()
    job_ids, skipped = retryable_answered_question_jobs(settings, limit=limit)
    log_path = retry_log_path(settings)
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    status: dict[str, Any] = {
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "limit": limit,
        "job_ids": job_ids,
        "completed": [],
        "failed": [],
        "skipped": skipped,
    }
    write_retry_status(settings, status)
    python = settings.root / ".venv/bin/python"
    for job_id in job_ids:
        command = [str(python), "-m", "app.main", "apply", "--job-id", job_id, "--prepare"]
        try:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\n[{datetime.now(UTC).isoformat()}] retry prepare {job_id}\n")
                log.flush()
                result = subprocess.run(command, cwd=settings.root, text=True, stdout=log, stderr=subprocess.STDOUT, timeout=900)
            bucket = "completed" if result.returncode == 0 else "failed"
            status[bucket].append(
                {"job_id": job_id, "returncode": result.returncode, "finished_at": datetime.now(UTC).isoformat()}
            )
        except subprocess.TimeoutExpired:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"[{datetime.now(UTC).isoformat()}] retry prepare {job_id} timed out\n")
            status["failed"].append({"job_id": job_id, "returncode": "timeout", "finished_at": datetime.now(UTC).isoformat()})
        write_retry_status(settings, status)
    status["status"] = "complete"
    status["finished_at"] = datetime.now(UTC).isoformat()
    write_retry_status(settings, status)
    return status
