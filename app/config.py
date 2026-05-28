"""Configuration and filesystem protection for the local-first agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_ALLOWED_HOSTS = (
    "html.duckduckgo.com",
    "duckduckgo.com",
    "www.google.com",
    "google.com",
    "jobs.lever.co",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "careers.smartrecruiters.com",
)


class ConfigurationError(ValueError):
    """Raised when configuration would break a safety invariant."""


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    ollama_base_url: str
    ollama_model: str
    allowed_hosts: tuple[str, ...]
    dashboard_host: str
    dashboard_port: int
    manual_review_url: str

    @classmethod
    def load(cls, root: Path | None = None) -> "Settings":
        project_root = (root or Path(__file__).resolve().parent.parent).resolve()
        load_dotenv(project_root / ".env")
        settings = cls(
            root=project_root,
            data_dir=project_root / "data",
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "gemma4:e4b-it-q4_K_M"),
            allowed_hosts=tuple(
                part.strip().lower()
                for part in os.getenv("JOB_AGENT_ALLOWED_HOSTS", ",".join(DEFAULT_ALLOWED_HOSTS)).split(",")
                if part.strip()
            ),
            dashboard_host=os.getenv("JOB_AGENT_DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=int(os.getenv("JOB_AGENT_DASHBOARD_PORT", "7860")),
            manual_review_url=os.getenv("JOB_AGENT_MANUAL_REVIEW_URL", ""),
        )
        settings.validate()
        return settings

    @property
    def cv_dir(self) -> Path:
        return self.data_dir / "cv"

    @property
    def profile_dir(self) -> Path:
        return self.data_dir / "profiles"

    @property
    def session_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def browser_profile_dir(self) -> Path:
        return self.session_dir / "browser-profile"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def applications_dir(self) -> Path:
        return self.data_dir / "applications"

    def validate(self) -> None:
        llm_host = (urlparse(self.ollama_base_url).hostname or "").lower()
        if llm_host not in LOCAL_HOSTS:
            raise ConfigurationError("OLLAMA_BASE_URL must be loopback-only.")
        if self.dashboard_host.lower() not in LOCAL_HOSTS:
            raise ConfigurationError("Dashboard host must be loopback-only.")
        if not self.allowed_hosts:
            raise ConfigurationError("At least one allowed read-only job host is required.")

    def ensure_directories(self) -> None:
        directories = (
            self.data_dir,
            self.cv_dir,
            self.profile_dir,
            self.session_dir,
            self.browser_profile_dir,
            self.log_dir,
            self.log_dir / "runs",
            self.log_dir / "screenshots",
            self.log_dir / "page_contexts",
            self.applications_dir,
            self.applications_dir / "drafts",
            self.applications_dir / "approvals",
            self.applications_dir / "prepared",
            self.applications_dir / "submissions",
            self.applications_dir / "submission_attempts",
            self.applications_dir / "gmail_checks",
        )
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)

    def is_allowed_url(self, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and host in self.allowed_hosts
