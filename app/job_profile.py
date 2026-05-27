"""Conservative CV-to-job matching and suggested answer generation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


TOKEN_RE = re.compile(r"[a-z][a-z0-9+#.-]{1,}", re.IGNORECASE)
STOPWORDS = {
    "and",
    "the",
    "with",
    "for",
    "from",
    "that",
    "will",
    "you",
    "our",
    "are",
    "this",
    "have",
    "experience",
    "skills",
}
REQUIREMENT_MARKERS = ("required", "must", "minimum", "qualification", "years")
MAX_PREFERENCE_BOOST = 30


@dataclass
class RankedJob:
    id: str
    title: str
    company: str | None
    location: str | None
    url: str
    description: str = ""
    source: str = ""
    match_score: int = 0
    why_it_matches: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    risks_uncertainties: list[str] = field(default_factory=list)
    suggested_application_answers: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _tokens(values: list[str] | str | None) -> set[str]:
    if not values:
        return set()
    text = "\n".join(values) if isinstance(values, list) else values
    return {token.casefold() for token in TOKEN_RE.findall(text) if token.casefold() not in STOPWORDS}


def match_job(job: RankedJob, profile: dict[str, Any], preferences: dict[str, Any] | None = None) -> RankedJob:
    profile_material: list[str] = []
    for key in ("skills", "projects", "work_experience", "education", "certifications"):
        profile_material.extend(profile.get(key) or [])
    profile_tokens = _tokens(profile_material)
    job_tokens = _tokens(f"{job.title}\n{job.description}")
    overlap = sorted(profile_tokens & job_tokens)
    denominator = max(1, min(len(job_tokens), 30))
    cv_signal_score = round(100 * len(overlap) / denominator)
    score = cv_signal_score
    job.why_it_matches = [f"CV mentions: {token}" for token in overlap[:12]]
    preference_matches = _preference_matches(job, preferences or {})
    if preference_matches:
        score += min(MAX_PREFERENCE_BOOST, sum(points for _, points in preference_matches))
        job.why_it_matches.extend(f"Preference match: {name}" for name, _ in preference_matches)
    if cv_signal_score == 0:
        score = min(score, 35)
    elif cv_signal_score < 20:
        score = min(score, 65)
    job.match_score = min(100, score)
    requirement_lines = [
        line.strip()
        for line in job.description.splitlines()
        if any(marker in line.casefold() for marker in REQUIREMENT_MARKERS)
    ]
    job.missing_requirements = [
        line for line in requirement_lines if not (_tokens(line) & profile_tokens)
    ][:10]
    uncertainties = list(profile.get("constraints_questions_needing_user_confirmation") or [])
    if cv_signal_score == 0:
        uncertainties.append("low_cv_match: no clear CV keyword overlap was found.")
    if not job.description.strip():
        uncertainties.append("needs_user_answer: job description was not captured for detailed matching.")
    job.risks_uncertainties = uncertainties
    job.suggested_application_answers = suggested_answers(profile)
    return job


def _preference_matches(job: RankedJob, preferences: dict[str, Any]) -> list[tuple[str, int]]:
    haystack = f"{job.title}\n{job.company or ''}\n{job.location or ''}\n{job.description}".casefold()
    matches: list[tuple[str, int]] = []
    for keyword in preferences.get("preferred_keywords") or []:
        if str(keyword).casefold() in haystack:
            matches.append((str(keyword), 2))
    for priority in preferences.get("priority_order") or []:
        locations = [str(item).casefold() for item in priority.get("locations", [])]
        if locations and any(location in haystack for location in locations):
            matches.append((str(priority.get("name", "location priority")), int(priority.get("weight", 0))))
    if "sponsor" in haystack or "visa" in haystack or "relocation" in haystack:
        matches.append(("possible sponsorship/relocation signal", 8))
    return matches[:12]


def suggested_answers(profile: dict[str, Any]) -> dict[str, Any]:
    known = {
        "name": profile.get("name"),
        "email": profile.get("email"),
        "phone": profile.get("phone"),
        "location": profile.get("location"),
        "links": profile.get("links") or [],
    }
    answers: dict[str, Any] = {
        key: value if value else "needs_user_answer" for key, value in known.items()
    }
    answers.update(
        {
            "work_authorization": "needs_user_answer",
            "salary_expectation": "needs_user_answer",
            "relocation": "needs_user_answer",
            "demographic_questions": "needs_user_answer",
        }
    )
    return answers
