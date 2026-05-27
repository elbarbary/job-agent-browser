"""Browser Use execution engine constrained to audited, safe operations."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

from browser_use import Browser  # noqa: E402  (telemetry opt-out precedes import)

from .config import Settings
from .policy import RiskClass, assert_action_allowed
from .webabi.recorder import AuditRecorder
from .webabi.schema import ActionCandidate, ActionRecord
from .webabi.verifier import PageSnapshot, verify_snapshot


@dataclass(frozen=True)
class PageObservation:
    url: str
    title: str
    candidates: list[ActionCandidate]


class BrowserSafetyError(ValueError):
    """Raised when navigation escapes the permitted read-only host set."""


class BrowserEngine:
    def __init__(self, settings: Settings, recorder: AuditRecorder) -> None:
        self.settings = settings
        self.recorder = recorder

    def _new_browser(
        self, *, headed: bool, persistent: bool = False, restricted: bool = False
    ) -> Browser:
        kwargs: dict[str, Any] = {
            "is_local": True,
            "headless": not headed,
            "executable_path": "/usr/bin/google-chrome",
            "enable_default_extensions": False,
            "permissions": [],
            "chromium_sandbox": False,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-background-networking",
            ],
        }
        if restricted:
            kwargs["allowed_domains"] = list(self.settings.allowed_hosts)
        if persistent:
            kwargs["user_data_dir"] = str(self.settings.browser_profile_dir)
            kwargs["profile_directory"] = "Default"
        return Browser(**kwargs)

    async def observe_page(self, url: str, *, allowed_url: bool = True) -> PageObservation:
        if allowed_url and not self.settings.is_allowed_url(url):
            raise BrowserSafetyError(f"Read-only browser navigation is not permitted for URL: {url}")
        assert_action_allowed(RiskClass.READ_ONLY)
        browser = self._new_browser(headed=False, restricted=allowed_url)
        try:
            await browser.start()
            page = await browser.new_page()
            await page.goto(url)
            await asyncio.sleep(0.75)
            title = str(_decoded_json(await page.evaluate("() => document.title")))
            raw_candidates = _decoded_json(await page.evaluate(
                """() => Array.from(document.querySelectorAll('a,button,input,select,textarea'))
                    .filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    })
                    .slice(0, 100)
                    .map(el => ({
                        label: (el.innerText || el.getAttribute('aria-label') || el.value || '').trim().slice(0, 120),
                        action_type: el.tagName.toLowerCase(),
                        selector: el.id ? '#' + el.id : el.tagName.toLowerCase()
                    }))"""
            ))
            candidates = [ActionCandidate(**candidate) for candidate in raw_candidates]
            observation = PageObservation(url=str(await page.get_url()), title=title, candidates=candidates)
            screenshot_path = self._save_read_only_screenshot(await page.screenshot(), "observation")
            precheck = verify_snapshot(PageSnapshot(observation.url, title), ["title_contains:Example Domain"]) if url == "https://example.com" else None
            self.recorder.record(
                ActionRecord(
                    run_id=self.recorder.run_id,
                    workflow="page_observation",
                    page_url=observation.url,
                    page_title=observation.title,
                    visible_action_candidates=candidates,
                    selected_action="read_visible_action_candidates",
                    risk_classification=RiskClass.READ_ONLY,
                    preconditions=["read-only navigation permitted"],
                    postconditions=precheck.checks if precheck else ["visible elements extracted"],
                    screenshot_path=str(screenshot_path),
                    result="success" if precheck is None or precheck.passed else "verification_failed",
                    errors=precheck.errors if precheck else [],
                )
            )
            return observation
        finally:
            await browser.stop()

    def _save_read_only_screenshot(self, encoded: str, label: str) -> Path:
        screenshots = self.settings.log_dir / "screenshots"
        screenshots.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = screenshots / f"{self.recorder.run_id}-{label}.png"
        raw = encoded.split(",", 1)[-1]
        path.write_bytes(base64.b64decode(raw))
        path.chmod(0o600)
        return path

    async def smoke_test(self) -> PageObservation:
        return await self.observe_page("https://example.com", allowed_url=False)

    async def manual_login_session(self) -> None:
        self.settings.ensure_directories()
        assert_action_allowed(RiskClass.ACCOUNT_LOGIN, manual=True)
        browser = self._new_browser(headed=True, persistent=True)
        try:
            await browser.start()
            page = await browser.new_page()
            print("A local persistent Chrome window is open.")
            print("Log into desired sites manually. Raw passwords are never requested by this app.")
            input("Press ENTER here when login is complete and the browser may be closed: ")
            self.recorder.record(
                ActionRecord(
                    run_id=self.recorder.run_id,
                    workflow="manual_login_session",
                    page_url=str(await page.get_url()),
                    page_title="Manual browser session",
                    visible_action_candidates=[],
                    selected_action="manual_account_login",
                    risk_classification=RiskClass.ACCOUNT_LOGIN,
                    preconditions=["visible user-controlled browser"],
                    postconditions=["local persistent browser profile retained"],
                    result="manual_session_closed",
                    approved=True,
                )
            )
        finally:
            await browser.stop()

    async def manual_submission_review(self, url: str, job_id: str) -> None:
        if not self.settings.is_allowed_url(url):
            raise BrowserSafetyError(f"Submission review URL is not an approved ATS host: {url}")
        assert_action_allowed(RiskClass.JOB_SUBMIT, confirmed=True)
        browser = self._new_browser(headed=True, persistent=True)
        try:
            await browser.start()
            page = await browser.new_page()
            await page.goto(url)
            print(f"Approved job {job_id} is open in the local persistent browser.")
            print("Review every answer and press Submit yourself only if it is accurate.")
            input("Press ENTER here after you have either submitted manually or cancelled: ")
            self.recorder.record(
                ActionRecord(
                    run_id=self.recorder.run_id,
                    workflow="manual_submission_review",
                    page_url=str(await page.get_url()),
                    page_title=job_id,
                    visible_action_candidates=[],
                    selected_action="open_approved_job_for_manual_submit",
                    risk_classification=RiskClass.JOB_SUBMIT,
                    preconditions=[f"typed confirmation: SUBMIT {job_id}"],
                    postconditions=["submission decision owned by visible human action"],
                    result="manual_review_session_closed",
                    approved=True,
                )
            )
        finally:
            await browser.stop()

    async def extract_job_links(self, search_url: str) -> list[dict[str, str]]:
        observation = await self.observe_page(search_url)
        candidates: list[dict[str, str]] = []
        # The actor observation deliberately avoids clicking results. Job URL
        # extraction is done in a second read-only DOM query below.
        browser = self._new_browser(headed=False, restricted=True)
        try:
            await browser.start()
            page = await browser.new_page()
            await page.goto(search_url)
            await asyncio.sleep(0.75)
            links = _decoded_json(await page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                    label: (a.innerText || '').trim().slice(0, 200),
                    href: a.href
                })).filter(item => item.label && item.href).slice(0, 100)"""
            ))
        finally:
            await browser.stop()
        for link in links:
            target = _decode_search_target(str(link["href"]))
            if self.settings.is_allowed_url(target) and urlparse(target).hostname not in {
                "html.duckduckgo.com",
                "duckduckgo.com",
            }:
                candidates.append({"title": str(link["label"]), "url": target})
        return candidates

    async def read_job_detail(self, url: str) -> dict[str, str]:
        if not self.settings.is_allowed_url(url):
            raise BrowserSafetyError(f"Job detail URL is not an approved public host: {url}")
        assert_action_allowed(RiskClass.READ_ONLY)
        browser = self._new_browser(headed=False, restricted=True)
        try:
            await browser.start()
            page = await browser.new_page()
            await page.goto(url)
            await asyncio.sleep(0.75)
            title = str(_decoded_json(await page.evaluate("() => document.title")))
            text = str(
                _decoded_json(
                    await page.evaluate(
                        "() => (document.body && document.body.innerText || '').slice(0, 20000)"
                    )
                )
            )
            screenshot_label = f"job-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:8]}"
            screenshot_path = self._save_read_only_screenshot(await page.screenshot(), screenshot_label)
            unavailable = "404 error" in title.casefold() or (
                "posting you're looking for might have closed" in text.casefold()
            )
            if unavailable:
                self.recorder.record(
                    ActionRecord(
                        run_id=self.recorder.run_id,
                        workflow="job_detail_read",
                        page_url=str(await page.get_url()),
                        page_title=title,
                        visible_action_candidates=[],
                        selected_action="extract_public_job_text",
                        risk_classification=RiskClass.READ_ONLY,
                        preconditions=["approved public job host"],
                        postconditions=["job posting must remain available"],
                        screenshot_path=str(screenshot_path),
                        result="failed",
                        errors=["The posting appears closed or unavailable."],
                    )
                )
                raise BrowserSafetyError("The approved job posting appears to be closed or unavailable.")
            self.recorder.record(
                ActionRecord(
                    run_id=self.recorder.run_id,
                    workflow="job_detail_read",
                    page_url=str(await page.get_url()),
                    page_title=title,
                    visible_action_candidates=[],
                    selected_action="extract_public_job_text",
                    risk_classification=RiskClass.READ_ONLY,
                    preconditions=["approved public job host"],
                    postconditions=["job text extracted for local ranking"],
                    screenshot_path=str(screenshot_path),
                    result="success",
                )
            )
            return {"title": title, "url": url, "description": text}
        finally:
            await browser.stop()


def _decode_search_target(href: str) -> str:
    parsed = urlparse(href)
    if parsed.hostname in {"duckduckgo.com", "html.duckduckgo.com"}:
        redirect = parse_qs(parsed.query).get("uddg")
        if redirect:
            return unquote(redirect[0])
    return href


def _decoded_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
