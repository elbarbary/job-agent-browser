"""Command-line entrypoint for the local-first job application browser agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .autopilot import decide_autopilot_for_job, load_autopilot, write_default_autopilot
from .application_agent import ApplicationRepository, ApplicationWorkflow
from .browser_engine import BrowserEngine, BrowserSafetyError
from .config import ConfigurationError, Settings
from .cv_store import CVError, ingest_cv, load_profile
from .email_notifier import EmailConfigurationError, generate_daily_update, send_update
from .job_search import search_and_rank_jobs
from .llm_client import LocalLLMClient, LocalLLMError
from .policy import PolicyViolation, RiskClass, require_typed_confirmation
from .preferences import write_default_preferences
from .watchlist import write_default_watchlist
from .webabi.recorder import AuditRecorder
from .webabi.replay import summarize_log
from .webabi.schema import ActionRecord
from .worker import run_forever, run_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local-first, approval-gated job browser agent")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest-cv", help="Extract a PDF or DOCX CV into a grounded profile")
    ingest.add_argument("path", type=Path)
    sub.add_parser("login-session", help="Open a persistent visible browser for manual login")
    sub.add_parser("smoke-test", help="Open example.com safely and report visible interactive items")
    sub.add_parser("init-preferences", help="Write the private user-confirmed job preference profile")
    sub.add_parser("init-watchlist", help="Write the private background worker watchlist")
    sub.add_parser("init-autopilot", help="Write the private opt-in autopilot submission template")
    worker_once = sub.add_parser("worker-once", help="Run one safe background worker cycle")
    worker_once.add_argument("--no-llm", action="store_true", help="Disable LLM advisory for this run")
    worker = sub.add_parser("worker", help="Run the safe background worker forever")
    worker.add_argument("--interval-minutes", type=int)
    llm = sub.add_parser("llm-status", help="Report local Ollama/model status")
    llm.add_argument("--test", action="store_true", help="Run a one-prompt model API test")
    search = sub.add_parser("search-jobs", help="Read public ATS search results and rank them")
    search.add_argument("--query", required=True)
    search.add_argument("--location", required=True)
    search.add_argument(
        "--source-url",
        help="Optional approved public ATS posting URL when search engines block automation",
    )
    search.add_argument("--max-results", type=int, default=5)
    sub.add_parser("review-jobs", help="Review saved ranked job discoveries")
    apply = sub.add_parser("apply", help="Generate a draft or approve a manual final review")
    apply.add_argument("--job-id", required=True)
    mode = apply.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--confirm", action="store_true")
    mode.add_argument("--auto-submit", action="store_true")
    apply.add_argument(
        "--with-llm",
        action="store_true",
        help="Add non-authoritative grounded advisory notes from local Gemma in dry-run mode",
    )
    update = sub.add_parser("daily-update", help="Generate a local daily report")
    update.add_argument("--send", action="store_true", help="Send through configured SMTP after confirmation")
    audit = sub.add_parser("audit-log", help="Render an audit JSONL log as a short replay summary")
    audit.add_argument("path", nargs="?", type=Path)
    return parser


def _recorder(settings: Settings) -> AuditRecorder:
    settings.ensure_directories()
    return AuditRecorder(settings.log_dir / "runs")


def _review_jobs(settings: Settings) -> None:
    jobs = ApplicationRepository(settings).load_jobs()
    if not jobs:
        print("No saved jobs. Run search-jobs first.")
        return
    for job in jobs:
        print(f"{job['id']} | score {job.get('match_score', 0):>3} | {job.get('title', '')}")
        print(f"  {job.get('url', '')}")
        for uncertainty in (job.get("risks_uncertainties") or [])[:3]:
            print(f"  ! {uncertainty}")


async def _run_async(args: argparse.Namespace, settings: Settings) -> int:
    recorder = _recorder(settings)
    if args.command == "login-session":
        await BrowserEngine(settings, recorder).manual_login_session()
        print(f"Session retained locally in {settings.browser_profile_dir}")
        return 0
    if args.command == "smoke-test":
        observed = await BrowserEngine(settings, recorder).smoke_test()
        print(f"Opened: {observed.title} ({observed.url})")
        for candidate in observed.candidates:
            print(f"- {candidate.action_type}: {candidate.label}")
        print(f"Audit log: {recorder.path}")
        return 0
    if args.command == "search-jobs":
        jobs = await search_and_rank_jobs(
            settings, recorder, args.query, args.location, args.source_url, args.max_results
        )
        print(f"Saved {len(jobs)} job result(s) to {settings.applications_dir / 'jobs.json'}")
        _review_jobs(settings)
        print(f"Audit log: {recorder.path}")
        return 0
    if args.command == "apply" and args.confirm:
        repository = ApplicationRepository(settings)
        job = repository.find_job(args.job_id)
        print(json.dumps(job, indent=2, ensure_ascii=True))
        expected = f"SUBMIT {args.job_id}"
        actual = input(f"Type {expected} to open final manual submission review: ")
        require_typed_confirmation(actual, expected)
        workflow = ApplicationWorkflow(settings, recorder)
        approval_path = workflow.approve_for_manual_submission(args.job_id)
        print(f"Approval saved: {approval_path}")
        await BrowserEngine(settings, recorder).manual_submission_review(str(job["url"]), args.job_id)
        return 0
    if args.command == "apply" and args.auto_submit:
        workflow = ApplicationWorkflow(settings, recorder)
        if workflow.repository.has_submission(args.job_id):
            print(json.dumps({"autopilot_allowed": False, "reasons": ["job already has a local submission record"]}, indent=2))
            return 2
        draft_path = workflow.draft(args.job_id)
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        config = load_autopilot(settings)
        decision = decide_autopilot_for_job(draft["job"], draft["answers"], config)
        if not decision.allowed:
            print(json.dumps({"autopilot_allowed": False, "reasons": decision.reasons}, indent=2))
            return 2
        result = await BrowserEngine(settings, recorder).auto_submit_application(
            str(draft["job"]["url"]),
            args.job_id,
            draft["answers"],
            config,
        )
        if result.get("submitted"):
            workflow.record_autopilot_submission(args.job_id, result)
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0 if result.get("submitted") else 2
    if args.command == "worker-once":
        status = await run_once(settings, with_llm=not args.no_llm)
        print(json.dumps(status, indent=2, ensure_ascii=True))
        return 0
    if args.command == "worker":
        await run_forever(settings, args.interval_minutes)
        return 0
    return 1


def run(args: argparse.Namespace, settings: Settings) -> int:
    if args.command in {"login-session", "smoke-test", "search-jobs", "worker-once", "worker"} or (
        args.command == "apply" and (args.confirm or args.auto_submit)
    ):
        if args.command == "apply" and (args.confirm or args.auto_submit) and args.with_llm:
            raise PolicyViolation("--with-llm is available only for a dry-run draft.")
        return asyncio.run(_run_async(args, settings))
    if args.command == "ingest-cv":
        result = ingest_cv(args.path, settings)
        recorder = _recorder(settings)
        recorder.record(
            ActionRecord(
                run_id=recorder.run_id,
                workflow="cv_ingestion",
                page_url="local://cv",
                page_title=result.source_path.name,
                visible_action_candidates=[],
                selected_action="extract_cv_profile",
                risk_classification=RiskClass.READ_ONLY,
                preconditions=["user provided local CV file"],
                postconditions=["extracted markdown saved", "candidate profile JSON saved"],
                result="success",
            )
        )
        print(f"Extracted text: {result.extracted_text_path}")
        print(f"Candidate profile: {result.profile_path}")
        return 0
    if args.command == "llm-status":
        client = LocalLLMClient(settings)
        print(json.dumps(client.status(), indent=2))
        if args.test:
            print(f"Model reply: {client.smoke_test()}")
        return 0
    if args.command == "init-preferences":
        path = write_default_preferences(settings)
        print(f"Job preferences saved: {path}")
        return 0
    if args.command == "init-watchlist":
        path = write_default_watchlist(settings)
        print(f"Worker watchlist saved: {path}")
        return 0
    if args.command == "init-autopilot":
        path = write_default_autopilot(settings)
        print(f"Autopilot template saved: {path}")
        return 0
    if args.command == "review-jobs":
        _review_jobs(settings)
        return 0
    if args.command == "apply" and args.dry_run:
        recorder = _recorder(settings)
        advisory = None
        if args.with_llm:
            job = ApplicationRepository(settings).find_job(args.job_id)
            advisory = LocalLLMClient(settings).grounded_job_advisory(load_profile(settings), job)
        output = ApplicationWorkflow(settings, recorder).draft(args.job_id, advisory)
        print(f"Draft saved without submission: {output}")
        print(f"Audit log: {recorder.path}")
        return 0
    if args.command == "daily-update":
        report = generate_daily_update(settings)
        print(f"Daily update draft: {report}")
        if args.send:
            expected = "SEND UPDATE"
            actual = input(f"Type {expected} to send the configured SMTP email: ")
            require_typed_confirmation(actual, expected)
            send_update(settings, report, _recorder(settings))
            print("Daily update sent after explicit confirmation.")
        return 0
    if args.command == "audit-log":
        path = args.path
        if path is None:
            logs = sorted((settings.log_dir / "runs").glob("*.jsonl"))
            if not logs:
                print("No audit logs found.")
                return 0
            path = logs[-1]
        print(summarize_log(path))
        return 0
    return 1


def main() -> int:
    try:
        settings = Settings.load()
        return run(build_parser().parse_args(), settings)
    except (
        BrowserSafetyError,
        ConfigurationError,
        CVError,
        LocalLLMError,
        PolicyViolation,
        EmailConfigurationError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
