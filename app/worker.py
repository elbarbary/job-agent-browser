"""Long-running safe worker for local job discovery and draft preparation."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
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
from .source_catalog import domains_for_names
from .telegram_notifier import TelegramConfigurationError, load_telegram_config, send_telegram_message, telegram_ready
from .tracker import format_tracker_chat, tracker_status
from .watchlist import load_watchlist
from .webabi.recorder import AuditRecorder
from .whatsapp_notifier import WhatsAppConfigurationError, load_whatsapp_config, send_whatsapp_message, whatsapp_ready


LOGGER = logging.getLogger("job_agent_worker")
DEFAULT_WORKER_CYCLE_TIMEOUT_SECONDS = 3600
DEFAULT_AUTOPILOT_JOB_TIMEOUT_SECONDS = 300
DEFAULT_BROWSER_TMP_CLEANUP_AGE_SECONDS = 6 * 60 * 60


def _job_score(job: dict[str, Any]) -> int:
    try:
        return int(job.get("match_score", 0))
    except (TypeError, ValueError):
        return 0


def select_worker_jobs(
    ranked_jobs: list[dict[str, Any]],
    *,
    min_score: int,
    draft_top_n: int,
    autopilot_scan_top_n: int,
    handled_job_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return visible drafts and the wider autopilot candidate scan window."""
    eligible = [job for job in ranked_jobs if _job_score(job) >= min_score]
    handled_job_ids = handled_job_ids or set()
    autopilot_eligible = [job for job in eligible if str(job.get("id")) not in handled_job_ids]
    return eligible[: max(0, draft_top_n)], autopilot_eligible[: max(0, autopilot_scan_top_n)]


def _existing_job_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {item.stem for item in path.glob("*.json") if item.is_file()}


def _draft_path(settings: Settings, job_id: str) -> Path:
    return settings.applications_dir / "drafts" / f"{job_id}.json"


def cleanup_browser_use_temp_dirs(max_age_seconds: int = DEFAULT_BROWSER_TMP_CLEANUP_AGE_SECONDS) -> dict[str, Any]:
    """Delete stale Browser Use temp profiles without touching persistent sessions."""
    temp_root = Path(tempfile.gettempdir())
    now = datetime.now().timestamp()
    removed: list[str] = []
    failed: list[dict[str, str]] = []
    patterns = ("browser-use-user-data-dir-*", "browser-use-downloads-*")
    for pattern in patterns:
        for path in temp_root.glob(pattern):
            try:
                if not path.is_dir():
                    continue
                age = now - path.stat().st_mtime
                if age < max_age_seconds:
                    continue
                shutil.rmtree(path)
                removed.append(str(path))
            except Exception as exc:
                failed.append({"path": str(path), "error": str(exc)})
    return {"removed": removed, "failed": failed}


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


def _last_discovery_lane(settings: Settings) -> str | None:
    status_path = settings.applications_dir / "worker_status.json"
    if not status_path.exists():
        return None
    try:
        return str(json.loads(status_path.read_text(encoding="utf-8")).get("discovery_lane") or "")
    except (json.JSONDecodeError, OSError):
        return None


def _discovery_plan(settings: Settings, watchlist: dict[str, Any]) -> tuple[bool, bool, str]:
    mode = str(watchlist.get("discovery_mode") or "alternate").casefold()
    if mode == "online":
        return True, False, "online"
    if mode == "source_urls":
        return False, True, "source_urls"
    if mode == "both":
        return True, True, "both"
    if not watchlist.get("source_urls"):
        return True, False, "online"
    last_lane = _last_discovery_lane(settings)
    if last_lane == "online":
        return False, True, "source_urls"
    return True, False, "online"


def _write_worker_status(settings: Settings, status: dict[str, Any]) -> Path:
    status_path = settings.applications_dir / "worker_status.json"
    status_path.write_text(json.dumps(status, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    status_path.chmod(0o600)
    return status_path


def _update_worker_status(settings: Settings, status: dict[str, Any], **updates: Any) -> Path:
    status.update(updates)
    status["updated_at"] = datetime.now(UTC).isoformat()
    if not status.get("in_progress"):
        status["finished_at"] = status["updated_at"]
    return _write_worker_status(settings, status)


def _new_worker_status(
    *,
    watchlist: dict[str, Any],
    phase: str,
    in_progress: bool,
    started_at: str | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "generated_at": now,
        "updated_at": now,
        "started_at": started_at or now,
        "finished_at": None if in_progress else now,
        "phase": phase,
        "in_progress": in_progress,
        "jobs_known": 0,
        "jobs_found_this_run": 0,
        "discovery_mode": str(watchlist.get("discovery_mode") or "alternate"),
        "discovery_lane": None,
        "run_online_sources": None,
        "run_source_urls": None,
        "source_urls_per_cycle": int(watchlist.get("source_urls_per_cycle", 10)),
        "source_url_timeout_seconds": int(watchlist.get("source_url_timeout_seconds", 120)),
        "eligible_jobs": 0,
        "drafted_job_ids": [],
        "autopilot_submitted_job_ids": [],
        "autopilot_blocked": [],
        "daily_update": None,
        "audit_log": None,
        "errors": errors or [],
    }


async def run_once(settings: Settings, *, with_llm: bool | None = None) -> dict[str, Any]:
    settings.ensure_directories()
    cleanup_result = cleanup_browser_use_temp_dirs()
    watchlist = load_watchlist(settings)
    recorder = AuditRecorder(settings.log_dir / "runs")
    repository = ApplicationRepository(settings)
    existing = {str(job.get("url")): job for job in repository.load_jobs()}
    found: list[dict[str, Any]] = []
    errors: list[str] = []
    started_at = datetime.now(UTC).isoformat()
    run_online, run_source_urls, discovery_lane = _discovery_plan(settings, watchlist)
    status = _new_worker_status(watchlist=watchlist, phase="starting", in_progress=True, started_at=started_at)
    status.update(
        {
            "discovery_lane": discovery_lane,
            "run_online_sources": run_online,
            "run_source_urls": run_source_urls,
            "audit_log": str(recorder.path),
            "browser_tmp_cleanup": {
                "removed_count": len(cleanup_result["removed"]),
                "failed": cleanup_result["failed"],
            },
        }
    )
    _write_worker_status(settings, status)
    _update_worker_status(settings, status, phase="discovering")

    if run_online and watchlist.get("public_feeds_enabled", True):
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

    if run_source_urls and not watchlist.get("source_urls", []):
        errors.append("source URL discovery selected, but no source_urls are configured")

    if run_source_urls:
        source_url_limit = max(1, int(watchlist.get("source_urls_per_cycle", 10)))
        source_url_timeout = max(15, int(watchlist.get("source_url_timeout_seconds", 120)))
        for item in watchlist.get("source_urls", [])[:source_url_limit]:
            source_url = item["url"] if isinstance(item, dict) else str(item)
            try:
                jobs = await asyncio.wait_for(
                    search_and_rank_jobs(
                        settings,
                        recorder,
                        "approved source url",
                        "user watchlist",
                        source_url=source_url,
                        max_results=1,
                    ),
                    timeout=source_url_timeout,
                )
                found.extend(jobs)
            except TimeoutError:
                errors.append(f"{source_url}: timed out after {source_url_timeout}s")
                LOGGER.warning("source_url timed out after %ss: %s", source_url_timeout, source_url)
            except Exception as exc:
                errors.append(f"{source_url}: {exc}")
                LOGGER.exception("source_url failed: %s", source_url)

    if run_online and watchlist.get("queries_enabled", False):
        source_domains = domains_for_names(watchlist.get("enabled_source_names") or [])
        for query in watchlist.get("queries", []):
            try:
                jobs = await search_and_rank_jobs(
                    settings,
                    recorder,
                    str(query["query"]),
                    str(query["location"]),
                    max_results=int(watchlist.get("max_results_per_query", 5)),
                    source_domains=source_domains,
                )
                found.extend(jobs)
            except Exception as exc:
                errors.append(f"{query}: {exc}")
                LOGGER.exception("query failed: %s", query)
                if "anti-bot challenge" in str(exc):
                    errors.append("public search queries paused for this run after search-engine anti-bot challenge")
                    LOGGER.warning("public search queries paused for this run after anti-bot challenge")
                    break
    elif run_online:
        LOGGER.info("online query search is disabled; public feed APIs may still run")
    else:
        LOGGER.info("online discovery skipped for lane=%s", discovery_lane)

    for job in found:
        existing[str(job.get("url"))] = job
    ranked = sorted(existing.values(), key=lambda item: int(item.get("match_score", 0)), reverse=True)
    repository.save_jobs(ranked)

    min_score = int(watchlist.get("min_auto_draft_score", 45))
    discovery_status = {
        "generated_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "started_at": started_at,
        "finished_at": None,
        "phase": "discovery_complete",
        "in_progress": True,
        "jobs_known": len(ranked),
        "jobs_found_this_run": len(found),
        "discovery_mode": str(watchlist.get("discovery_mode") or "alternate"),
        "discovery_lane": discovery_lane,
        "run_online_sources": run_online,
        "run_source_urls": run_source_urls,
        "source_urls_per_cycle": int(watchlist.get("source_urls_per_cycle", 10)),
        "source_url_timeout_seconds": int(watchlist.get("source_url_timeout_seconds", 120)),
        "eligible_jobs": len([job for job in ranked if _job_score(job) >= min_score]),
        "drafted_job_ids": [],
        "autopilot_submitted_job_ids": [],
        "autopilot_blocked": [],
        "daily_update": None,
        "audit_log": str(recorder.path),
        "browser_tmp_cleanup": {
            "removed_count": len(cleanup_result["removed"]),
            "failed": cleanup_result["failed"],
        },
        "errors": errors,
    }
    _write_worker_status(settings, discovery_status)
    status = discovery_status

    drafted: list[str] = []
    autopilot_submitted: list[str] = []
    autopilot_blocked: list[dict[str, Any]] = []
    workflow = ApplicationWorkflow(settings, recorder)
    profile = load_profile(settings)
    use_llm = watchlist.get("with_llm_advisory", True) if with_llm is None else with_llm
    llm = LocalLLMClient(settings) if use_llm else None
    autopilot_config = load_autopilot(settings)
    max_autopilot_submits = int(autopilot_config.get("max_submissions_per_run", 1))
    autopilot_job_timeout = max(
        30,
        int(autopilot_config.get("autopilot_job_timeout_seconds", DEFAULT_AUTOPILOT_JOB_TIMEOUT_SECONDS)),
    )
    browser_engine = BrowserEngine(settings, recorder)
    draft_top_n = int(watchlist.get("auto_draft_top_n", 5))
    autopilot_scan_top_n = int(
        watchlist.get("autopilot_scan_top_n", max(draft_top_n, max_autopilot_submits * 3))
    )
    draftable_jobs, autopilot_candidates = select_worker_jobs(
        ranked,
        min_score=min_score,
        draft_top_n=draft_top_n,
        autopilot_scan_top_n=autopilot_scan_top_n,
        handled_job_ids=(
            _existing_job_ids(settings.applications_dir / "submissions")
            | _existing_job_ids(settings.applications_dir / "submission_attempts")
        ),
    )
    draft_paths: dict[str, Path] = {}
    _update_worker_status(
        settings,
        status,
        phase="drafting",
        auto_draft_top_n=draft_top_n,
        autopilot_scan_top_n=autopilot_scan_top_n,
        max_autopilot_submissions_per_run=max_autopilot_submits,
        autopilot_job_timeout_seconds=autopilot_job_timeout,
        drafted_job_ids=drafted,
        autopilot_submitted_job_ids=autopilot_submitted,
        autopilot_blocked=autopilot_blocked,
        errors=errors,
    )
    for job in draftable_jobs:
        job_id = str(job["id"])
        existing_draft = _draft_path(settings, job_id)
        if existing_draft.exists():
            draft_paths[job_id] = existing_draft
            drafted.append(job_id)
            _update_worker_status(settings, status, drafted_job_ids=drafted, errors=errors)
            continue
        advisory = None
        if llm:
            try:
                advisory = llm.grounded_job_advisory(profile, job)
            except LocalLLMError as exc:
                errors.append(f"LLM advisory failed for {job_id}: {exc}")
        draft_path = workflow.draft(job_id, advisory)
        draft_paths[job_id] = draft_path
        drafted.append(job_id)
        _update_worker_status(settings, status, drafted_job_ids=drafted, errors=errors)

    _update_worker_status(
        settings,
        status,
        phase="autopilot",
        drafted_job_ids=drafted,
        autopilot_submitted_job_ids=autopilot_submitted,
        autopilot_blocked=autopilot_blocked,
        errors=errors,
    )
    for job in autopilot_candidates:
        job_id = str(job["id"])
        if repository.has_submission(job_id):
            autopilot_blocked.append({"job_id": job_id, "reasons": ["job already has a local submission record"]})
            _update_worker_status(settings, status, autopilot_blocked=autopilot_blocked)
            continue
        if repository.has_submission_attempt(job_id):
            autopilot_blocked.append({"job_id": job_id, "reasons": ["job already has an unverified submit-click record"]})
            _update_worker_status(settings, status, autopilot_blocked=autopilot_blocked)
            continue
        if len(autopilot_submitted) >= max_autopilot_submits:
            break
        draft_path = draft_paths.get(job_id)
        if draft_path is None:
            existing_draft = _draft_path(settings, job_id)
            if existing_draft.exists():
                draft_path = existing_draft
                draft_paths[job_id] = draft_path
        if draft_path is None:
            advisory = None
            if llm:
                try:
                    advisory = llm.grounded_job_advisory(profile, job)
                except LocalLLMError as exc:
                    errors.append(f"LLM advisory failed for {job_id}: {exc}")
            draft_path = workflow.draft(job_id, advisory)
            draft_paths[job_id] = draft_path
            drafted.append(job_id)
            _update_worker_status(settings, status, drafted_job_ids=drafted, errors=errors)
        try:
            draft_payload = json.loads(draft_path.read_text(encoding="utf-8"))
            decision = decide_autopilot_for_job(
                draft_payload["job"],
                draft_payload["answers"],
                autopilot_config,
            )
            if not decision.allowed:
                autopilot_blocked.append({"job_id": job_id, "reasons": decision.reasons})
                _update_worker_status(settings, status, autopilot_blocked=autopilot_blocked)
                continue
            _update_worker_status(
                settings,
                status,
                current_autopilot_job_id=job_id,
                autopilot_attempt_started_at=datetime.now(UTC).isoformat(),
            )
            result = await asyncio.wait_for(
                browser_engine.auto_submit_application(
                    str(job["url"]),
                    job_id,
                    draft_payload["answers"],
                    autopilot_config,
                ),
                timeout=autopilot_job_timeout,
            )
            if result.get("submitted"):
                workflow.record_autopilot_submission(job_id, result)
                autopilot_submitted.append(job_id)
                _update_worker_status(
                    settings,
                    status,
                    autopilot_submitted_job_ids=autopilot_submitted,
                    current_autopilot_job_id=None,
                    autopilot_attempt_started_at=None,
                )
            elif result.get("clicked"):
                workflow.record_autopilot_attempt(job_id, result)
                autopilot_blocked.append(
                    {"job_id": job_id, "reasons": result.get("errors", ["submit click was unverified"])}
                )
                _update_worker_status(
                    settings,
                    status,
                    autopilot_blocked=autopilot_blocked,
                    current_autopilot_job_id=None,
                    autopilot_attempt_started_at=None,
                )
            else:
                autopilot_blocked.append({"job_id": job_id, "reasons": result.get("errors", [])})
                _update_worker_status(
                    settings,
                    status,
                    autopilot_blocked=autopilot_blocked,
                    current_autopilot_job_id=None,
                    autopilot_attempt_started_at=None,
                )
        except TimeoutError:
            reason = f"autopilot attempt timed out after {autopilot_job_timeout}s"
            autopilot_blocked.append({"job_id": job_id, "reasons": [reason]})
            LOGGER.warning("autopilot timed out for %s after %ss", job_id, autopilot_job_timeout)
            _update_worker_status(
                settings,
                status,
                autopilot_blocked=autopilot_blocked,
                current_autopilot_job_id=None,
                autopilot_attempt_started_at=None,
            )
        except Exception as exc:
            errors.append(f"Autopilot failed for {job_id}: {exc}")
            LOGGER.exception("autopilot failed for %s", job_id)
            _update_worker_status(
                settings,
                status,
                errors=errors,
                autopilot_blocked=autopilot_blocked,
                current_autopilot_job_id=None,
                autopilot_attempt_started_at=None,
            )

    _update_worker_status(settings, status, current_autopilot_job_id=None, autopilot_attempt_started_at=None)

    report = generate_daily_update(settings)
    cleanup_result = cleanup_browser_use_temp_dirs()
    status = {
        "generated_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "started_at": started_at,
        "finished_at": None,
        "phase": "complete",
        "in_progress": False,
        "jobs_known": len(ranked),
        "jobs_found_this_run": len(found),
        "discovery_mode": str(watchlist.get("discovery_mode") or "alternate"),
        "discovery_lane": discovery_lane,
        "run_online_sources": run_online,
        "run_source_urls": run_source_urls,
        "source_urls_per_cycle": int(watchlist.get("source_urls_per_cycle", 10)),
        "source_url_timeout_seconds": int(watchlist.get("source_url_timeout_seconds", 120)),
        "eligible_jobs": len([job for job in ranked if _job_score(job) >= min_score]),
        "auto_draft_top_n": draft_top_n,
        "autopilot_scan_top_n": autopilot_scan_top_n,
        "max_autopilot_submissions_per_run": max_autopilot_submits,
        "autopilot_job_timeout_seconds": autopilot_job_timeout,
        "drafted_job_ids": drafted,
        "autopilot_submitted_job_ids": autopilot_submitted,
        "autopilot_blocked": autopilot_blocked,
        "daily_update": str(report),
        "audit_log": str(recorder.path),
        "browser_tmp_cleanup": {
            "removed_count": len(cleanup_result["removed"]),
            "failed": cleanup_result["failed"],
        },
        "errors": errors,
    }
    status["finished_at"] = status["updated_at"]
    status_path = _write_worker_status(settings, status)
    telegram_config = load_telegram_config(settings)
    tracker_message = ""
    if telegram_ready(telegram_config) and telegram_config.get("notify_on_worker_run"):
        try:
            tracker_message = tracker_message or format_tracker_chat(tracker_status(settings))
            send_telegram_message(settings, tracker_message)
        except (TelegramConfigurationError, Exception) as exc:
            errors.append(f"Telegram notification failed: {exc}")
            status["errors"] = errors
            status_path.write_text(json.dumps(status, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
            LOGGER.exception("telegram notification failed")
    whatsapp_config = load_whatsapp_config(settings)
    if whatsapp_ready(whatsapp_config) and whatsapp_config.get("notify_on_worker_run"):
        try:
            tracker_message = tracker_message or format_tracker_chat(tracker_status(settings))
            send_whatsapp_message(settings, tracker_message)
        except (WhatsAppConfigurationError, Exception) as exc:
            errors.append(f"WhatsApp notification failed: {exc}")
            status["errors"] = errors
            status_path.write_text(json.dumps(status, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
            LOGGER.exception("whatsapp notification failed")
    LOGGER.info("worker run complete: %s", status)
    return status


async def run_forever(settings: Settings, interval_minutes: int | None = None) -> None:
    setup_logging(settings)
    watchlist = load_watchlist(settings)
    interval = int(interval_minutes or watchlist.get("interval_minutes", 180))
    LOGGER.info("worker starting with interval_minutes=%s", interval)
    while True:
        watchlist = load_watchlist(settings)
        timeout_seconds = max(60, int(watchlist.get("worker_cycle_timeout_seconds", DEFAULT_WORKER_CYCLE_TIMEOUT_SECONDS)))
        try:
            await asyncio.wait_for(run_once(settings), timeout=timeout_seconds)
        except Exception as exc:
            LOGGER.exception("worker run failed")
            cleanup_result = cleanup_browser_use_temp_dirs()
            failed_status = _new_worker_status(
                watchlist=watchlist,
                phase="failed",
                in_progress=False,
                errors=[f"worker run failed: {exc}"],
            )
            failed_status["browser_tmp_cleanup"] = {
                "removed_count": len(cleanup_result["removed"]),
                "failed": cleanup_result["failed"],
            }
            _write_worker_status(settings, failed_status)
        await asyncio.sleep(max(1, interval) * 60)
