"""CV extraction and conservative structured-profile creation."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docx import Document
from pypdf import PdfReader

from .config import Settings


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d[\d\s().-]{6,}\d")
URL_RE = re.compile(r"(?:https?://|www\.|linkedin\.com/|github\.com/|/linkedin/|/github/)\S+", re.IGNORECASE)
LOCATION_LABEL_RE = re.compile(r"\b(?:location|address|based in)\b\s*[:|-]\s*(.+)", re.IGNORECASE)
SECTION_ALIASES = {
    "education": ("education", "academic background"),
    "work_experience": ("experience", "work experience", "employment", "professional experience"),
    "projects": ("projects", "selected projects"),
    "skills": ("skills", "technical skills", "technologies"),
    "languages": ("languages",),
    "certifications": ("certifications", "certificates", "licenses"),
}
UNKNOWN_FACTS = (
    ("work authorization", ("authorized to work", "work authorization", "visa", "citizen")),
    ("desired salary or salary history", ("salary", "compensation")),
    ("availability/start date", ("available", "start date")),
    ("relocation willingness", ("relocat",)),
)


@dataclass(frozen=True)
class IngestionResult:
    source_path: Path
    extracted_text_path: Path
    profile_path: Path
    profile: dict[str, Any]


class CVError(ValueError):
    """Raised for unsupported or unreadable CV inputs."""


def _extract_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx(path: Path) -> str:
    document = Document(str(path))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def extract_text(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix == ".pdf":
        text = _extract_pdf(path)
    elif suffix == ".docx":
        text = _extract_docx(path)
    else:
        raise CVError("Supported CV formats are PDF and DOCX.")
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not text:
        raise CVError("No extractable text was found in the CV.")
    return text + "\n"


def _lines_for_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {name: [] for name in SECTION_ALIASES}
    current: str | None = None
    aliases = {
        alias.casefold(): name for name, values in SECTION_ALIASES.items() for alias in values
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized = re.sub(r"[^a-z ]", "", line.casefold()).strip()
        matched = next((name for alias, name in aliases.items() if normalized == alias), None)
        if matched:
            current = matched
            continue
        if current:
            sections[current].append(line)
    return sections


def _candidate_name(text: str) -> str | None:
    for line in text.splitlines()[:8]:
        clean = line.strip()
        if (
            clean
            and not EMAIL_RE.search(clean)
            and not URL_RE.search(clean)
            and not any(char.isdigit() for char in clean)
            and 1 < len(clean.split()) <= 5
        ):
            return clean
    return None


def _clean_links(text: str) -> list[str]:
    links: set[str] = set()
    for match in URL_RE.findall(text):
        value = match.rstrip(".,;)")
        if value.startswith("/linkedin/"):
            value = "https://linkedin.com/in/" + value.removeprefix("/linkedin/")
        elif value.startswith("/github/"):
            value = "https://github.com/" + value.removeprefix("/github/")
        elif value.startswith("www."):
            value = "https://" + value
        links.add(value)
    return sorted(links)


def _location(text: str) -> str | None:
    for raw_line in text.splitlines()[:40]:
        match = LOCATION_LABEL_RE.search(raw_line.strip())
        if not match:
            continue
        location = match.group(1).strip(" ,.;")
        if 2 <= len(location) <= 80:
            return location
    return None


def build_profile(text: str, source_name: str) -> dict[str, Any]:
    sections = _lines_for_sections(text)
    email_match = EMAIL_RE.search(text)
    phone_match = PHONE_RE.search(text)
    links = _clean_links(text)
    lowered = text.casefold()
    questions = [
        f"needs_user_answer: {fact} is not stated in the CV."
        for fact, indicators in UNKNOWN_FACTS
        if not any(indicator in lowered for indicator in indicators)
    ]
    location = _location(text)
    return {
        "source_document": source_name,
        "source_truth_policy": "Only statements extracted from the supplied CV may be used as facts.",
        "generated_at": datetime.now(UTC).isoformat(),
        "name": _candidate_name(text),
        "email": email_match.group(0) if email_match else None,
        "phone": phone_match.group(0).strip() if phone_match else None,
        "location": location,
        "education": sections["education"],
        "work_experience": sections["work_experience"],
        "projects": sections["projects"],
        "skills": sections["skills"],
        "languages": sections["languages"]
        or [line for line in sections["skills"] if line.casefold().startswith("•languages:")],
        "certifications": sections["certifications"],
        "links": links,
        "constraints_questions_needing_user_confirmation": questions,
    }


def ingest_cv(input_path: Path, settings: Settings) -> IngestionResult:
    settings.ensure_directories()
    source = input_path.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise CVError(f"CV file not found: {source}")
    if source.suffix.casefold() not in {".pdf", ".docx"}:
        raise CVError("Supported CV formats are PDF and DOCX.")
    destination = settings.cv_dir / source.name
    if destination.resolve() != source:
        shutil.copy2(source, destination)
        destination.chmod(0o600)
    else:
        destination.chmod(0o600)
    text = extract_text(destination)
    extracted_path = settings.profile_dir / "cv_extracted.md"
    extracted_path.write_text(f"# Extracted CV Text\n\n{text}", encoding="utf-8")
    extracted_path.chmod(0o600)
    profile = build_profile(text, destination.name)
    profile_path = settings.profile_dir / "candidate_profile.json"
    profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    profile_path.chmod(0o600)
    return IngestionResult(destination, extracted_path, profile_path, profile)


def load_profile(settings: Settings) -> dict[str, Any]:
    path = settings.profile_dir / "candidate_profile.json"
    if not path.exists():
        raise CVError("No candidate profile exists. Run ingest-cv first.")
    return json.loads(path.read_text(encoding="utf-8"))
