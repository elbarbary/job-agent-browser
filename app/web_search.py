"""Local-only web search client for a private SearXNG instance."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .config import Settings


class WebSearchError(RuntimeError):
    """Raised when the configured local web-search provider is unavailable."""


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str


def build_job_search_terms(query: str, location: str) -> str:
    return (
        f"{query} {location} "
        "(site:jobs.lever.co OR site:boards.greenhouse.io OR site:job-boards.greenhouse.io "
        "OR site:job-boards.eu.greenhouse.io OR site:jobs.ashbyhq.com "
        "OR site:jobs.workable.com OR site:apply.workable.com "
        "OR site:careers.smartrecruiters.com)"
    )


def _is_external_https_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host not in {"localhost"}
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


async def search_web(settings: Settings, query: str, *, max_results: int = 10) -> list[WebSearchResult]:
    if settings.web_search_provider == "disabled":
        raise WebSearchError("Local web search is disabled. Set JOB_AGENT_WEB_SEARCH_PROVIDER=searxng.")
    if settings.web_search_provider != "searxng":
        raise WebSearchError(f"Unsupported web search provider: {settings.web_search_provider}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{settings.searxng_base_url}/search",
            params={
                "q": query,
                "format": "json",
                "language": "en",
                "safesearch": "0",
            },
            headers={"User-Agent": "job-agent-browser/0.1"},
        )
    if response.status_code == 403:
        raise WebSearchError("SearXNG JSON output is disabled. Enable search.formats: [html, json].")
    response.raise_for_status()
    payload = response.json()
    results: list[WebSearchResult] = []
    seen: set[str] = set()
    for item in payload.get("results", []):
        url = str(item.get("url") or "")
        if not url or url in seen or not _is_external_https_url(url):
            continue
        seen.add(url)
        results.append(
            WebSearchResult(
                title=str(item.get("title") or url),
                url=url,
                snippet=str(item.get("content") or ""),
            )
        )
        if len(results) >= max_results:
            break
    return results
