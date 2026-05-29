"""Human-readable tracking views for jobs, drafts, submissions, and worker state."""

from __future__ import annotations

import html
import json
import subprocess
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .application_agent import ApplicationRepository
from .config import LOCAL_HOSTS, ConfigurationError, Settings


DASHBOARD_NAV = (
    ("Jobs", "/"),
    ("Manual Queue", "/manual"),
    ("Worker", "/worker"),
    ("Web Search", "/search"),
)


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


def tracker_status(settings: Settings) -> dict[str, Any]:
    settings.ensure_directories()
    jobs = ApplicationRepository(settings).load_jobs()
    drafts = _read_json_dir(settings.applications_dir / "drafts")
    prepared = _read_json_dir(settings.applications_dir / "prepared")
    submissions = _read_json_dir(settings.applications_dir / "submissions")
    attempts = _read_json_dir(settings.applications_dir / "submission_attempts")
    approvals = _read_json_dir(settings.applications_dir / "approvals")
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
    input[type="search"] {{ width: min(680px, 100%); padding: 10px 12px; border-radius: 12px; border: 1px solid #d5c8b5; }}
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
    manual_actions = ""
    if job.get("manual_submit_ready"):
        manual_actions = f"""
          <section class="manual-actions">
            <strong>Manual submit options</strong>
            <p><a class="review secondary" href="{job_url}">Open original application page</a></p>
            <p>To try pre-filling in the remote challenge browser:</p>
            <code>.venv/bin/python -m app.main apply --job-id {job_id} --prepare</code>
            <p>To open a final manual review session from the CLI:</p>
            <code>.venv/bin/python -m app.main apply --job-id {job_id} --confirm</code>
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
          <p>{'<a class="review" href="' + review_url + '">Review prepared application and press Submit</a>' if review_url else ''}</p>
          {manual_actions}
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


def render_manual_queue_html(status: dict[str, Any]) -> str:
    jobs = [job for job in status["jobs"] if job.get("manual_submit_ready")]
    rows = "\n".join(_job_article(job) for job in jobs) or "<p>No drafted/prepared jobs are waiting for manual submission.</p>"
    body = _summary_cards(status["counts"]) + f"<h2>Manual Submit Queue</h2><p>{len(jobs)} jobs ready for you to review manually.</p>{rows}"
    return _dashboard_shell(title="Manual Submit Queue", generated_at=status["generated_at"], body=body, active_path="/manual")


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
            if parsed.path != "/action":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            action = (parse_qs(body).get("action") or [""])[0]
            result = _dashboard_action(settings, action)
            status = tracker_status(settings)
            message = f"{action}: {'ok' if result.get('ok') else 'failed'}\n{result.get('output', '')}".strip()
            active = "/search" if action.startswith("search-") else "/worker"
            if active == "/search":
                payload = render_search_html(settings, status, message=message).encode("utf-8")
            else:
                payload = render_worker_html(settings, status, message=message).encode("utf-8")
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
