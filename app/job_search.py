"""Read-only public job discovery and CV-grounded ranking."""

from __future__ import annotations

import hashlib
from urllib.parse import quote_plus

from .application_agent import ApplicationRepository
from .browser_engine import BrowserEngine
from .config import Settings
from .cv_store import load_profile
from .job_profile import RankedJob, match_job
from .preferences import load_preferences
from .webabi.recorder import AuditRecorder


def build_search_url(query: str, location: str) -> str:
    terms = (
        f"{query} {location} "
        "(site:jobs.lever.co OR site:boards.greenhouse.io OR site:job-boards.greenhouse.io "
        "OR site:careers.smartrecruiters.com)"
    )
    return f"https://html.duckduckgo.com/html/?q={quote_plus(terms)}"


def merge_ranked_jobs(existing: list[dict[str, object]], discovered: list[dict[str, object]]) -> list[dict[str, object]]:
    """Merge new discovery without deleting prior local job records."""
    by_url: dict[str, dict[str, object]] = {
        str(job.get("url")): job for job in existing if job.get("url")
    }
    for job in discovered:
        url = str(job.get("url") or "")
        if url:
            by_url[url] = job
    merged = list(by_url.values())
    merged.sort(key=lambda item: int(item.get("match_score", 0)), reverse=True)
    return merged


async def search_and_rank_jobs(
    settings: Settings,
    recorder: AuditRecorder,
    query: str,
    location: str,
    source_url: str | None = None,
    max_results: int = 5,
) -> list[dict[str, object]]:
    profile = load_profile(settings)
    preferences = load_preferences(settings)
    engine = BrowserEngine(settings, recorder)
    if source_url:
        links = [await engine.read_job_detail(source_url)]
    else:
        links = await engine.extract_job_links(build_search_url(query, location))
    jobs: list[dict[str, object]] = []
    seen: set[str] = set()
    for link in links[:max_results]:
        if link["url"] in seen:
            continue
        seen.add(link["url"])
        identifier = hashlib.sha256(link["url"].encode("utf-8")).hexdigest()[:12]
        detail = link if link.get("description") else await engine.read_job_detail(link["url"])
        ranked = match_job(
            RankedJob(
                id=identifier,
                title=detail["title"],
                company=None,
                location=location,
                url=detail["url"],
                description=detail.get("description", ""),
                source="approved_source_url" if source_url else "duckduckgo_public_ats_search",
            ),
            profile,
            preferences,
        )
        jobs.append(ranked.to_dict())
    jobs.sort(key=lambda item: int(item["match_score"]), reverse=True)
    repository = ApplicationRepository(settings)
    repository.save_jobs(merge_ranked_jobs(repository.load_jobs(), jobs))
    return jobs
