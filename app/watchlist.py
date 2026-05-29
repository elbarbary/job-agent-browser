"""Private watchlist configuration for the background worker."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .source_catalog import source_names


DEFAULT_WATCHLIST: dict[str, Any] = {
    "version": 1,
    "generated_at": None,
    "interval_minutes": 180,
    "auto_draft_top_n": 5,
    "autopilot_scan_top_n": 25,
    "min_auto_draft_score": 45,
    "with_llm_advisory": True,
    "discovery_mode": "alternate",
    "public_feeds_enabled": True,
    "public_feed_limit": 40,
    "source_urls_per_cycle": 10,
    "queries_enabled": False,
    "max_results_per_query": 5,
    "enabled_source_names": source_names(),
    "queries": [
        {"query": "software engineer", "location": "remote"},
        {"query": "machine learning engineer", "location": "remote"},
        {"query": "product engineer", "location": "remote"},
        {"query": "data engineer", "location": "remote"},
    ],
    "source_urls": [],
    "notes": [
        "Discovery mode can be online, source_urls, both, or alternate.",
        "Online discovery uses public feeds and optional local SearXNG searches across the enabled source catalog.",
        "Source URL discovery follows job URLs you add manually.",
        "The worker drafts locally only for jobs at or above min_auto_draft_score. Autopilot submissions require private autopilot.json authorization.",
    ],
}


def watchlist_path(settings: Settings) -> Path:
    return settings.profile_dir / "watchlist.json"


def write_default_watchlist(settings: Settings) -> Path:
    settings.ensure_directories()
    payload = DEFAULT_WATCHLIST.copy()
    payload["generated_at"] = datetime.now(UTC).isoformat()
    path = watchlist_path(settings)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def load_watchlist(settings: Settings) -> dict[str, Any]:
    path = watchlist_path(settings)
    if not path.exists():
        write_default_watchlist(settings)
    watchlist = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for key, value in DEFAULT_WATCHLIST.items():
        if key not in watchlist:
            watchlist[key] = value
            changed = True
    if changed:
        path.write_text(json.dumps(watchlist, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        path.chmod(0o600)
    return watchlist
