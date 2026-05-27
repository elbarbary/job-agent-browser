"""Draft-oriented job application workflow with human submission ownership."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .cv_store import load_profile
from .job_profile import RankedJob, match_job
from .preferences import load_preferences
from .policy import RiskClass, assert_action_allowed
from .webabi.recorder import AuditRecorder
from .webabi.schema import ActionRecord


class ApplicationError(ValueError):
    """Raised for missing jobs or unsafe application state."""


def _write_private_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


class ApplicationRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_directories()
        self.jobs_path = settings.applications_dir / "jobs.json"

    def load_jobs(self) -> list[dict[str, Any]]:
        if not self.jobs_path.exists():
            return []
        return json.loads(self.jobs_path.read_text(encoding="utf-8"))

    def save_jobs(self, jobs: list[dict[str, Any]]) -> Path:
        return _write_private_json(self.jobs_path, jobs)

    def find_job(self, job_id: str) -> dict[str, Any]:
        for job in self.load_jobs():
            if str(job.get("id")) == job_id:
                return job
        raise ApplicationError(f"Unknown job id: {job_id}")

    def submission_path(self, job_id: str) -> Path:
        return self.settings.applications_dir / "submissions" / f"{job_id}.json"

    def has_submission(self, job_id: str) -> bool:
        return self.submission_path(job_id).exists()

    def record_submission(self, job_id: str, payload: dict[str, Any]) -> Path:
        return _write_private_json(self.submission_path(job_id), payload)


class ApplicationWorkflow:
    def __init__(self, settings: Settings, recorder: AuditRecorder) -> None:
        self.settings = settings
        self.recorder = recorder
        self.repository = ApplicationRepository(settings)

    def draft(self, job_id: str, llm_advisory: str | None = None) -> Path:
        job_data = self.repository.find_job(job_id)
        profile = load_profile(self.settings)
        preferences = load_preferences(self.settings)
        job = match_job(RankedJob(**_ranked_job_fields(job_data)), profile, preferences)
        answers = job.suggested_application_answers
        facts = preferences.get("candidate_user_confirmed_facts") or {}
        _apply_user_confirmed_facts(answers, facts)
        payload = {
            "job": job.to_dict(),
            "status": "draft_only_no_submission",
            "generated_at": datetime.now(UTC).isoformat(),
            "answers": answers,
            "required_user_answers": job.risks_uncertainties,
            "safety_note": "No site form was submitted. Unknown answers require the user.",
            "llm_advisory_notes": llm_advisory,
            "llm_advisory_policy": (
                "Advisory only; it must not supply candidate facts or trigger submission."
                if llm_advisory
                else "Not requested."
            ),
        }
        output = _write_private_json(self.settings.applications_dir / "drafts" / f"{job_id}.json", payload)
        self.recorder.record(
            ActionRecord(
                run_id=self.recorder.run_id,
                workflow="apply_dry_run",
                page_url=job.url,
                page_title=job.title,
                visible_action_candidates=[],
                selected_action="draft_form_answers",
                risk_classification=RiskClass.FORM_FILL,
                input_values={"answers": answers},
                preconditions=["candidate profile exists", "job was saved in the application repository"],
                postconditions=["draft saved locally", "no submit action executed"],
                result="draft_saved_no_submission",
            )
        )
        return output

    def approve_for_manual_submission(self, job_id: str) -> Path:
        job_data = self.repository.find_job(job_id)
        assert_action_allowed(RiskClass.JOB_SUBMIT, confirmed=True)
        approval = {
            "job_id": job_id,
            "job_url": job_data.get("url"),
            "approved_at": datetime.now(UTC).isoformat(),
            "execution": "manual_submission_only",
            "note": "Approval recorded. Use the visible logged-in browser to review and submit manually.",
        }
        path = _write_private_json(
            self.settings.applications_dir / "approvals" / f"{job_id}.json", approval
        )
        self.recorder.record(
            ActionRecord(
                run_id=self.recorder.run_id,
                workflow="apply_confirmation",
                page_url=str(job_data.get("url", "")),
                page_title=str(job_data.get("title", job_id)),
                visible_action_candidates=[],
                selected_action="approve_job_submit_manual_only",
                risk_classification=RiskClass.JOB_SUBMIT,
                preconditions=[f"typed confirmation: SUBMIT {job_id}"],
                postconditions=["approval file saved", "no automated click executed"],
                result="approved_manual_submission_required",
                approved=True,
            )
        )
        return path

    def record_autopilot_submission(self, job_id: str, result: dict[str, Any]) -> Path:
        job_data = self.repository.find_job(job_id)
        payload = {
            "job_id": job_id,
            "job_url": job_data.get("url"),
            "submitted_at": datetime.now(UTC).isoformat(),
            "execution": "autopilot_submit_clicked",
            "result": result,
            "note": "Autopilot clicked the final submit/apply button after private standing authorization.",
        }
        return self.repository.record_submission(job_id, payload)


def _ranked_job_fields(job: dict[str, Any]) -> dict[str, Any]:
    names = {"id", "title", "company", "location", "url", "description", "source"}
    return {name: job.get(name, "") for name in names}


def _known_fact(value: Any) -> str | None:
    if value in (None, "", "needs_user_answer"):
        return None
    return str(value)


def _apply_user_confirmed_facts(answers: dict[str, Any], facts: dict[str, Any]) -> None:
    work_authorization = _known_fact(facts.get("work_authorization_summary"))
    if work_authorization:
        answers["work_authorization"] = work_authorization
    else:
        legacy_home_country = _legacy_sponsorship_home_country(facts)
        needs_sponsorship = (
            facts.get("needs_work_sponsorship_outside_home_country") is True
            or legacy_home_country is not None
        )
        nationality = _known_fact(facts.get("nationality"))
        home_country = _known_fact(facts.get("home_country")) or legacy_home_country
        if needs_sponsorship and nationality and home_country:
            answers["work_authorization"] = (
                f"{nationality} citizen; requires employer visa/work authorization sponsorship "
                f"for roles outside {home_country}."
            )

    availability = _known_fact(facts.get("availability"))
    if availability:
        answers["availability"] = availability
    answers["salary_expectation"] = _known_fact(facts.get("salary_target")) or "needs_user_answer"


def _legacy_sponsorship_home_country(facts: dict[str, Any]) -> str | None:
    prefix = "needs_work_sponsorship_outside_"
    for key, value in facts.items():
        if key == "needs_work_sponsorship_outside_home_country":
            continue
        if value is True and isinstance(key, str) and key.startswith(prefix):
            return key.removeprefix(prefix).replace("_", " ").title()
    return None
