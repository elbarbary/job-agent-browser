"""Generate private PDF cover letters grounded in the CV and job description."""

from __future__ import annotations

import json
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .cv_store import load_profile
from .llm_client import LocalLLMClient, LocalLLMError


def generate_cover_letter(settings: Settings, job: dict[str, Any], *, llm: LocalLLMClient | None = None) -> Path:
    settings.ensure_directories()
    profile = load_profile(settings)
    letter_text = _generate_letter_text(profile, job, llm=llm or LocalLLMClient(settings))
    output = settings.applications_dir / "cover_letters" / f"{job['id']}.pdf"
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write_simple_pdf(output, letter_text)
    output.chmod(0o600)
    meta = {
        "job_id": job.get("id"),
        "job_url": job.get("url"),
        "generated_at": datetime.now(UTC).isoformat(),
        "policy": "Generated from CV profile and job description only; user must review before use.",
    }
    output.with_suffix(".json").write_text(json.dumps(meta, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    output.with_suffix(".json").chmod(0o600)
    return output


def _generate_letter_text(profile: dict[str, Any], job: dict[str, Any], *, llm: LocalLLMClient) -> str:
    prompt = (
        "Write a concise one-page cover letter for this job application. Use only facts present in "
        "the CV profile and job description. Do not invent education, employment, certifications, "
        "citizenship, work authorization, location, salary, or achievements. If a fact is absent, "
        "avoid mentioning it. Keep the tone professional and specific.\n\n"
        f"CV PROFILE:\n{json.dumps(profile, ensure_ascii=True)}\n\n"
        f"JOB:\n{json.dumps(job, ensure_ascii=True)}"
    )
    try:
        text = llm.chat(prompt).strip()
    except LocalLLMError:
        text = _fallback_letter(profile, job)
    return text or _fallback_letter(profile, job)


def _fallback_letter(profile: dict[str, Any], job: dict[str, Any]) -> str:
    name = profile.get("name") or "[Your Name]"
    title = job.get("title") or "the role"
    company = job.get("company") or "your team"
    skills = profile.get("skills") or []
    skills_text = ", ".join(str(item) for item in skills[:8]) if isinstance(skills, list) else str(skills)
    return (
        f"Dear Hiring Team,\n\n"
        f"I am writing to express my interest in {title} at {company}. My CV shows experience and skills "
        f"that align with the role, including {skills_text or 'the relevant areas described in my CV'}.\n\n"
        "I am especially interested in work where I can contribute to product, software, and AI-related "
        "systems while continuing to learn from a strong engineering team. I would welcome the chance to "
        "discuss how my background fits your needs.\n\n"
        f"Sincerely,\n{name}\n"
    )


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_simple_pdf(path: Path, text: str) -> None:
    lines: list[str] = []
    for paragraph in text.splitlines():
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(textwrap.wrap(paragraph, width=88) or [""])
    stream_lines = ["BT", "/F1 11 Tf", "72 750 Td", "14 TL"]
    first = True
    for line in lines[:48]:
        if not first:
            stream_lines.append("T*")
        first = False
        stream_lines.append(f"({_pdf_escape(line)}) Tj")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode("ascii"))
        content.extend(obj)
        content.extend(b"\nendobj\n")
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(bytes(content))
