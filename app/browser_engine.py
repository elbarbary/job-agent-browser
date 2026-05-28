"""Browser Use execution engine constrained to audited, safe operations."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

from browser_use import Browser  # noqa: E402  (telemetry opt-out precedes import)

from .autopilot import host_allowed, is_known
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

    async def ai_page_context(
        self,
        url: str,
        *,
        allowed_url: bool = True,
        persistent: bool = False,
        headed: bool = False,
    ) -> Path:
        if allowed_url and not self.settings.is_allowed_url(url):
            raise BrowserSafetyError(f"AI page-context navigation is not permitted for URL: {url}")
        assert_action_allowed(RiskClass.READ_ONLY)
        browser = self._new_browser(headed=headed, persistent=persistent, restricted=allowed_url)
        try:
            await browser.start()
            page = await browser.new_page()
            await page.goto(url)
            await asyncio.sleep(1.0)
            context = _decoded_json(await page.evaluate(AI_PAGE_CONTEXT_SCRIPT))
            if not isinstance(context, dict):
                context = {"raw": context}
            page_url = str(await page.get_url())
            title = str(context.get("title") or _decoded_json(await page.evaluate("() => document.title || ''")))
            screenshot_path = self._save_read_only_screenshot(await page.screenshot(), "ai-page-context")
            context.update(
                {
                    "schema": "job_agent.ai_page_context.v1",
                    "run_id": self.recorder.run_id,
                    "captured_at": datetime.now(UTC).isoformat(),
                    "url": page_url,
                    "title": title,
                    "screenshot_path": str(screenshot_path),
                }
            )
            output_dir = self.settings.log_dir / "page_contexts"
            output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = output_dir / f"{self.recorder.run_id}-{hashlib.sha256(page_url.encode('utf-8')).hexdigest()[:8]}.json"
            path.write_text(json.dumps(context, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
            path.chmod(0o600)
            candidates = [
                ActionCandidate(
                    label=str(action.get("label") or ""),
                    action_type=str(action.get("role") or action.get("tag") or "action"),
                    selector=action.get("selector"),
                )
                for action in context.get("actions", [])[:100]
                if isinstance(action, dict)
            ]
            self.recorder.record(
                ActionRecord(
                    run_id=self.recorder.run_id,
                    workflow="ai_page_context",
                    page_url=page_url,
                    page_title=title,
                    visible_action_candidates=candidates,
                    selected_action="extract_ai_browser_context",
                    risk_classification=RiskClass.READ_ONLY,
                    input_values={"url": url, "persistent_session": persistent},
                    preconditions=["read-only navigation permitted"],
                    postconditions=["semantic page context saved", "screenshot saved"],
                    screenshot_path=str(screenshot_path),
                    result="success",
                )
            )
            return path
        finally:
            await browser.stop()

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

    async def gmail_check(self, query: str) -> Path:
        self.settings.ensure_directories()
        assert_action_allowed(RiskClass.READ_ONLY)
        browser = self._new_browser(headed=True, persistent=True)
        output_dir = self.settings.applications_dir / "gmail_checks"
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_path = output_dir / f"{self.recorder.run_id}.txt"
        gmail_url = f"https://mail.google.com/mail/u/0/#search/{quote(query, safe='')}"
        try:
            await browser.start()
            page = await browser.new_page()
            await page.goto(gmail_url)
            print("Gmail is open in the persistent browser profile.")
            print("This command is read-only: it searches and saves visible page text, but never sends email.")
            input("After Gmail finishes loading the search results, press ENTER here to save the visible text: ")
            page_url = str(await page.get_url())
            page_title = str(_decoded_json(await page.evaluate("() => document.title || 'Gmail'")))
            visible_text = str(
                _decoded_json(
                    await page.evaluate(
                        "() => (document.body && document.body.innerText || '').slice(0, 20000)"
                    )
                )
            )
            output_path.write_text(visible_text + "\n", encoding="utf-8")
            output_path.chmod(0o600)
            screenshot_path = self._save_read_only_screenshot(await page.screenshot(), "gmail-check")
            self.recorder.record(
                ActionRecord(
                    run_id=self.recorder.run_id,
                    workflow="gmail_check",
                    page_url=page_url,
                    page_title=page_title,
                    visible_action_candidates=[],
                    selected_action="read_gmail_search_results",
                    risk_classification=RiskClass.READ_ONLY,
                    input_values={"query": query},
                    preconditions=["manual Google login exists in the local browser profile"],
                    postconditions=["visible Gmail search text saved locally", "no email was sent"],
                    screenshot_path=str(screenshot_path),
                    result="success",
                )
            )
            return output_path
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

    async def auto_submit_application(
        self,
        url: str,
        job_id: str,
        answers: dict[str, Any],
        autopilot_config: dict[str, Any],
    ) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not host_allowed(url, autopilot_config):
            raise BrowserSafetyError("Autopilot submission host is not privately allowlisted.")
        assert_action_allowed(RiskClass.JOB_SUBMIT, confirmed=True)
        browser = self._new_browser(
            headed=not bool(autopilot_config.get("headless", True)),
            persistent=True,
            restricted=False,
        )
        errors: list[str] = []
        fills: list[dict[str, Any]] = []
        submit_index: int | None = None
        page_url = url
        page_title = job_id
        screenshot_path: Path | None = None
        try:
            await browser.start()
            page = await browser.new_page()
            await page.goto(url)
            await asyncio.sleep(1.25)
            page_url = str(await page.get_url())
            if _is_arbeitnow_job_page(page_url):
                return await self._auto_submit_arbeitnow_page(
                    page,
                    page_url,
                    job_id,
                    answers,
                    autopilot_config,
                )
            snapshot = _decoded_json(await page.evaluate(AUTOPILOT_SNAPSHOT_SCRIPT))
            page_url = str(await page.get_url())
            page_title = str(snapshot.get("title") or job_id)
            fields = list(snapshot.get("fields") or [])
            buttons = list(snapshot.get("buttons") or [])
            submit_button = _choose_submit_button(buttons)
            submit_index = int(submit_button["index"]) if submit_button else None
            if submit_button and int(submit_button.get("form_index", -1)) >= 0:
                fields = [
                    field
                    for field in fields
                    if int(field.get("form_index", -1)) == int(submit_button["form_index"])
                ]
            fills, errors = _plan_form_fills(fields, answers, autopilot_config)
            if not fields:
                errors.append("No application form fields were found on this page.")
            elif not fills:
                errors.append("No form fields could be safely filled from known answers.")
            if submit_index is None:
                errors.append("No safe submit/apply button was found.")
            if errors:
                screenshot_path = self._save_read_only_screenshot(await page.screenshot(), "autopilot-blocked")
                self.recorder.record(
                    ActionRecord(
                        run_id=self.recorder.run_id,
                        workflow="autopilot_submit",
                        page_url=page_url,
                        page_title=page_title,
                        visible_action_candidates=[],
                        selected_action="blocked_before_submit",
                        risk_classification=RiskClass.JOB_SUBMIT,
                        input_values={"job_id": job_id, "planned_fills": _audit_fills(fills)},
                        preconditions=["private autopilot standing authorization exists"],
                        postconditions=["no submit button was clicked"],
                        screenshot_path=str(screenshot_path),
                        result="blocked",
                        errors=errors,
                        approved=True,
                    )
                )
                return {"submitted": False, "blocked": True, "errors": errors, "fills": _audit_fills(fills)}

            file_fills = [fill for fill in fills if fill.get("kind") == "file"]
            text_fills = [fill for fill in fills if fill.get("kind") != "file"]
            await _upload_file_fields(page, file_fills)
            await page.evaluate(_fill_form_script(text_fills))
            await asyncio.sleep(0.5)
            await page.evaluate(_click_submit_script(submit_index))
            await asyncio.sleep(2.0)
            page_url = str(await page.get_url())
            post_state = _decoded_json(await page.evaluate(AUTOPILOT_POST_SUBMIT_SCRIPT))
            verified = _submission_verified(post_state)
            post_errors = [] if verified else _post_submit_errors(post_state) or ["No post-submit confirmation was detected."]
            screenshot_path = self._save_read_only_screenshot(await page.screenshot(), "autopilot-submit")
            self.recorder.record(
                ActionRecord(
                    run_id=self.recorder.run_id,
                    workflow="autopilot_submit",
                    page_url=page_url,
                    page_title=page_title,
                    visible_action_candidates=[],
                    selected_action="click_final_submit_with_private_autopilot_authorization",
                    risk_classification=RiskClass.JOB_SUBMIT,
                    input_values={"job_id": job_id, "planned_fills": _audit_fills(fills)},
                    preconditions=[
                        "private autopilot standing authorization exists",
                        "all required fields were mapped to known answers",
                        "submit host was privately allowlisted",
                    ],
                    postconditions=[
                        "submit/apply button clicked",
                        "post-submit page checked for confirmation",
                        "audit screenshot saved",
                    ],
                    screenshot_path=str(screenshot_path),
                    result="submit_confirmed" if verified else "submit_clicked_unverified",
                    errors=post_errors,
                    approved=True,
                )
            )
            return {
                "submitted": verified,
                "clicked": True,
                "blocked": False,
                "verified": verified,
                "errors": post_errors,
                "post_submit_url": page_url,
                "fills": _audit_fills(fills),
            }
        finally:
            await browser.stop()

    async def _auto_submit_arbeitnow_page(
        self,
        page: Any,
        url: str,
        job_id: str,
        answers: dict[str, Any],
        autopilot_config: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = _decoded_json(await page.evaluate(ARBEITNOW_SNAPSHOT_SCRIPT))
        page_title = str(snapshot.get("title") or job_id)
        errors: list[str] = []
        fills: list[dict[str, Any]] = []

        if not snapshot.get("form_present"):
            errors.append("Arbeitnow application form was not found.")
        if not snapshot.get("button_present"):
            errors.append("Arbeitnow apply button was not found.")

        first_name, last_name = _split_name(answers.get("name"))
        field_values = {
            "first_name": first_name,
            "last_name": last_name,
            "email": str(answers["email"]) if is_known(answers.get("email")) else None,
        }
        labels = {
            "first_name": "First name",
            "last_name": "Last name",
            "email": "Email address",
        }
        for name, value in field_values.items():
            if value:
                fills.append({"selector": f"#{name}", "label": labels[name], "value": value, "kind": "text"})
            else:
                errors.append(f"Arbeitnow required field has no known answer: {labels[name]}")

        resume_path = Path(str(autopilot_config.get("resume_path") or "")).expanduser()
        if autopilot_config.get("block_file_uploads", True) or not resume_path.exists():
            errors.append("Arbeitnow CV/resume upload is blocked or resume_path does not exist.")
        else:
            fills.append(
                {
                    "selector": "#cv_or_resume",
                    "label": "CV / Resume",
                    "value": str(resume_path.resolve()),
                    "kind": "file",
                }
            )

        if snapshot.get("terms_present"):
            if autopilot_config.get("allow_application_terms_checkbox") is True:
                fills.append(
                    {
                        "selector": "#terms",
                        "label": "Application terms and privacy checkbox",
                        "value": True,
                        "kind": "checkbox",
                    }
                )
            else:
                errors.append(
                    "Arbeitnow requires an application terms/privacy checkbox. "
                    "Set allow_application_terms_checkbox=true only if the user authorizes this."
                )

        if errors:
            screenshot_path = self._save_read_only_screenshot(await page.screenshot(), "arbeitnow-blocked")
            self.recorder.record(
                ActionRecord(
                    run_id=self.recorder.run_id,
                    workflow="autopilot_submit",
                    page_url=url,
                    page_title=page_title,
                    visible_action_candidates=[],
                    selected_action="arbeitnow_blocked_before_submit",
                    risk_classification=RiskClass.JOB_SUBMIT,
                    input_values={"job_id": job_id, "planned_fills": _audit_fills(fills)},
                    preconditions=["private autopilot standing authorization exists", "arbeitnow adapter selected"],
                    postconditions=["no submit button was clicked"],
                    screenshot_path=str(screenshot_path),
                    result="blocked",
                    errors=errors,
                    approved=True,
                )
            )
            return {
                "adapter": "arbeitnow",
                "submitted": False,
                "clicked": False,
                "blocked": True,
                "errors": errors,
                "fills": _audit_fills(fills),
            }

        text_fills = {fill["selector"]: fill["value"] for fill in fills if fill.get("kind") == "text"}
        await page.evaluate(_arbeitnow_fill_script(text_fills))
        for fill in fills:
            if fill.get("kind") == "file":
                await _upload_file_selector(page, str(fill["selector"]), str(fill["value"]))
            elif fill.get("kind") == "checkbox":
                await page.evaluate(_checkbox_script(str(fill["selector"]), checked=True))
        await asyncio.sleep(0.5)
        await page.evaluate("() => document.querySelector('#button_send_application').click()")
        post_state = await _wait_for_arbeitnow_result(page)
        verified = _submission_verified(post_state)
        post_errors = [] if verified else _post_submit_errors(post_state) or ["No post-submit confirmation was detected."]
        screenshot_path = self._save_read_only_screenshot(await page.screenshot(), "arbeitnow-submit")
        page_url = str(await page.get_url())
        self.recorder.record(
            ActionRecord(
                run_id=self.recorder.run_id,
                workflow="autopilot_submit",
                page_url=page_url,
                page_title=page_title,
                visible_action_candidates=[],
                selected_action="arbeitnow_submit_application_form",
                risk_classification=RiskClass.JOB_SUBMIT,
                input_values={
                    "job_id": job_id,
                    "adapter": "arbeitnow",
                    "planned_fills": _audit_fills(fills),
                    "post_submit_state": _audit_post_state(post_state),
                },
                preconditions=[
                    "private autopilot standing authorization exists",
                    "arbeitnow form adapter selected",
                    "required fields mapped to known answers",
                ],
                postconditions=[
                    "arbeitnow apply button clicked",
                    "success div and visible field errors checked",
                    "audit screenshot saved",
                ],
                screenshot_path=str(screenshot_path),
                result="submit_confirmed" if verified else "submit_clicked_unverified",
                errors=post_errors,
                approved=True,
            )
        )
        return {
            "adapter": "arbeitnow",
            "submitted": verified,
            "clicked": True,
            "blocked": False,
            "verified": verified,
            "errors": post_errors,
            "post_submit_url": page_url,
            "post_submit_state": _audit_post_state(post_state),
            "fills": _audit_fills(fills),
        }

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


def _is_arbeitnow_job_page(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.arbeitnow.com"
        and path.startswith("/jobs/companies/")
        and not path.endswith("/apply")
    )


def _decoded_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


AI_PAGE_CONTEXT_SCRIPT = """() => {
    const MAX_ITEMS = 160;
    const MAX_TEXT = 240;
    const visible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
    };
    const clean = (value, limit = MAX_TEXT) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
    const selectorFor = (el) => {
        if (!el || !el.tagName) return null;
        if (el.id) return '#' + CSS.escape(el.id);
        const attr = ['data-testid', 'data-test', 'aria-label', 'name'].find(name => el.getAttribute(name));
        if (attr) return `${el.tagName.toLowerCase()}[${attr}="${CSS.escape(el.getAttribute(attr))}"]`;
        const parent = el.parentElement;
        if (!parent) return el.tagName.toLowerCase();
        const siblings = Array.from(parent.children).filter(child => child.tagName === el.tagName);
        if (siblings.length === 1) return el.tagName.toLowerCase();
        return `${el.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(el) + 1})`;
    };
    const textFor = (el) => {
        const parts = [];
        if (el.id) {
            document.querySelectorAll(`label[for="${CSS.escape(el.id)}"]`).forEach(label => parts.push(label.innerText || ''));
        }
        const label = el.closest('label');
        if (label) parts.push(label.innerText || '');
        parts.push(el.getAttribute('aria-label') || '');
        parts.push(el.getAttribute('placeholder') || '');
        parts.push(el.name || '');
        parts.push(el.id || '');
        parts.push(el.innerText || el.value || '');
        return clean(parts.join(' '));
    };
    const riskHint = (label, tag, type, href) => {
        const text = `${label} ${tag} ${type} ${href || ''}`.toLowerCase();
        if (/pay|purchase|checkout|billing|credit card/.test(text)) return 'payment';
        if (/delete|remove|withdraw|cancel account|terminate/.test(text)) return 'destructive';
        if (/send email|send message|reply/.test(text)) return 'email_send';
        if (/submit|apply|send application|complete application/.test(text)) return 'job_submit';
        if (/login|sign in|log in|password/.test(text)) return 'account_login';
        if (/input|textarea|select|upload|checkbox|radio/.test(`${tag} ${type}`)) return 'form_fill';
        return 'read_only';
    };
    const valueState = (el) => {
        const type = (el.type || '').toLowerCase();
        if (['password', 'hidden', 'file'].includes(type)) return el.value ? '[REDACTED]' : '';
        if (type === 'checkbox' || type === 'radio') return el.checked ? 'checked' : 'unchecked';
        return el.value ? '[NON_EMPTY]' : '';
    };
    const forms = Array.from(document.querySelectorAll('form')).map((form, index) => ({
        index,
        selector: selectorFor(form),
        method: clean(form.method || 'get', 20),
        action: form.action || '',
        label: clean(form.getAttribute('aria-label') || form.querySelector('h1,h2,h3,legend')?.innerText || form.innerText, 160),
        visible: visible(form)
    })).filter(form => form.visible).slice(0, 30);
    const formElements = Array.from(document.querySelectorAll('input, textarea, select'))
        .filter(el => visible(el) && !el.disabled)
        .filter(el => !['hidden', 'submit', 'button', 'reset', 'image'].includes((el.type || '').toLowerCase()));
    const fields = formElements.map((el, index) => {
        const form = el.closest('form');
        const formIndex = form ? Array.from(document.querySelectorAll('form')).indexOf(form) : -1;
        return {
            index,
            selector: selectorFor(el),
            tag: el.tagName.toLowerCase(),
            type: (el.type || '').toLowerCase(),
            name: el.name || '',
            id: el.id || '',
            label: textFor(el),
            required: Boolean(el.required || el.getAttribute('aria-required') === 'true'),
            autocomplete: el.getAttribute('autocomplete') || '',
            value_state: valueState(el),
            form_index: formIndex,
            options: el.tagName.toLowerCase() === 'select'
                ? Array.from(el.options || []).slice(0, 40).map(option => clean(option.innerText || option.value, 120))
                : []
        };
    }).slice(0, MAX_ITEMS);
    const actionElements = Array.from(document.querySelectorAll('a[href], button, input[type="submit"], input[type="button"], [role="button"], summary'))
        .filter(el => visible(el) && !el.disabled);
    const actions = actionElements.map((el, index) => {
        const tag = el.tagName.toLowerCase();
        const type = (el.type || '').toLowerCase();
        const label = textFor(el);
        const href = tag === 'a' ? el.href : '';
        const form = el.closest('form');
        const formIndex = form ? Array.from(document.querySelectorAll('form')).indexOf(form) : -1;
        return {
            index,
            selector: selectorFor(el),
            tag,
            role: el.getAttribute('role') || (tag === 'a' ? 'link' : 'button'),
            type,
            label,
            href,
            form_index: formIndex,
            risk_hint: riskHint(label, tag, type, href)
        };
    }).slice(0, MAX_ITEMS);
    const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4'))
        .filter(visible)
        .map(el => ({level: el.tagName.toLowerCase(), text: clean(el.innerText, 200), selector: selectorFor(el)}))
        .slice(0, 80);
    const textBlocks = Array.from(document.querySelectorAll('main p, main li, article p, article li, section p, section li, label, legend, [role="alert"]'))
        .filter(visible)
        .map(el => clean(el.innerText, 220))
        .filter(Boolean)
        .slice(0, MAX_ITEMS);
    const visibleErrors = Array.from(document.querySelectorAll('[id^="error-"], [role="alert"], .error, .errors, .invalid-feedback, .text-red-500'))
        .filter(el => visible(el) && clean(el.innerText))
        .map(el => ({selector: selectorFor(el), text: clean(el.innerText, 240)}))
        .slice(0, 60);
    const meta = Array.from(document.querySelectorAll('meta[name], meta[property]'))
        .map(el => ({name: el.getAttribute('name') || el.getAttribute('property'), content: clean(el.getAttribute('content'), 240)}))
        .filter(item => ['description', 'og:title', 'og:description'].includes(item.name))
        .slice(0, 20);
    return {
        url: location.href,
        title: document.title || '',
        language: document.documentElement.lang || '',
        viewport: {width: window.innerWidth, height: window.innerHeight},
        meta,
        headings,
        forms,
        fields,
        actions,
        visible_errors: visibleErrors,
        text_blocks: textBlocks,
        visible_text_excerpt: clean(document.body?.innerText || '', 4000)
    };
}"""


ARBEITNOW_SNAPSHOT_SCRIPT = """() => {
    const form = document.querySelector('#form_job_application');
    const button = document.querySelector('#button_send_application');
    const terms = document.querySelector('#terms');
    const success = document.querySelector('#div_success_message');
    const visible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    return {
        title: document.title || '',
        url: location.href,
        form_present: Boolean(form),
        form_visible: visible(form),
        button_present: Boolean(button),
        terms_present: Boolean(terms),
        terms_label: (document.querySelector('label[for="terms"]')?.innerText || '').replace(/\\s+/g, ' ').trim(),
        success_present: Boolean(success),
        success_visible: visible(success)
    };
}"""


AUTOPILOT_SNAPSHOT_SCRIPT = """() => {
    const visible = (el) => {
        const r = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    const textFor = (el) => {
        const parts = [];
        if (el.id) {
            document.querySelectorAll(`label[for="${CSS.escape(el.id)}"]`).forEach(label => parts.push(label.innerText || ''));
        }
        const label = el.closest('label');
        if (label) parts.push(label.innerText || '');
        parts.push(el.getAttribute('aria-label') || '');
        parts.push(el.getAttribute('placeholder') || '');
        parts.push(el.name || '');
        parts.push(el.id || '');
        return parts.join(' ').replace(/\\s+/g, ' ').trim();
    };
    const forms = Array.from(document.querySelectorAll('form'));
    const formIndexFor = (el) => forms.indexOf(el.closest('form'));
    const fieldElements = Array.from(document.querySelectorAll('input, textarea, select'))
        .filter(el => visible(el) && !el.disabled)
        .filter(el => !['hidden', 'submit', 'button', 'reset', 'image'].includes((el.type || '').toLowerCase()));
    fieldElements.forEach((el, index) => el.setAttribute('data-autopilot-field-index', String(index)));
    const fields = fieldElements.map((el, index) => ({
            index,
            tag: el.tagName.toLowerCase(),
            type: (el.type || '').toLowerCase(),
            name: el.name || '',
            id: el.id || '',
            label: textFor(el).slice(0, 240),
            required: Boolean(el.required || el.getAttribute('aria-required') === 'true'),
            form_index: formIndexFor(el)
        }));
    const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"]'))
        .filter(el => visible(el) && !el.disabled)
        .map((el, index) => ({
            index,
            form_index: formIndexFor(el),
            text: ((el.innerText || el.value || el.getAttribute('aria-label') || '') + '').replace(/\\s+/g, ' ').trim().slice(0, 160)
        }));
    return {title: document.title || '', url: location.href, fields, buttons};
}"""


def _field_text(field: dict[str, Any]) -> str:
    return " ".join(str(field.get(key, "")) for key in ("label", "name", "id", "type")).casefold()


def _split_name(name: Any) -> tuple[str | None, str | None]:
    if not is_known(name):
        return None, None
    parts = str(name).strip().split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _link_for(answers: dict[str, Any], needle: str) -> str | None:
    for link in answers.get("links") or []:
        value = str(link)
        if needle in value.casefold():
            return value
    return None


def _answer_for_field(field: dict[str, Any], answers: dict[str, Any]) -> str | None:
    text = _field_text(field)
    first_name, last_name = _split_name(answers.get("name"))
    if "first" in text and first_name:
        return first_name
    if any(word in text for word in ("last", "surname", "family")) and last_name:
        return last_name
    if "name" in text and is_known(answers.get("name")):
        return str(answers["name"])
    if "email" in text and is_known(answers.get("email")):
        return str(answers["email"])
    if any(word in text for word in ("phone", "mobile", "tel")) and is_known(answers.get("phone")):
        return str(answers["phone"])
    if any(word in text for word in ("location", "city", "address", "country")) and is_known(answers.get("location")):
        return str(answers["location"])
    if "linkedin" in text:
        return _link_for(answers, "linkedin")
    if "github" in text:
        return _link_for(answers, "github")
    if any(word in text for word in ("portfolio", "website", "url", "link")):
        links = answers.get("links") or []
        return str(links[0]) if links else None
    if any(word in text for word in ("authorization", "authorisation", "visa", "sponsor", "work permit")):
        return str(answers["work_authorization"]) if is_known(answers.get("work_authorization")) else None
    if any(word in text for word in ("availability", "start date", "notice period")):
        return str(answers["availability"]) if is_known(answers.get("availability")) else None
    if any(word in text for word in ("salary", "compensation", "pay expectation")):
        return str(answers["salary_expectation"]) if is_known(answers.get("salary_expectation")) else None
    return None


def _plan_form_fills(
    fields: list[dict[str, Any]],
    answers: dict[str, Any],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    fills: list[dict[str, Any]] = []
    errors: list[str] = []
    for field in fields:
        field_type = str(field.get("type", "")).casefold()
        tag = str(field.get("tag", "")).casefold()
        label = str(field.get("label") or field.get("name") or field.get("id") or f"field {field.get('index')}")
        required = bool(field.get("required"))
        if field_type == "file":
            resume_path = Path(str(config.get("resume_path") or "")).expanduser()
            if config.get("block_file_uploads", True) or not resume_path.exists():
                errors.append(f"File upload field blocked: {label}")
            else:
                fills.append(
                    {
                        "index": int(field["index"]),
                        "label": label,
                        "value": str(resume_path.resolve()),
                        "kind": "file",
                    }
                )
            continue
        if field_type in {"checkbox", "radio"} and required and config.get("block_required_checkboxes", True):
            errors.append(f"Required checkbox/radio blocked: {label}")
            continue
        if tag == "select" and required:
            errors.append(f"Required select field needs manual review: {label}")
            continue
        answer = _answer_for_field(field, answers)
        if answer:
            fills.append({"index": int(field["index"]), "label": label, "value": answer, "kind": "text"})
        elif required and config.get("block_unknown_required_fields", True):
            errors.append(f"Required field has no known answer: {label}")
    return fills, errors


def _choose_submit_button(buttons: list[dict[str, Any]]) -> dict[str, Any] | None:
    blocked = ("delete", "withdraw", "remove", "pay", "purchase", "checkout")
    candidates: list[tuple[int, dict[str, Any]]] = []
    for button in buttons:
        text = str(button.get("text", "")).casefold()
        if not text or any(word in text for word in blocked):
            continue
        form_index = int(button.get("form_index", -1))
        if "submit" in text:
            priority = 0 if form_index >= 0 else 2
        elif "apply" in text:
            priority = 1 if form_index >= 0 else 4
        else:
            continue
        candidates.append((priority, button))
    if candidates:
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]
    return None


AUTOPILOT_POST_SUBMIT_SCRIPT = """() => ({
    title: document.title || '',
    url: location.href,
    text: (document.body && document.body.innerText || '').slice(0, 5000),
    visible_errors: Array.from(document.querySelectorAll('[id^="error-"]'))
        .filter(el => {
            const r = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return (el.innerText || '').trim() && r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
        })
        .map(el => ({id: el.id || '', text: (el.innerText || '').replace(/\\s+/g, ' ').trim()}))
})"""


ARBEITNOW_POST_SUBMIT_SCRIPT = """() => {
    const visible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    const form = document.querySelector('#form_job_application');
    const success = document.querySelector('#div_success_message');
    const visibleErrors = Array.from(document.querySelectorAll('[id^="error-"]'))
        .filter(el => (el.innerText || '').trim() && visible(el))
        .map(el => ({id: el.id || '', text: (el.innerText || '').replace(/\\s+/g, ' ').trim()}));
    return {
        title: document.title || '',
        url: location.href,
        text: (document.body && document.body.innerText || '').slice(0, 5000),
        form_visible: visible(form),
        success_visible: visible(success),
        success_text: success ? (success.innerText || '').replace(/\\s+/g, ' ').trim() : '',
        visible_errors: visibleErrors
    };
}"""


async def _wait_for_arbeitnow_result(page: Any) -> Any:
    post_state: Any = {}
    for _ in range(20):
        post_state = _decoded_json(await page.evaluate(ARBEITNOW_POST_SUBMIT_SCRIPT))
        if _submission_verified(post_state) or _post_submit_errors(post_state):
            return post_state
        await asyncio.sleep(0.5)
    return post_state


def _submission_verified(post_state: Any) -> bool:
    if not isinstance(post_state, dict):
        return False
    if post_state.get("success_visible") is True:
        return True
    haystack = " ".join(str(post_state.get(key, "")) for key in ("title", "url", "text")).casefold()
    success_markers = (
        "application submitted",
        "application received",
        "successfully submitted",
        "job application has been sent successfully",
        "we received your application",
        "we have received your application",
        "thanks for applying",
        "thank you for applying",
        "thank you for your application",
        "your application has been received",
        "your application was sent",
        "submission received",
    )
    return any(marker in haystack for marker in success_markers)


def _post_submit_errors(post_state: Any) -> list[str]:
    if not isinstance(post_state, dict):
        return []
    errors: list[str] = []
    for item in post_state.get("visible_errors") or []:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            if text:
                field = str(item.get("id") or "form_error").removeprefix("error-")
                errors.append(f"{field}: {text}")
        elif item:
            errors.append(str(item))
    return errors


def _audit_post_state(post_state: Any) -> dict[str, Any]:
    if not isinstance(post_state, dict):
        return {}
    return {
        "url": post_state.get("url"),
        "success_visible": post_state.get("success_visible"),
        "success_text": post_state.get("success_text"),
        "form_visible": post_state.get("form_visible"),
        "visible_errors": post_state.get("visible_errors") or [],
    }


def _fill_form_script(fills: list[dict[str, Any]]) -> str:
    payload = json.dumps(fills)
    return f"""() => {{
        const fills = {payload};
        const visible = (el) => {{
            const r = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
        }};
        const fields = Array.from(document.querySelectorAll('input, textarea, select'))
            .filter(el => visible(el) && !el.disabled)
            .filter(el => !['hidden', 'submit', 'button', 'reset', 'image'].includes((el.type || '').toLowerCase()));
        for (const fill of fills) {{
            const el = fields[fill.index];
            if (!el) continue;
            el.focus();
            el.value = fill.value;
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
        }}
        return fills.length;
    }}"""


def _arbeitnow_fill_script(values: dict[str, Any]) -> str:
    payload = json.dumps(values)
    return f"""() => {{
        const values = {payload};
        for (const [selector, value] of Object.entries(values)) {{
            const el = document.querySelector(selector);
            if (!el) throw new Error(`Missing Arbeitnow field: ${{selector}}`);
            el.focus();
            el.value = value;
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
        }}
        return Object.keys(values).length;
    }}"""


def _checkbox_script(selector: str, *, checked: bool) -> str:
    payload = json.dumps({"selector": selector, "checked": checked})
    return f"""() => {{
        const payload = {payload};
        const el = document.querySelector(payload.selector);
        if (!el) throw new Error(`Missing checkbox: ${{payload.selector}}`);
        el.checked = Boolean(payload.checked);
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
        return el.checked;
    }}"""


async def _upload_file_fields(page: Any, fills: list[dict[str, Any]]) -> None:
    if not fills:
        return
    session_id = await page.session_id
    document = await page._client.send.DOM.getDocument({"depth": -1, "pierce": True}, session_id=session_id)
    root_id = document["root"]["nodeId"]
    for fill in fills:
        selector = f'[data-autopilot-field-index="{int(fill["index"])}"]'
        node = await page._client.send.DOM.querySelector(
            {"nodeId": root_id, "selector": selector},
            session_id=session_id,
        )
        node_id = node.get("nodeId")
        if not node_id:
            raise BrowserSafetyError(f"Could not find file input for upload: {fill.get('label', '')}")
        await page._client.send.DOM.setFileInputFiles(
            {"nodeId": node_id, "files": [str(fill["value"])]},
            session_id=session_id,
        )


async def _upload_file_selector(page: Any, selector: str, file_path: str) -> None:
    session_id = await page.session_id
    document = await page._client.send.DOM.getDocument({"depth": -1, "pierce": True}, session_id=session_id)
    root_id = document["root"]["nodeId"]
    node = await page._client.send.DOM.querySelector(
        {"nodeId": root_id, "selector": selector},
        session_id=session_id,
    )
    node_id = node.get("nodeId")
    if not node_id:
        raise BrowserSafetyError(f"Could not find file input for upload: {selector}")
    await page._client.send.DOM.setFileInputFiles(
        {"nodeId": node_id, "files": [file_path]},
        session_id=session_id,
    )


def _audit_fills(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audited: list[dict[str, Any]] = []
    for fill in fills:
        item = fill.copy()
        if item.get("kind") == "file":
            item["value"] = "[LOCAL_FILE]"
        audited.append(item)
    return audited


def _click_submit_script(index: int) -> str:
    return f"""() => {{
        const visible = (el) => {{
            const r = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
        }};
        const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"]'))
            .filter(el => visible(el) && !el.disabled);
        const button = buttons[{index}];
        if (!button) throw new Error('submit button disappeared before click');
        button.click();
        return true;
    }}"""
