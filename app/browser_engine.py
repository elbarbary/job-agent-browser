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
                    errors=[] if verified else ["No post-submit confirmation was detected."],
                    approved=True,
                )
            )
            return {
                "submitted": verified,
                "clicked": True,
                "blocked": False,
                "verified": verified,
                "errors": [] if verified else ["No post-submit confirmation was detected."],
                "post_submit_url": page_url,
                "fills": _audit_fills(fills),
            }
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
    text: (document.body && document.body.innerText || '').slice(0, 5000)
})"""


def _submission_verified(post_state: Any) -> bool:
    if not isinstance(post_state, dict):
        return False
    haystack = " ".join(str(post_state.get(key, "")) for key in ("title", "url", "text")).casefold()
    success_markers = (
        "application submitted",
        "application received",
        "successfully submitted",
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
