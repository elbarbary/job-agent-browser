"""Data structures for WebABI-style browser audit records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.policy import RiskClass, redact_mapping


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ActionCandidate:
    label: str
    action_type: str
    selector: str | None = None


@dataclass
class ActionIntent:
    workflow: str
    action: str
    risk: RiskClass
    input_values: dict[str, Any] = field(default_factory=dict)
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    requires_confirmation: bool = False


@dataclass
class ActionRecord:
    run_id: str
    workflow: str
    page_url: str
    page_title: str
    visible_action_candidates: list[ActionCandidate]
    selected_action: str
    risk_classification: RiskClass
    input_values: dict[str, Any] = field(default_factory=dict)
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    screenshot_path: str | None = None
    result: str = "pending"
    errors: list[str] = field(default_factory=list)
    approved: bool = False
    timestamp: str = field(default_factory=utc_timestamp)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["risk_classification"] = self.risk_classification.value
        payload["input_values"] = redact_mapping(self.input_values)
        return payload
