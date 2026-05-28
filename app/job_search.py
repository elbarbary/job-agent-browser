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
from .web_search import build_job_search_terms, search_web
from .webabi.recorder import AuditRecorder


def build_search_url(query: str, location: str) -> str:
    terms = build_job_search_terms(query, location)
    return f"https://html.duckduckgo.com/html/?q={quote_plus(terms)}"


async def discover_web_search_links(
    settings: Settings,
    query: str,
    location: str,
    *,
    max_results: int,
) -> list[dict[str, str]]:
    terms = build_job_search_terms(query, location)
    results = await search_web(settings, terms, max_results=max_results * 3)
    links: list[dict[str, str]] = []
    for result in results:
        if settings.is_allowed_url(result.url):
            links.append({"title": result.title, "url": result.url, "description": result.snippet})
        if len(links) >= max_results:
            break
    return links


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
        if settings.web_search_provider == "searxng":
            links = await discover_web_search_links(settings, query, location, max_results=max_results)
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
                source=(
                    "approved_source_url"
                    if source_url
                    else ("local_searxng_web_search" if settings.web_search_provider == "searxng" else "duckduckgo_public_ats_search")
                ),
            ),
            profile,
            preferences,
        )
        jobs.append(ranked.to_dict())
    jobs.sort(key=lambda item: int(item["match_score"]), reverse=True)
    repository = ApplicationRepository(settings)
    repository.save_jobs(merge_ranked_jobs(repository.load_jobs(), jobs))
    return jobs
