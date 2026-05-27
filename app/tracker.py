"""Human-readable tracking views for jobs, drafts, submissions, and worker state."""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .application_agent import ApplicationRepository
from .config import LOCAL_HOSTS, ConfigurationError, Settings


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


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


def tracker_status(settings: Settings) -> dict[str, Any]:
    settings.ensure_directories()
    jobs = ApplicationRepository(settings).load_jobs()
    drafts = _read_json_dir(settings.applications_dir / "drafts")
    submissions = _read_json_dir(settings.applications_dir / "submissions")
    attempts = _read_json_dir(settings.applications_dir / "submission_attempts")
    approvals = _read_json_dir(settings.applications_dir / "approvals")
    worker_status = _read_json(settings.applications_dir / "worker_status.json", {})

    rows: list[dict[str, Any]] = []
    for job in jobs:
        job_id = str(job.get("id", ""))
        if job_id in submissions:
            state = "submitted"
        elif job_id in attempts:
            state = "unverified_submit_click"
        elif job_id in drafts:
            state = "drafted"
        else:
            state = "found"
        rows.append(
            {
                "id": job_id,
                "state": state,
                "score": int(job.get("match_score", 0)),
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "location": job.get("location", ""),
                "url": job.get("url", ""),
                "risks_uncertainties": job.get("risks_uncertainties") or [],
                "draft": drafts.get(job_id),
                "submission": submissions.get(job_id),
                "submission_attempt": attempts.get(job_id),
                "approval": approvals.get(job_id),
            }
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "counts": {
            "jobs": len(jobs),
            "drafts": len(drafts),
            "submitted": len(submissions),
            "unverified_submit_clicks": len(attempts),
            "approvals": len(approvals),
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
            f"Submitted: {counts['submitted']} | Unverified clicks: {counts['unverified_submit_clicks']}"
        ),
    ]
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
        lines.append("Drafted / pending:")
        for job in drafted[:5]:
            lines.append(f"- {job['title']} at {job['company']} (score {job['score']})")
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


def render_tracker_html(status: dict[str, Any]) -> str:
    counts = status["counts"]
    rows = []
    for job in status["jobs"]:
        risks = "".join(f"<li>{html.escape(str(risk))}</li>" for risk in job.get("risks_uncertainties") or [])
        draft = job.get("draft") or {}
        answers = draft.get("answers") or {}
        answer_rows = "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in answers.items()
        )
        submission = job.get("submission") or {}
        attempt = job.get("submission_attempt") or {}
        submitted_at = html.escape(str(submission.get("submitted_at", "")))
        attempted_at = html.escape(str(attempt.get("attempted_at", "")))
        rows.append(
            f"""
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
        )
    body = "\n".join(rows) or "<p>No jobs found yet.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Job Agent Tracker</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; background: #f7f4ee; color: #1e1b18; }}
    main {{ max-width: 1120px; margin: 0 auto; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 12px; margin: 24px 0; }}
    .card, .job {{ background: #fffaf1; border: 1px solid #e2d7c4; border-radius: 16px; padding: 16px; box-shadow: 0 8px 28px rgba(48, 36, 20, 0.08); }}
    .job {{ margin-bottom: 14px; }}
    .job header {{ display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }}
    .pill {{ border-radius: 999px; padding: 3px 9px; background: #ede2d0; text-transform: uppercase; font-size: 12px; letter-spacing: 0.04em; }}
    .submitted .pill {{ background: #d8f3dc; }}
    .drafted .pill {{ background: #fff3bf; }}
    a {{ color: #5b3fd6; word-break: break-word; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
    th, td {{ border-top: 1px solid #eadfce; text-align: left; padding: 8px; vertical-align: top; }}
    th {{ width: 220px; }}
    code {{ background: #eee5d7; padding: 2px 5px; border-radius: 6px; }}
  </style>
</head>
<body>
<main>
  <h1>Job Agent Tracker</h1>
  <p>Generated: <code>{html.escape(status['generated_at'])}</code></p>
  <section class="cards">
    <div class="card"><strong>{counts['jobs']}</strong><br>jobs</div>
    <div class="card"><strong>{counts['drafts']}</strong><br>drafts</div>
    <div class="card"><strong>{counts['submitted']}</strong><br>submitted</div>
    <div class="card"><strong>{counts['unverified_submit_clicks']}</strong><br>unverified clicks</div>
    <div class="card"><strong>{counts['approvals']}</strong><br>approvals</div>
  </section>
  {body}
</main>
</body>
</html>
"""


def write_tracker_html(settings: Settings) -> Path:
    status = tracker_status(settings)
    path = settings.applications_dir / "tracker.html"
    path.write_text(render_tracker_html(status), encoding="utf-8")
    path.chmod(0o600)
    return path


def serve_tracker(settings: Settings, host: str | None = None, port: int | None = None) -> None:
    bind_host = host or settings.dashboard_host
    bind_port = port or settings.dashboard_port
    if bind_host.lower() not in LOCAL_HOSTS:
        raise ConfigurationError("Tracker dashboard must bind to loopback only.")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            if self.path not in {"/", "/index.html"}:
                self.send_response(404)
                self.end_headers()
                return
            payload = render_tracker_html(tracker_status(settings)).encode("utf-8")
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
