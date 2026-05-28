"""Public job feed discovery that does not depend on search engines."""

from __future__ import annotations

import hashlib
import html
import json
import re
from html.parser import HTMLParser
from typing import Any

import httpx

from .config import Settings
from .job_profile import RankedJob, match_job
from .preferences import load_preferences


TARGET_TITLE_RE = re.compile(
    r"\b("
    r"ai engineer|artificial intelligence engineer|machine learning engineer|ml engineer|"
    r"llm engineer|data scientist|data engineer|software engineer|software developer|"
    r"backend engineer|back-end engineer|full.?stack engineer|python developer|flutter developer|"
    r"product engineer|computer vision engineer|automation engineer|ai automation"
    r")\b",
    flags=re.IGNORECASE,
)
PRODUCT_TITLE_RE = re.compile(
    r"\b(product manager|associate product manager|junior product manager|product owner)\b",
    flags=re.IGNORECASE,
)
EARLY_CAREER_RE = re.compile(r"\b(graduate program|new grad|entry[- ]level|junior)\b", re.IGNORECASE)
TECHNICAL_CONTEXT_RE = re.compile(
    r"\b(ai|artificial intelligence|machine learning|ml|llm|software|developer|engineering|"
    r"api|platform|data|automation|integration|integrations|technical|cloud|saas|devtools)\b",
    flags=re.IGNORECASE,
)
SENIORITY_EXCLUDE_RE = re.compile(
    r"\b(senior|staff|principal|director|head|chief|vp|vice president|lead)\b",
    re.IGNORECASE,
)
NOISE_TITLE_RE = re.compile(
    r"\b(sales|marketing|office assistant|crew member|territory|medical science liaison|msl|"
    r"country director|client delivery|revenue systems|cinematic video editor|creative strategist|"
    r"people business partner|human resources|hr business partner|recruiter|talent acquisition|"
    r"retail lending|administrative assistant|project manager|artist manager|influencer manager|"
    r"customer support|customer success|account executive|business development)\b",
    flags=re.IGNORECASE,
)


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self.parts)


def clean_html(value: str | None) -> str:
    if not value:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(value)
    return html.unescape(stripper.text())


def make_job_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


async def discover_public_feed_jobs(
    settings: Settings,
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=45.0, headers={"User-Agent": "job-agent-browser/0.1"}) as client:
        batches = await _fetch_all(client)
    profile = _load_profile_lenient(settings)
    preferences = load_preferences(settings)
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in batches:
        if item.url in seen:
            continue
        seen.add(item.url)
        if not _looks_relevant(item):
            continue
        job = match_job(
            RankedJob(
                id=make_job_id(item.url),
                title=item.title,
                company=item.company,
                location=item.location,
                url=item.url,
                description=item.description,
                source=item.source,
            ),
            profile,
            preferences,
        )
        ranked.append(job.to_dict())
    ranked.sort(key=lambda job: int(job.get("match_score", 0)), reverse=True)
    return ranked[:limit]


class FeedItem:
    def __init__(
        self,
        *,
        source: str,
        title: str,
        company: str | None,
        location: str | None,
        url: str,
        description: str,
    ) -> None:
        self.source = source
        self.title = title
        self.company = company
        self.location = location
        self.url = url
        self.description = description


async def _fetch_all(client: httpx.AsyncClient) -> list[FeedItem]:
    results: list[FeedItem] = []
    for query in (
        "ai product",
        "ai engineer",
        "machine learning",
        "llm",
        "software engineer",
        "product engineer",
        "data engineer",
        "automation engineer",
        "junior developer",
        "graduate engineer",
    ):
        results.extend(await _remotive(client, query))
    results.extend(await _remoteok(client))
    results.extend(await _arbeitnow(client))
    return results


async def _remotive(client: httpx.AsyncClient, query: str) -> list[FeedItem]:
    response = await client.get("https://remotive.com/api/remote-jobs", params={"search": query})
    response.raise_for_status()
    payload = response.json()
    items: list[FeedItem] = []
    for job in payload.get("jobs", []):
        items.append(
            FeedItem(
                source="remotive_public_api",
                title=str(job.get("title") or ""),
                company=job.get("company_name"),
                location=job.get("candidate_required_location"),
                url=str(job.get("url") or ""),
                description=clean_html(str(job.get("description") or "")),
            )
        )
    return items


async def _remoteok(client: httpx.AsyncClient) -> list[FeedItem]:
    response = await client.get("https://remoteok.com/api")
    response.raise_for_status()
    payload = response.json()
    items: list[FeedItem] = []
    for job in payload:
        if not isinstance(job, dict) or not job.get("url") or not job.get("position"):
            continue
        tags = ", ".join(str(tag) for tag in job.get("tags") or [])
        items.append(
            FeedItem(
                source="remoteok_public_api",
                title=str(job.get("position") or ""),
                company=job.get("company"),
                location=job.get("location") or "Remote",
                url=str(job.get("url") or ""),
                description=f"{clean_html(str(job.get('description') or ''))}\nTags: {tags}\nSalary: {job.get('salary_min')}-{job.get('salary_max')}",
            )
        )
    return items


async def _arbeitnow(client: httpx.AsyncClient) -> list[FeedItem]:
    items: list[FeedItem] = []
    # Arbeitnow's public API is paginated. The first page alone is too small
    # for a continuously running worker, so sample several pages while keeping
    # the request count bounded.
    for page in range(1, 6):
        response = await client.get("https://www.arbeitnow.com/api/job-board-api", params={"page": page})
        response.raise_for_status()
        payload = response.json()
        for job in payload.get("data", []):
            items.append(
                FeedItem(
                    source="arbeitnow_public_api",
                    title=str(job.get("title") or ""),
                    company=job.get("company_name"),
                    location=job.get("location"),
                    url=str(job.get("url") or ""),
                    description=clean_html(str(job.get("description") or "")),
                )
            )
    return items


def _looks_relevant(item: FeedItem) -> bool:
    title = item.title.strip()
    haystack = f"{item.title}\n{item.company or ''}\n{item.location or ''}\n{item.description}"
    title_lower = title.casefold()
    if NOISE_TITLE_RE.search(title):
        return False
    if SENIORITY_EXCLUDE_RE.search(title) and "founding ai engineer" not in title_lower:
        return False
    if TARGET_TITLE_RE.search(title):
        return True
    if PRODUCT_TITLE_RE.search(title):
        return bool(TECHNICAL_CONTEXT_RE.search(haystack))
    if EARLY_CAREER_RE.search(title):
        return bool(
            TARGET_TITLE_RE.search(haystack)
            or (PRODUCT_TITLE_RE.search(haystack) and TECHNICAL_CONTEXT_RE.search(haystack))
        )
    return bool(
        EARLY_CAREER_RE.search(haystack)
        and (
            TARGET_TITLE_RE.search(haystack)
            or (PRODUCT_TITLE_RE.search(haystack) and TECHNICAL_CONTEXT_RE.search(haystack))
        )
        and not NOISE_TITLE_RE.search(haystack)
    )


def _load_profile_lenient(settings: Settings) -> dict[str, Any]:
    path = settings.profile_dir / "candidate_profile.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
