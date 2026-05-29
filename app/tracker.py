"""Human-readable tracking views for jobs, drafts, submissions, and worker state."""

from __future__ import annotations

import cgi
import html
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .application_agent import ApplicationRepository
from .config import LOCAL_HOSTS, ConfigurationError, Settings
from .cv_store import CVError, ingest_cv
from .preferences import DEFAULT_USER_PREFERENCES, load_preferences
from .profile_review import CONFIRM_PROFILE_PHRASE, build_profile_review, mark_profile_reviewed


DASHBOARD_NAV = (
    ("Jobs", "/"),
    ("Manual Queue", "/manual"),
    ("Onboarding", "/onboarding"),
    ("AI Providers", "/providers"),
    ("Autopilot", "/autopilot"),
    ("Worker", "/worker"),
    ("Web Search", "/search"),
)

PROVIDER_ENV_KEYS = {
    "ollama": [],
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "gemini": ["GEMINI_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
}

PROVIDER_LABELS = {
    "ollama": "Local Ollama (default)",
    "openai": "OpenAI / GPT API",
    "anthropic": "Anthropic / Claude API",
    "gemini": "Google Gemini API",
    "deepseek": "DeepSeek API",
}


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_private_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_key_value_lines(value: str) -> dict[str, str]:
    items: dict[str, str] = {}
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, answer = line.split("=", 1)
        key = key.strip()
        answer = answer.strip()
        if key and answer:
            items[key] = answer
    return items


def _format_key_value_lines(values: Any) -> str:
    if not isinstance(values, dict):
        return ""
    return "\n".join(f"{key} = {value}" for key, value in values.items())


def _gui_settings_path(settings: Settings) -> Path:
    return settings.profile_dir / "gui_settings.json"


def _provider_settings_path(settings: Settings) -> Path:
    return settings.profile_dir / "llm_providers.json"


def load_gui_settings(settings: Settings) -> dict[str, Any]:
    return _read_json(
        _gui_settings_path(settings),
        {
            "mode": "local_first",
            "autopilot_mode": "fill_only_manual_submit",
            "created_by": "dashboard",
        },
    )


def save_gui_settings(settings: Settings, payload: dict[str, Any]) -> None:
    existing = load_gui_settings(settings)
    existing.update(payload)
    existing["updated_at"] = datetime.now(UTC).isoformat()
    _write_private_json(_gui_settings_path(settings), existing)


def load_provider_settings(settings: Settings) -> dict[str, Any]:
    return _read_json(
        _provider_settings_path(settings),
        {
            "active_provider": "ollama",
            "models": {"ollama": settings.ollama_model},
            "store_api_keys": False,
            "note": "API keys are read from environment variables only, not stored in this file.",
        },
    )


def save_provider_settings(settings: Settings, payload: dict[str, Any]) -> None:
    existing = load_provider_settings(settings)
    models = existing.setdefault("models", {})
    provider = str(payload.get("active_provider") or existing.get("active_provider") or "ollama")
    existing["active_provider"] = provider
    if payload.get("model"):
        models[provider] = str(payload["model"])
    existing["store_api_keys"] = False
    existing["updated_at"] = datetime.now(UTC).isoformat()
    _write_private_json(_provider_settings_path(settings), existing)


def _read_json_dir(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    items: dict[str, Any] = {}
    for file in sorted(path.glob("*.json")):
        try:
            items[file.stem] = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            items[file.stem] = {"error": "invalid_json", "path": str(file)}
    return items


def _run_command(command: list[str], *, cwd: Path, timeout: int = 20, input_text: str | None = None) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "output": (completed.stdout + completed.stderr).strip(),
        }
    except subprocess.TimeoutExpired as exc:
        output = "".join(part for part in (exc.stdout, exc.stderr) if isinstance(part, str)).strip()
        return {"ok": False, "returncode": 124, "output": output or f"Timed out after {timeout}s."}
    except OSError as exc:
        return {"ok": False, "returncode": 127, "output": str(exc)}


def _worker_runtime(settings: Settings) -> dict[str, Any]:
    active = _run_command(["systemctl", "--user", "is-active", "job-agent-browser.service"], cwd=settings.root)
    status = _run_command(
        ["systemctl", "--user", "status", "job-agent-browser.service", "--no-pager"],
        cwd=settings.root,
        timeout=10,
    )
    return {
        "active": active["output"].splitlines()[0] if active["output"] else "unknown",
        "status": "\n".join(status["output"].splitlines()[:20]),
    }


def _local_search_runtime(settings: Settings) -> dict[str, Any]:
    url = f"{settings.searxng_base_url}/search?{urlencode({'q': 'healthcheck', 'format': 'json'})}"
    try:
        request = Request(url, headers={"User-Agent": "job-agent-browser-dashboard/0.1"})
        with urlopen(request, timeout=5) as response:  # noqa: S310 - loopback URL is validated in Settings.
            payload = response.read(120).decode("utf-8", errors="replace")
        return {"active": "active", "url": settings.searxng_base_url, "detail": payload}
    except Exception as exc:  # noqa: BLE001 - dashboard should show the exact local failure.
        return {"active": "inactive", "url": settings.searxng_base_url, "detail": str(exc)}


def _dashboard_action(settings: Settings, action: str) -> dict[str, Any]:
    actions: dict[str, list[str]] = {
        "worker-start": ["systemctl", "--user", "start", "job-agent-browser.service"],
        "worker-stop": ["systemctl", "--user", "stop", "job-agent-browser.service"],
        "worker-restart": ["systemctl", "--user", "restart", "job-agent-browser.service"],
        "worker-once": [str(settings.root / ".venv/bin/python"), "-m", "app.main", "worker-once"],
        "search-start": [str(settings.root / "scripts/start_local_search.sh")],
        "search-stop": [str(settings.root / "scripts/stop_local_search.sh")],
    }
    if action not in actions:
        return {"ok": False, "output": f"Unknown action: {action}"}
    result = _run_command(actions[action], cwd=settings.root, timeout=90, input_text="")
    if action in {"search-start", "search-stop"} and not result["ok"] and "password" in result["output"].casefold():
        result["output"] += (
            "\nDocker requires sudo for this user, so run the script from an SSH terminal: "
            f"{'scripts/start_local_search.sh' if action == 'search-start' else 'scripts/stop_local_search.sh'}"
        )
    return result


def _save_preferences_from_form(settings: Settings, form: dict[str, list[str]]) -> dict[str, Any]:
    preferences = (
        load_preferences(settings)
        if (settings.profile_dir / "job_preferences.json").exists()
        else dict(DEFAULT_USER_PREFERENCES)
    )
    preferences["target_roles"] = _split_csv((form.get("target_roles") or [""])[0])
    preferences["target_locations"] = _split_csv((form.get("target_locations") or [""])[0])
    remote_preference = (form.get("remote_preference") or [""])[0].strip()
    if remote_preference:
        preferences["remote_preference"] = remote_preference
    facts = dict(preferences.get("candidate_user_confirmed_facts") or {})
    for key in (
        "salary_target",
        "work_authorization_summary",
        "availability",
        "relocation",
        "nationality",
        "home_country",
        "cover_letter_path",
    ):
        value = (form.get(key) or [""])[0].strip()
        if value:
            facts[key] = value
        else:
            facts.pop(key, None)
    needs_sponsorship = (form.get("needs_work_sponsorship_outside_home_country") or [""])[0]
    if needs_sponsorship in {"true", "false"}:
        facts["needs_work_sponsorship_outside_home_country"] = needs_sponsorship == "true"
    language_proficiency = dict(facts.get("language_proficiency") or {})
    for key in ("english", "german"):
        value = (form.get(f"language_{key}") or [""])[0].strip()
        if value:
            language_proficiency[key] = value
        else:
            language_proficiency.pop(key, None)
    facts["language_proficiency"] = language_proficiency
    default_answers = _parse_key_value_lines((form.get("application_default_answers") or [""])[0])
    if default_answers:
        facts["application_default_answers"] = default_answers
    else:
        facts.pop("application_default_answers", None)
    preferences["candidate_user_confirmed_facts"] = facts
    _write_private_json(settings.profile_dir / "job_preferences.json", preferences)
    return {"ok": True, "output": "Saved private job preferences."}


def _onboarding_action(settings: Settings, form: dict[str, list[str]]) -> dict[str, Any]:
    action = (form.get("action") or [""])[0]
    if action == "save-preferences":
        return _save_preferences_from_form(settings, form)
    if action == "build-review":
        try:
            path = build_profile_review(settings)
            return {"ok": True, "output": f"Wrote review checklist: {path}"}
        except Exception as exc:  # noqa: BLE001 - local dashboard should surface setup errors.
            return {"ok": False, "output": str(exc)}
    if action == "confirm-profile":
        confirmation = (form.get("confirmation") or [""])[0]
        if confirmation != CONFIRM_PROFILE_PHRASE:
            return {"ok": False, "output": f"Type exactly: {CONFIRM_PROFILE_PHRASE}"}
        try:
            mark_profile_reviewed(settings)
            return {"ok": True, "output": "Profile facts marked reviewed."}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "output": str(exc)}
    return {"ok": False, "output": f"Unknown onboarding action: {action}"}


def _provider_action(settings: Settings, form: dict[str, list[str]]) -> dict[str, Any]:
    provider = (form.get("provider") or ["ollama"])[0]
    if provider not in PROVIDER_LABELS:
        return {"ok": False, "output": f"Unknown provider: {provider}"}
    model = (form.get("model") or [""])[0].strip()
    save_provider_settings(settings, {"active_provider": provider, "model": model})
    missing = [key for key in PROVIDER_ENV_KEYS[provider] if not os.environ.get(key)]
    suffix = f" Missing env vars: {', '.join(missing)}." if missing else ""
    return {"ok": True, "output": f"Saved provider preference: {PROVIDER_LABELS[provider]}.{suffix}"}


def _autopilot_action(settings: Settings, form: dict[str, list[str]]) -> dict[str, Any]:
    action = (form.get("action") or [""])[0]
    if action == "safe-fill-only":
        save_gui_settings(settings, {"autopilot_mode": "fill_only_manual_submit"})
        return {"ok": True, "output": "GUI Autopilot set to fill-only; you press final Submit."}
    if action == "manual-only":
        save_gui_settings(settings, {"autopilot_mode": "manual_review_only"})
        return {"ok": True, "output": "GUI mode set to manual review only."}
    return {"ok": False, "output": f"Unknown autopilot action: {action}"}


def _upload_cv(settings: Settings, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_type = handler.headers.get("Content-Type", "")
    if not content_type.startswith("multipart/form-data"):
        return {"ok": False, "output": "Expected multipart CV upload."}
    form = cgi.FieldStorage(
        fp=handler.rfile,
        headers=handler.headers,
        environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
    )
    field = form["cv_file"] if "cv_file" in form else None
    if field is None or not getattr(field, "filename", ""):
        return {"ok": False, "output": "No CV file was uploaded."}
    filename = Path(field.filename).name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        return {"ok": False, "output": "Only PDF and DOCX CV uploads are supported."}
    destination = settings.cv_dir / filename
    settings.cv_dir.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        shutil.copyfileobj(field.file, output)
    destination.chmod(0o600)
    try:
        result = ingest_cv(destination, settings)
    except CVError as exc:
        return {"ok": False, "output": str(exc)}
    return {"ok": True, "output": f"Uploaded and extracted CV: {result.source_path}"}


def _safe_job_id(job_id: str) -> str:
    cleaned = job_id.strip()
    if not cleaned or "/" in cleaned or "\\" in cleaned or cleaned in {".", ".."}:
        raise ValueError("Invalid job id.")
    return cleaned


def _job_action(settings: Settings, form: dict[str, list[str]]) -> dict[str, Any]:
    action = (form.get("action") or [""])[0]
    try:
        job_id = _safe_job_id((form.get("job_id") or [""])[0])
    except ValueError as exc:
        return {"ok": False, "output": str(exc)}
    note = (form.get("note") or [""])[0].strip()
    try:
        job = ApplicationRepository(settings).find_job(job_id)
    except Exception as exc:  # noqa: BLE001 - dashboard should surface missing private data clearly.
        return {"ok": False, "output": str(exc)}

    if action == "prepare-job":
        return _run_command(
            [str(settings.root / ".venv/bin/python"), "-m", "app.main", "apply", "--job-id", job_id, "--prepare"],
            cwd=settings.root,
            timeout=600,
            input_text="",
        )

    if action == "mark-submitted":
        _write_private_json(
            settings.applications_dir / "submissions" / f"{job_id}.json",
            {
                "submitted_at": datetime.now(UTC).isoformat(),
                "source": "manual_dashboard_status_edit",
                "job_id": job_id,
                "title": job.get("title"),
                "company": job.get("company"),
                "url": job.get("url"),
                "note": note,
            },
        )
        (settings.applications_dir / "status_overrides" / f"{job_id}.json").unlink(missing_ok=True)
        return {"ok": True, "output": f"Marked {job_id} as submitted."}

    if action == "clear-submitted":
        (settings.applications_dir / "submissions" / f"{job_id}.json").unlink(missing_ok=True)
        return {"ok": True, "output": f"Cleared submitted status for {job_id}."}

    if action == "skip-job":
        _write_private_json(
            settings.applications_dir / "status_overrides" / f"{job_id}.json",
            {
                "status": "skipped",
                "updated_at": datetime.now(UTC).isoformat(),
                "source": "manual_dashboard_status_edit",
                "job_id": job_id,
                "note": note,
            },
        )
        return {"ok": True, "output": f"Marked {job_id} as skipped."}

    if action == "mark-broken-link":
        _write_private_json(
            settings.applications_dir / "status_overrides" / f"{job_id}.json",
            {
                "status": "broken_link",
                "updated_at": datetime.now(UTC).isoformat(),
                "source": "manual_dashboard_status_edit",
                "job_id": job_id,
                "note": note,
            },
        )
        return {"ok": True, "output": f"Marked {job_id} as broken link."}

    if action == "reopen-job":
        (settings.applications_dir / "status_overrides" / f"{job_id}.json").unlink(missing_ok=True)
        return {"ok": True, "output": f"Reopened {job_id}."}

    return {"ok": False, "output": f"Unknown job action: {action}"}


def tracker_status(settings: Settings) -> dict[str, Any]:
    settings.ensure_directories()
    jobs = ApplicationRepository(settings).load_jobs()
    drafts = _read_json_dir(settings.applications_dir / "drafts")
    prepared = _read_json_dir(settings.applications_dir / "prepared")
    submissions = _read_json_dir(settings.applications_dir / "submissions")
    attempts = _read_json_dir(settings.applications_dir / "submission_attempts")
    approvals = _read_json_dir(settings.applications_dir / "approvals")
    status_overrides = _read_json_dir(settings.applications_dir / "status_overrides")
    worker_status = _read_json(settings.applications_dir / "worker_status.json", {})

    rows: list[dict[str, Any]] = []
    for job in jobs:
        job_id = str(job.get("id", ""))
        draft = drafts.get(job_id)
        if job_id in submissions:
            state = "submitted"
        elif job_id in prepared:
            state = "prepared_manual_submit"
        elif job_id in attempts:
            state = "unverified_submit_click"
        elif (status_overrides.get(job_id) or {}).get("status") in {"skipped", "broken_link"}:
            state = str((status_overrides.get(job_id) or {}).get("status"))
        elif job_id in drafts:
            state = "drafted"
        else:
            state = "found"
        manual_submit_ready = state in {"drafted", "prepared_manual_submit"}
        rows.append(
            {
                "id": job_id,
                "state": state,
                "manual_submit_ready": manual_submit_ready,
                "score": int(job.get("match_score", 0)),
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "location": job.get("location", ""),
                "url": job.get("url", ""),
                "risks_uncertainties": (
                    draft.get("required_user_answers")
                    if isinstance(draft, dict) and draft.get("required_user_answers") is not None
                    else job.get("risks_uncertainties") or []
                ),
                "draft": draft,
                "prepared": prepared.get(job_id),
                "submission": submissions.get(job_id),
                "submission_attempt": attempts.get(job_id),
                "approval": approvals.get(job_id),
                "status_override": status_overrides.get(job_id),
            }
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "counts": {
            "jobs": len(jobs),
            "drafts": len(drafts),
            "prepared": len(prepared),
            "manual_submit_queue": sum(1 for row in rows if row["manual_submit_ready"]),
            "submitted": len(submissions),
            "unverified_submit_clicks": len(attempts),
            "approvals": len(approvals),
            "skipped": sum(1 for row in rows if row["state"] == "skipped"),
            "broken_links": sum(1 for row in rows if row["state"] == "broken_link"),
        },
        "worker_status": worker_status,
        "jobs": rows,
    }


def format_tracker_text(status: dict[str, Any]) -> str:
    counts = status["counts"]
    lines = [
        "Job Agent Status",
        f"Generated: {status['generated_at']}",
        "",
        f"Jobs known: {counts['jobs']}",
        f"Drafts: {counts['drafts']}",
        f"Prepared for manual submit: {counts['prepared']}",
        f"Manual submit queue: {counts['manual_submit_queue']}",
        f"Submitted: {counts['submitted']}",
        f"Unverified submit-clicks: {counts['unverified_submit_clicks']}",
        f"Approvals: {counts['approvals']}",
        "",
        "Jobs:",
    ]
    for job in status["jobs"]:
        lines.append(
            f"- {job['state'].upper()} | score {job['score']} | {job['id']} | {job['title']} | {job['company']}"
        )
        if job.get("url"):
            lines.append(f"  {job['url']}")
        for risk in (job.get("risks_uncertainties") or [])[:3]:
            lines.append(f"  ! {risk}")
    if not status["jobs"]:
        lines.append("- No jobs found yet.")
    return "\n".join(lines)


def format_tracker_chat(status: dict[str, Any]) -> str:
    counts = status["counts"]
    lines = [
        "Job Agent update",
        (
            f"Jobs: {counts['jobs']} | Drafts: {counts['drafts']} | "
            f"Prepared: {counts['prepared']} | Submitted: {counts['submitted']} | "
            f"Manual queue: {counts['manual_submit_queue']} | "
            f"Unverified clicks: {counts['unverified_submit_clicks']}"
        ),
    ]
    prepared = [job for job in status["jobs"] if job["state"] == "prepared_manual_submit"]
    submitted = [job for job in status["jobs"] if job["state"] == "submitted"]
    attempts = [job for job in status["jobs"] if job["state"] == "unverified_submit_click"]
    drafted = [job for job in status["jobs"] if job["state"] == "drafted"]
    if submitted:
        lines.append("")
        lines.append("Submitted:")
        for job in submitted[:5]:
            lines.append(f"- {job['title']} at {job['company']}")
    if drafted:
        lines.append("")
        lines.append("Manual submit queue:")
        for job in drafted[:5]:
            lines.append(f"- {job['title']} at {job['company']} (score {job['score']})")
    if prepared:
        lines.append("")
        lines.append("Prepared for your final submit:")
        for job in prepared[:5]:
            lines.append(f"- {job['title']} at {job['company']}")
    if attempts:
        lines.append("")
        lines.append("Unverified submit-clicks:")
        for job in attempts[:5]:
            lines.append(f"- {job['title']} at {job['company']}")
    worker_errors = status.get("worker_status", {}).get("errors") or []
    if worker_errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in worker_errors[:5])
    return "\n".join(lines)


def _dashboard_shell(
    *,
    title: str,
    generated_at: str,
    body: str,
    active_path: str = "/",
    message: str = "",
) -> str:
    nav = "".join(
        f'<a class="nav-link {"active" if path == active_path else ""}" href="{path}">{html.escape(label)}</a>'
        for label, path in DASHBOARD_NAV
    )
    message_html = f'<section class="notice"><pre>{html.escape(message)}</pre></section>' if message else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; background: #f7f4ee; color: #1e1b18; }}
    main {{ max-width: 1120px; margin: 0 auto; }}
    nav {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 18px 0 24px; }}
    .nav-link {{ border-radius: 999px; padding: 9px 13px; background: #fffaf1; border: 1px solid #e2d7c4; color: #5b3fd6; text-decoration: none; }}
    .nav-link.active {{ background: #1d4ed8; color: white; border-color: #1d4ed8; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 12px; margin: 24px 0; }}
    .card, .job, .panel, .notice {{ background: #fffaf1; border: 1px solid #e2d7c4; border-radius: 16px; padding: 16px; box-shadow: 0 8px 28px rgba(48, 36, 20, 0.08); }}
    .notice {{ margin-bottom: 18px; background: #eef7ff; }}
    .notice pre, .panel pre {{ white-space: pre-wrap; margin: 0; }}
    .job {{ margin-bottom: 14px; }}
    .job header {{ display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }}
    .pill {{ border-radius: 999px; padding: 3px 9px; background: #ede2d0; text-transform: uppercase; font-size: 12px; letter-spacing: 0.04em; }}
    .submitted .pill {{ background: #d8f3dc; }}
    .prepared_manual_submit .pill {{ background: #dbeafe; }}
    .drafted .pill {{ background: #fff3bf; }}
    .manual-actions {{ margin-top: 12px; padding: 12px; border-radius: 12px; background: #f1ecff; }}
    .review, button {{ display: inline-block; margin: 6px 8px 6px 0; border-radius: 999px; padding: 8px 12px; background: #1d4ed8; color: white; border: 0; text-decoration: none; cursor: pointer; }}
    .review.secondary, button.secondary {{ background: #5b3fd6; }}
    button.danger {{ background: #b91c1c; }}
    input[type="search"], input[type="text"], input:not([type]), select, textarea {{ width: min(680px, 100%); padding: 10px 12px; border-radius: 12px; border: 1px solid #d5c8b5; }}
    a {{ color: #5b3fd6; word-break: break-word; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
    th, td {{ border-top: 1px solid #eadfce; text-align: left; padding: 8px; vertical-align: top; }}
    th {{ width: 220px; }}
    code {{ background: #eee5d7; padding: 2px 5px; border-radius: 6px; }}
  </style>
</head>
<body>
<main>
  <h1>{html.escape(title)}</h1>
  <p>Generated: <code>{html.escape(generated_at)}</code></p>
  <nav>{nav}</nav>
  {message_html}
  {body}
</main>
</body>
</html>
"""


def _summary_cards(counts: dict[str, Any]) -> str:
    return f"""
  <section class="cards">
    <div class="card"><strong>{counts['jobs']}</strong><br>jobs</div>
    <div class="card"><strong>{counts['drafts']}</strong><br>drafts</div>
    <div class="card"><strong>{counts['prepared']}</strong><br>ready for you</div>
    <div class="card"><strong>{counts['manual_submit_queue']}</strong><br>manual queue</div>
    <div class="card"><strong>{counts['submitted']}</strong><br>submitted</div>
    <div class="card"><strong>{counts['unverified_submit_clicks']}</strong><br>unverified clicks</div>
    <div class="card"><strong>{counts['approvals']}</strong><br>approvals</div>
    <div class="card"><strong>{counts.get('skipped', 0)}</strong><br>skipped</div>
    <div class="card"><strong>{counts.get('broken_links', 0)}</strong><br>broken links</div>
  </section>
"""


def _job_article(job: dict[str, Any]) -> str:
    risks = "".join(f"<li>{html.escape(str(risk))}</li>" for risk in job.get("risks_uncertainties") or [])
    draft = job.get("draft") or {}
    answers = draft.get("answers") or {}
    answer_rows = "".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
        for key, value in answers.items()
    )
    submission = job.get("submission") or {}
    prepared = job.get("prepared") or {}
    attempt = job.get("submission_attempt") or {}
    prepared_at = html.escape(str(prepared.get("prepared_at", "")))
    review_url = html.escape(str((prepared.get("result") or {}).get("manual_review_url") or ""))
    screenshot_path = html.escape(str((prepared.get("result") or {}).get("screenshot_path") or ""))
    submitted_at = html.escape(str(submission.get("submitted_at", "")))
    attempted_at = html.escape(str(attempt.get("attempted_at", "")))
    job_id = html.escape(str(job["id"]))
    job_url = html.escape(str(job.get("url") or ""))
    prepare_button = ""
    if job.get("state") == "drafted":
        prepare_button = f"""
            <form method="post" action="/job-action">
              <input type="hidden" name="job_id" value="{job_id}">
              <button name="action" value="prepare-job">Prepare/fill this application</button>
            </form>
        """
    review_button = (
        f'<p><a class="review" href="{review_url}">Review prepared application and press Submit</a></p>'
        if review_url
        else ""
    )
    manual_actions = ""
    if job.get("manual_submit_ready"):
        manual_actions = f"""
          <section class="manual-actions">
            <strong>Manual submit options</strong>
            <p><a class="review secondary" href="{job_url}">Open original application page</a></p>
            {review_button}
            {prepare_button}
            <p>CLI fallback for pre-filling in the remote challenge browser:</p>
            <code>.venv/bin/python -m app.main apply --job-id {job_id} --prepare</code>
            <p>To open a final manual review session from the CLI:</p>
            <code>.venv/bin/python -m app.main apply --job-id {job_id} --confirm</code>
          </section>
        """
    status_controls = f"""
          <section class="manual-actions">
            <strong>Edit local status</strong>
            <form method="post" action="/job-action">
              <input type="hidden" name="job_id" value="{job_id}">
              <input name="note" placeholder="Optional note, e.g. submitted on company site">
              <button name="action" value="mark-submitted">Mark submitted</button>
              <button class="secondary" name="action" value="clear-submitted">Clear submitted</button>
              <button class="secondary" name="action" value="reopen-job">Reopen</button>
              <button class="secondary" name="action" value="mark-broken-link">Broken link</button>
              <button class="danger" name="action" value="skip-job">Skip</button>
            </form>
          </section>
    """
    return f"""
        <article class="job {html.escape(job['state'])}">
          <header>
            <span class="pill">{html.escape(job['state'])}</span>
            <strong>{html.escape(str(job['title']))}</strong>
            <span>{html.escape(str(job['company'] or ''))}</span>
            <span>score {job['score']}</span>
          </header>
          <a href="{html.escape(str(job['url']))}">{html.escape(str(job['url']))}</a>
          <p>{html.escape(str(job.get('location') or ''))}</p>
          <p>{'Submitted at: ' + submitted_at if submitted_at else ''}</p>
          <p>{'Prepared at: ' + prepared_at if prepared_at else ''}</p>
          {manual_actions}
          {status_controls}
          <p>{'Prepared screenshot: ' + screenshot_path if screenshot_path else ''}</p>
          <p>{'Unverified click at: ' + attempted_at if attempted_at else ''}</p>
          <details>
            <summary>Risks / questions</summary>
            <ul>{risks or '<li>None recorded.</li>'}</ul>
          </details>
          <details>
            <summary>Draft answers</summary>
            <table>{answer_rows or '<tr><td>No draft answers yet.</td></tr>'}</table>
          </details>
        </article>
        """


def render_tracker_html(status: dict[str, Any]) -> str:
    counts = status["counts"]
    rows = "\n".join(_job_article(job) for job in status["jobs"]) or "<p>No jobs found yet.</p>"
    body = _summary_cards(counts) + rows
    return _dashboard_shell(title="Job Agent Tracker", generated_at=status["generated_at"], body=body, active_path="/")


def write_tracker_html(settings: Settings) -> Path:
    status = tracker_status(settings)
    path = settings.applications_dir / "tracker.html"
    path.write_text(render_tracker_html(status), encoding="utf-8")
    path.chmod(0o600)
    return path


def render_manual_queue_html(status: dict[str, Any], *, message: str = "") -> str:
    jobs = [job for job in status["jobs"] if job.get("manual_submit_ready")]
    rows = "\n".join(_job_article(job) for job in jobs) or "<p>No drafted/prepared jobs are waiting for manual submission.</p>"
    body = _summary_cards(status["counts"]) + f"<h2>Manual Submit Queue</h2><p>{len(jobs)} jobs ready for you to review manually.</p>{rows}"
    return _dashboard_shell(
        title="Manual Submit Queue",
        generated_at=status["generated_at"],
        body=body,
        active_path="/manual",
        message=message,
    )


def render_worker_html(settings: Settings, status: dict[str, Any], *, message: str = "") -> str:
    runtime = _worker_runtime(settings)
    worker_status = status.get("worker_status") or {}
    worker_status_text = html.escape(json.dumps(worker_status, indent=2, ensure_ascii=True)[:12000])
    body = _summary_cards(status["counts"]) + f"""
    <section class="panel">
      <h2>Worker Control</h2>
      <p>Status: <strong>{html.escape(str(runtime['active']))}</strong></p>
      <form method="post" action="/action">
        <button name="action" value="worker-start">Start worker</button>
        <button class="danger" name="action" value="worker-stop">Stop worker</button>
        <button class="secondary" name="action" value="worker-restart">Restart worker</button>
        <button class="secondary" name="action" value="worker-once">Run one cycle now</button>
      </form>
      <h3>Systemd status</h3>
      <pre>{html.escape(str(runtime['status']))}</pre>
      <h3>Last worker summary</h3>
      <pre>{worker_status_text}</pre>
    </section>
    """
    return _dashboard_shell(title="Worker", generated_at=status["generated_at"], body=body, active_path="/worker", message=message)


def render_search_html(settings: Settings, status: dict[str, Any], *, query: str = "", message: str = "") -> str:
    runtime = _local_search_runtime(settings)
    results_html = ""
    if query:
        search_url = f"{settings.searxng_base_url}/search?{urlencode({'q': query, 'format': 'json'})}"
        try:
            request = Request(search_url, headers={"User-Agent": "job-agent-browser-dashboard/0.1"})
            with urlopen(request, timeout=20) as response:  # noqa: S310 - loopback URL is validated in Settings.
                payload = json.loads(response.read().decode("utf-8"))
            items = payload.get("results", [])[:20]
            results_html = "<h3>Results</h3>" + "".join(
                f"<article class=\"job\"><strong>{html.escape(str(item.get('title') or 'Untitled'))}</strong>"
                f"<p><a href=\"{html.escape(str(item.get('url') or ''))}\">{html.escape(str(item.get('url') or ''))}</a></p>"
                f"<p>{html.escape(str(item.get('content') or ''))}</p></article>"
                for item in items
            )
            if not items:
                results_html = "<p>No results returned.</p>"
        except Exception as exc:  # noqa: BLE001 - dashboard should show local search errors.
            results_html = f"<section class=\"notice\"><pre>{html.escape(str(exc))}</pre></section>"
    body = f"""
    <section class="panel">
      <h2>Local Web Search</h2>
      <p>Status: <strong>{html.escape(str(runtime['active']))}</strong> at <code>{html.escape(str(runtime['url']))}</code></p>
      <p>Detail: <code>{html.escape(str(runtime['detail']))}</code></p>
      <form method="get" action="/search">
        <input type="search" name="q" value="{html.escape(query)}" placeholder="AI product engineer Switzerland sponsorship">
        <button type="submit">Search</button>
      </form>
      <form method="post" action="/action">
        <button name="action" value="search-start">Start local search</button>
        <button class="danger" name="action" value="search-stop">Stop local search</button>
      </form>
      <p>If Docker requires sudo, use SSH and run <code>scripts/start_local_search.sh</code> or <code>scripts/stop_local_search.sh</code>.</p>
    </section>
    {results_html}
    """
    return _dashboard_shell(title="Web Search", generated_at=status["generated_at"], body=body, active_path="/search", message=message)


def _file_status(path: Path) -> str:
    return "present" if path.exists() else "missing"


def render_onboarding_html(settings: Settings, status: dict[str, Any], *, message: str = "") -> str:
    preferences = load_preferences(settings)
    profile_path = settings.profile_dir / "candidate_profile.json"
    extracted_path = settings.profile_dir / "cv_extracted.md"
    review_path = settings.profile_dir / "profile_review.md"
    confirmed = bool((preferences.get("candidate_user_confirmed_facts") or {}).get("profile_reviewed"))
    roles = ", ".join(preferences.get("target_roles") or [])
    locations = ", ".join(preferences.get("target_locations") or [])
    facts = preferences.get("candidate_user_confirmed_facts") or {}
    language_proficiency = facts.get("language_proficiency") or {}
    default_answers_text = html.escape(_format_key_value_lines(facts.get("application_default_answers") or {}))
    sponsorship_value = facts.get("needs_work_sponsorship_outside_home_country")
    sponsorship_options = "".join(
        f'<option value="{value}" {"selected" if selected else ""}>{label}</option>'
        for value, label, selected in (
            ("", "needs_user_answer", sponsorship_value not in {True, False}),
            ("true", "Yes", sponsorship_value is True),
            ("false", "No", sponsorship_value is False),
        )
    )
    profile_preview = html.escape(json.dumps(_read_json(profile_path, {}), indent=2, ensure_ascii=True)[:9000])
    review_preview = html.escape(review_path.read_text(encoding="utf-8")[:9000]) if review_path.exists() else ""
    body = _summary_cards(status["counts"]) + f"""
    <section class="panel">
      <h2>Guided Setup</h2>
      <p>This is the less-technical path: upload a CV, save preferences, review extracted facts, log in manually, then run safe fill-only application prep from the dashboard.</p>
      <table>
        <tr><th>CV extracted text</th><td>{html.escape(_file_status(extracted_path))}</td></tr>
        <tr><th>Candidate profile</th><td>{html.escape(_file_status(profile_path))}</td></tr>
        <tr><th>Profile reviewed</th><td>{'yes' if confirmed else 'no'}</td></tr>
        <tr><th>Gmail/browser login</th><td>Use the persistent browser login command below; passwords are never stored by the app.</td></tr>
      </table>
    </section>

    <section class="panel">
      <h2>1. Upload CV</h2>
      <p>PDF and DOCX are supported. The file is copied into private ignored storage under <code>data/cv/</code>.</p>
      <form method="post" action="/upload-cv" enctype="multipart/form-data">
        <input type="file" name="cv_file" accept=".pdf,.docx" required>
        <button type="submit">Upload and extract CV</button>
      </form>
    </section>

    <section class="panel">
      <h2>2. Preferences</h2>
      <form method="post" action="/onboarding-action">
        <input type="hidden" name="action" value="save-preferences">
        <label>Target roles<br><input name="target_roles" value="{html.escape(roles)}" placeholder="AI Product Manager, Product Engineer"></label><br><br>
        <label>Target locations<br><input name="target_locations" value="{html.escape(locations)}" placeholder="Switzerland, Europe, Remote"></label><br><br>
        <label>Remote preference<br><input name="remote_preference" value="{html.escape(str(preferences.get('remote_preference') or ''))}" placeholder="remote, hybrid, onsite"></label><br><br>
        <label>Target salary / compensation notes<br><input name="salary_target" value="{html.escape(str(facts.get('salary_target') or ''))}" placeholder="User-confirmed only"></label><br><br>
        <label>Work authorization / sponsorship notes<br><input name="work_authorization_summary" value="{html.escape(str(facts.get('work_authorization_summary') or ''))}" placeholder="User-confirmed only"></label><br><br>
        <label>Nationality / citizenships<br><input name="nationality" value="{html.escape(str(facts.get('nationality') or ''))}" placeholder="e.g. Egyptian"></label><br><br>
        <label>Home country<br><input name="home_country" value="{html.escape(str(facts.get('home_country') or ''))}" placeholder="e.g. Egypt"></label><br><br>
        <label>Need sponsorship outside home country?<br><select name="needs_work_sponsorship_outside_home_country">{sponsorship_options}</select></label><br><br>
        <label>Availability<br><input name="availability" value="{html.escape(str(facts.get('availability') or ''))}" placeholder="immediately, after graduation, etc."></label><br><br>
        <label>Relocation preference<br><input name="relocation" value="{html.escape(str(facts.get('relocation') or ''))}" placeholder="open to relocate, remote only, etc."></label><br><br>
        <label>English level<br><input name="language_english" value="{html.escape(str(language_proficiency.get('english') or ''))}" placeholder="e.g. Fluent / C1"></label><br><br>
        <label>German level<br><input name="language_german" value="{html.escape(str(language_proficiency.get('german') or ''))}" placeholder="e.g. A1 / beginner / not fluent"></label><br><br>
        <label>Cover letter file path<br><input name="cover_letter_path" value="{html.escape(str(facts.get('cover_letter_path') or ''))}" placeholder="/absolute/path/to/cover-letter.pdf"></label><br><br>
        <label>Extra default answers<br>
          <textarea name="application_default_answers" rows="7" placeholder="question keyword = answer">{default_answers_text}</textarea>
        </label>
        <p>Use extra defaults only for facts or preferences you personally confirm, for example <code>desired annual salary = 90000 EUR</code>. The agent matches the left side against form labels.</p>
        <button type="submit">Save private preferences</button>
      </form>
    </section>

    <section class="panel">
      <h2>3. Review Extracted Profile</h2>
      <p>Anything not in your CV or private preferences remains unknown; the agent must mark those as questions instead of inventing answers.</p>
      <form method="post" action="/onboarding-action">
        <button name="action" value="build-review">Build review checklist</button>
      </form>
      <form method="post" action="/onboarding-action">
        <input type="hidden" name="action" value="confirm-profile">
        <label>Type <code>{CONFIRM_PROFILE_PHRASE}</code><br><input name="confirmation" placeholder="{CONFIRM_PROFILE_PHRASE}"></label>
        <button type="submit">Confirm reviewed facts</button>
      </form>
      <details open><summary>Candidate profile JSON</summary><pre>{profile_preview or 'No profile extracted yet.'}</pre></details>
      <details><summary>Review checklist</summary><pre>{review_preview or 'No review checklist yet.'}</pre></details>
    </section>

    <section class="panel">
      <h2>4. Manual Gmail / Site Login</h2>
      <p>Run this once from the machine that hosts the browser session, then log into Gmail and job sites manually:</p>
      <code>.venv/bin/python -m app.main login-session</code>
      <p>The saved browser profile stays local in <code>data/sessions/browser-profile</code>. The app never asks for raw Gmail passwords.</p>
    </section>
    """
    return _dashboard_shell(title="Onboarding", generated_at=status["generated_at"], body=body, active_path="/onboarding", message=message)


def render_provider_html(settings: Settings, status: dict[str, Any], *, message: str = "") -> str:
    provider_settings = load_provider_settings(settings)
    active_provider = str(provider_settings.get("active_provider") or "ollama")
    models = provider_settings.get("models") or {}
    rows = []
    for provider, label in PROVIDER_LABELS.items():
        env_keys = PROVIDER_ENV_KEYS[provider]
        env_state = "local/no key needed" if not env_keys else ", ".join(
            f"{key}={'set' if os.environ.get(key) else 'missing'}" for key in env_keys
        )
        rows.append(
            f"<tr><th>{html.escape(label)}</th><td>{'active' if provider == active_provider else 'available'}</td>"
            f"<td>{html.escape(str(models.get(provider) or 'user configured'))}</td><td>{html.escape(env_state)}</td></tr>"
        )
    options = "".join(
        f'<option value="{provider}" {"selected" if provider == active_provider else ""}>{html.escape(label)}</option>'
        for provider, label in PROVIDER_LABELS.items()
    )
    body = _summary_cards(status["counts"]) + f"""
    <section class="panel">
      <h2>AI Provider Mode</h2>
      <p>Local Ollama remains the default. External APIs are optional and read keys from environment variables only; this dashboard never stores API keys.</p>
      <form method="post" action="/provider-action">
        <label>Provider<br><select name="provider">{options}</select></label><br><br>
        <label>Model name<br><input name="model" value="{html.escape(str(models.get(active_provider) or ''))}" placeholder="Leave blank to keep current/default"></label><br><br>
        <button type="submit">Save provider preference</button>
      </form>
      <h3>Status</h3>
      <table><tr><th>Provider</th><th>State</th><th>Model</th><th>Environment</th></tr>{''.join(rows)}</table>
      <p>Set API keys in your private shell or <code>.env</code> only. Do not paste keys into application forms or commit them.</p>
    </section>
    """
    return _dashboard_shell(title="AI Providers", generated_at=status["generated_at"], body=body, active_path="/providers", message=message)


def render_autopilot_html(settings: Settings, status: dict[str, Any], *, message: str = "") -> str:
    gui_settings = load_gui_settings(settings)
    mode = str(gui_settings.get("autopilot_mode") or "fill_only_manual_submit")
    body = _summary_cards(status["counts"]) + f"""
    <section class="panel">
      <h2>Autopilot</h2>
      <p><strong>GUI Autopilot means fill-only:</strong> the agent may prepare safe drafts and filled pages, but you press the final Submit button yourself.</p>
      <p>Current GUI mode: <code>{html.escape(mode)}</code></p>
      <form method="post" action="/autopilot-action">
        <button name="action" value="safe-fill-only">Use Autopilot: AI fills, I submit</button>
        <button class="secondary" name="action" value="manual-only">Use manual review only</button>
      </form>
      <p>For advanced users, the old CLI autopilot config still exists at <code>data/profiles/autopilot.json</code>. It is private and ignored by git.</p>
    </section>
    <section class="panel">
      <h2>One-Run GUI Command</h2>
      <p>Start the local dashboard and helper services with:</p>
      <code>scripts/start_gui.sh</code>
      <p>Then open <code>http://127.0.0.1:{settings.dashboard_port}</code> on the same device or through your private tunnel.</p>
    </section>
    """
    return _dashboard_shell(title="Autopilot", generated_at=status["generated_at"], body=body, active_path="/autopilot", message=message)


def serve_tracker(settings: Settings, host: str | None = None, port: int | None = None) -> None:
    bind_host = host or settings.dashboard_host
    bind_port = port or settings.dashboard_port
    if bind_host.lower() not in LOCAL_HOSTS:
        raise ConfigurationError("Tracker dashboard must bind to loopback only.")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            parsed = urlparse(self.path)
            status = tracker_status(settings)
            if parsed.path in {"/", "/index.html"}:
                payload = render_tracker_html(status).encode("utf-8")
            elif parsed.path == "/manual":
                payload = render_manual_queue_html(status).encode("utf-8")
            elif parsed.path == "/onboarding":
                payload = render_onboarding_html(settings, status).encode("utf-8")
            elif parsed.path == "/providers":
                payload = render_provider_html(settings, status).encode("utf-8")
            elif parsed.path == "/autopilot":
                payload = render_autopilot_html(settings, status).encode("utf-8")
            elif parsed.path == "/worker":
                payload = render_worker_html(settings, status).encode("utf-8")
            elif parsed.path == "/search":
                query = (parse_qs(parsed.query).get("q") or [""])[0]
                payload = render_search_html(settings, status, query=query).encode("utf-8")
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            parsed = urlparse(self.path)
            if parsed.path == "/upload-cv":
                result = _upload_cv(settings, self)
                status = tracker_status(settings)
                message = f"upload-cv: {'ok' if result.get('ok') else 'failed'}\n{result.get('output', '')}".strip()
                payload = render_onboarding_html(settings, status, message=message).encode("utf-8")
            elif parsed.path in {"/action", "/job-action", "/onboarding-action", "/provider-action", "/autopilot-action"}:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8", errors="replace")
                form = parse_qs(body)
                action = (form.get("action") or [""])[0]
                if parsed.path == "/action":
                    result = _dashboard_action(settings, action)
                    active = "/search" if action.startswith("search-") else "/worker"
                elif parsed.path == "/job-action":
                    result = _job_action(settings, form)
                    active = "/manual"
                elif parsed.path == "/onboarding-action":
                    result = _onboarding_action(settings, form)
                    active = "/onboarding"
                elif parsed.path == "/provider-action":
                    result = _provider_action(settings, form)
                    active = "/providers"
                else:
                    result = _autopilot_action(settings, form)
                    active = "/autopilot"
                status = tracker_status(settings)
                message = f"{action}: {'ok' if result.get('ok') else 'failed'}\n{result.get('output', '')}".strip()
                if active == "/search":
                    payload = render_search_html(settings, status, message=message).encode("utf-8")
                elif active == "/worker":
                    payload = render_worker_html(settings, status, message=message).encode("utf-8")
                elif active == "/manual":
                    payload = render_manual_queue_html(status, message=message).encode("utf-8")
                elif active == "/onboarding":
                    payload = render_onboarding_html(settings, status, message=message).encode("utf-8")
                elif active == "/providers":
                    payload = render_provider_html(settings, status, message=message).encode("utf-8")
                else:
                    payload = render_autopilot_html(settings, status, message=message).encode("utf-8")
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            return

    server = ThreadingHTTPServer((bind_host, bind_port), Handler)
    print(f"Tracker dashboard: http://{bind_host}:{bind_port}")
    server.serve_forever()


def format_manual_queue(status: dict[str, Any], *, limit: int = 100) -> str:
    jobs = [job for job in status["jobs"] if job.get("manual_submit_ready")]
    lines = [
        "Manual Submit Queue",
        f"Generated: {status['generated_at']}",
        f"Jobs ready for manual review/submission: {len(jobs)}",
        "",
    ]
    for job in jobs[:limit]:
        lines.extend(
            [
                f"{job['id']} | {job['state']} | score {job['score']} | {job['title']} | {job['company']}",
                f"  URL: {job.get('url', '')}",
                f"  Prepare: .venv/bin/python -m app.main apply --job-id {job['id']} --prepare",
                f"  Manual review: .venv/bin/python -m app.main apply --job-id {job['id']} --confirm",
            ]
        )
        questions = job.get("risks_uncertainties") or []
        if questions:
            lines.append(f"  Needs: {questions[0]}")
        lines.append("")
    if len(jobs) > limit:
        lines.append(f"... {len(jobs) - limit} more omitted; rerun with a higher --limit.")
    if not jobs:
        lines.append("No drafted/prepared jobs are waiting for manual submission.")
    return "\n".join(lines)
