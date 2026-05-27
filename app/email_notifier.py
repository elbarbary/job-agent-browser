"""Daily report generation and explicitly confirmed optional SMTP sending."""

from __future__ import annotations

import os
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from .application_agent import ApplicationRepository
from .config import Settings
from .policy import RiskClass, assert_action_allowed
from .webabi.recorder import AuditRecorder
from .webabi.schema import ActionRecord


class EmailConfigurationError(ValueError):
    """Raised when explicitly requested SMTP delivery is not configured."""


def generate_daily_update(settings: Settings) -> Path:
    settings.ensure_directories()
    repository = ApplicationRepository(settings)
    jobs = repository.load_jobs()
    drafts = sorted((settings.applications_dir / "drafts").glob("*.json"))
    approvals = sorted((settings.applications_dir / "approvals").glob("*.json"))
    lines = [
        "# Daily Job Application Update",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "## Summary",
        "",
        f"- Jobs found: {len(jobs)}",
        f"- Jobs shortlisted: {len(jobs)}",
        f"- Applications drafted: {len(drafts)}",
        "- Applications submitted: manually reported only (not automated by this app)",
        f"- Submission approvals recorded: {len(approvals)}",
        "",
        "## Questions Needing Input",
        "",
    ]
    questions: set[str] = set()
    for job in jobs:
        questions.update(job.get("risks_uncertainties") or [])
    lines.extend(f"- {question}" for question in sorted(questions))
    if not questions:
        lines.append("- None currently recorded.")
    lines.extend(["", "## Errors / Failures", "", "- Review audit logs for recorded workflow failures.", ""])
    path = settings.applications_dir / "daily_update.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o600)
    return path


def send_update(settings: Settings, report_path: Path, recorder: AuditRecorder) -> None:
    required = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_APP_PASSWORD", "SMTP_FROM", "SMTP_TO")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise EmailConfigurationError(f"SMTP is not configured: missing {', '.join(missing)}")
    assert_action_allowed(RiskClass.EMAIL_SEND, confirmed=True)
    message = EmailMessage()
    message["Subject"] = "Job application daily update"
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = os.environ["SMTP_TO"]
    message.set_content(report_path.read_text(encoding="utf-8"))
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as server:
        if os.getenv("SMTP_USE_TLS", "true").casefold() == "true":
            server.starttls()
        server.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_APP_PASSWORD"])
        server.send_message(message)
    recorder.record(
        ActionRecord(
            run_id=recorder.run_id,
            workflow="daily_update_email",
            page_url="smtp://configured-server",
            page_title="Daily update",
            visible_action_candidates=[],
            selected_action="send_email_update",
            risk_classification=RiskClass.EMAIL_SEND,
            input_values={"smtp_app_password": os.environ["SMTP_APP_PASSWORD"]},
            preconditions=["typed confirmation: SEND UPDATE"],
            postconditions=["SMTP send_message completed"],
            result="sent_after_explicit_confirmation",
            approved=True,
        )
    )
