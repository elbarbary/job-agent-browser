"""Long-running safe worker for local job discovery and draft preparation."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .autopilot import decide_autopilot_for_job, load_autopilot
from .application_agent import ApplicationRepository, ApplicationWorkflow
from .browser_engine import BrowserEngine
from .config import Settings
from .cv_store import load_profile
from .email_notifier import generate_daily_update
from .job_sources import discover_public_feed_jobs
from .job_search import search_and_rank_jobs
from .llm_client import LocalLLMClient, LocalLLMError
from .telegram_notifier import TelegramConfigurationError, load_telegram_config, send_telegram_message, telegram_ready
from .tracker import format_tracker_chat, tracker_status
from .watchlist import load_watchlist
from .webabi.recorder import AuditRecorder


LOGGER = logging.getLogger("job_agent_worker")


def setup_logging(settings: Settings) -> Path:
    settings.ensure_directories()
    path = settings.log_dir / "worker.log"
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    logging.basicConfig(
        filename=path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )
    return path


async def run_once(settings: Settings, *, with_llm: bool | None = None) -> dict[str, Any]:
    settings.ensure_directories()
    watchlist = load_watchlist(settings)
    recorder = AuditRecorder(settings.log_dir / "runs")
    repository = ApplicationRepository(settings)
    existing = {str(job.get("url")): job for job in repository.load_jobs()}
    found: list[dict[str, Any]] = []
    errors: list[str] = []

    if watchlist.get("public_feeds_enabled", True):
        try:
            found.extend(
                await discover_public_feed_jobs(
                    settings,
                    limit=int(watchlist.get("public_feed_limit", 40)),
                )
            )
        except Exception as exc:
            errors.append(f"public feeds: {exc}")
            LOGGER.exception("public feed discovery failed")

    for item in watchlist.get("source_urls", []):
        source_url = item["url"] if isinstance(item, dict) else str(item)
        try:
            jobs = await search_and_rank_jobs(
                settings,
                recorder,
                "approved source url",
                "user watchlist",
                source_url=source_url,
                max_results=1,
            )
            found.extend(jobs)
        except Exception as exc:
            errors.append(f"{source_url}: {exc}")
            LOGGER.exception("source_url failed: %s", source_url)

    if watchlist.get("queries_enabled", False):
        for query in watchlist.get("queries", []):
            try:
                jobs = await search_and_rank_jobs(
                    settings,
                    recorder,
                    str(query["query"]),
                    str(query["location"]),
                    max_results=int(watchlist.get("max_results_per_query", 5)),
                )
                found.extend(jobs)
            except Exception as exc:
                errors.append(f"{query}: {exc}")
                LOGGER.exception("query failed: %s", query)
    else:
        LOGGER.info("public search queries are disabled; processing source_urls only")

    for job in found:
        existing[str(job.get("url"))] = job
    ranked = sorted(existing.values(), key=lambda item: int(item.get("match_score", 0)), reverse=True)
    repository.save_jobs(ranked)

    drafted: list[str] = []
    autopilot_submitted: list[str] = []
    autopilot_blocked: list[dict[str, Any]] = []
    workflow = ApplicationWorkflow(settings, recorder)
    profile = load_profile(settings)
    use_llm = watchlist.get("with_llm_advisory", True) if with_llm is None else with_llm
    llm = LocalLLMClient(settings) if use_llm else None
    autopilot_config = load_autopilot(settings)
    max_autopilot_submits = int(autopilot_config.get("max_submissions_per_run", 1))
    browser_engine = BrowserEngine(settings, recorder)
    min_score = int(watchlist.get("min_auto_draft_score", 45))
    draftable_jobs = [
        job for job in ranked if int(job.get("match_score", 0)) >= min_score
    ][: int(watchlist.get("auto_draft_top_n", 5))]
    for job in draftable_jobs:
        job_id = str(job["id"])
        advisory = None
        if llm:
            try:
                advisory = llm.grounded_job_advisory(profile, job)
            except LocalLLMError as exc:
                errors.append(f"LLM advisory failed for {job_id}: {exc}")
        draft_path = workflow.draft(job_id, advisory)
        drafted.append(job_id)
        if repository.has_submission(job_id):
            autopilot_blocked.append({"job_id": job_id, "reasons": ["job already has a local submission record"]})
            continue
        if len(autopilot_submitted) >= max_autopilot_submits:
            continue
        try:
            draft_payload = json.loads(draft_path.read_text(encoding="utf-8"))
            decision = decide_autopilot_for_job(
                draft_payload["job"],
                draft_payload["answers"],
                autopilot_config,
            )
            if not decision.allowed:
                autopilot_blocked.append({"job_id": job_id, "reasons": decision.reasons})
                continue
            result = await browser_engine.auto_submit_application(
                str(job["url"]),
                job_id,
                draft_payload["answers"],
                autopilot_config,
            )
            if result.get("submitted"):
                workflow.record_autopilot_submission(job_id, result)
                autopilot_submitted.append(job_id)
            else:
                autopilot_blocked.append({"job_id": job_id, "reasons": result.get("errors", [])})
        except Exception as exc:
            errors.append(f"Autopilot failed for {job_id}: {exc}")
            LOGGER.exception("autopilot failed for %s", job_id)

    report = generate_daily_update(settings)
    status = {
        "generated_at": datetime.now(UTC).isoformat(),
        "jobs_known": len(ranked),
        "jobs_found_this_run": len(found),
        "drafted_job_ids": drafted,
        "autopilot_submitted_job_ids": autopilot_submitted,
        "autopilot_blocked": autopilot_blocked,
        "daily_update": str(report),
        "audit_log": str(recorder.path),
        "errors": errors,
    }
    status_path = settings.applications_dir / "worker_status.json"
    status_path.write_text(json.dumps(status, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    status_path.chmod(0o600)
    telegram_config = load_telegram_config(settings)
    if telegram_ready(telegram_config) and telegram_config.get("notify_on_worker_run"):
        try:
            send_telegram_message(settings, format_tracker_chat(tracker_status(settings)))
        except (TelegramConfigurationError, Exception) as exc:
            errors.append(f"Telegram notification failed: {exc}")
            status["errors"] = errors
            status_path.write_text(json.dumps(status, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
            LOGGER.exception("telegram notification failed")
    LOGGER.info("worker run complete: %s", status)
    return status


async def run_forever(settings: Settings, interval_minutes: int | None = None) -> None:
    setup_logging(settings)
    watchlist = load_watchlist(settings)
    interval = int(interval_minutes or watchlist.get("interval_minutes", 180))
    LOGGER.info("worker starting with interval_minutes=%s", interval)
    while True:
        try:
            await run_once(settings)
        except Exception:
            LOGGER.exception("worker run failed")
        await asyncio.sleep(max(1, interval) * 60)
