"""Non-negotiable policy gates for browser and communication actions."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Mapping


class RiskClass(StrEnum):
    READ_ONLY = "read_only"
    FORM_FILL = "form_fill"
    ACCOUNT_LOGIN = "account_login"
    EMAIL_SEND = "email_send"
    JOB_SUBMIT = "job_submit"
    DESTRUCTIVE = "destructive"
    PAYMENT = "payment"
    UNKNOWN = "unknown"


BLOCKED_RISKS = {RiskClass.DESTRUCTIVE, RiskClass.PAYMENT}
CONFIRMATION_RISKS = {RiskClass.EMAIL_SEND, RiskClass.JOB_SUBMIT, RiskClass.UNKNOWN}
MANUAL_RISKS = {RiskClass.ACCOUNT_LOGIN}
SENSITIVE_KEY_RE = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|cookie|authorization|auth|session|ssn)",
    flags=re.IGNORECASE,
)


class PolicyViolation(PermissionError):
    """Raised when a requested action violates an approval gate."""


def redact_mapping(values: Mapping[str, Any] | None) -> dict[str, Any]:
    """Redact credentials before any input values reach audit storage."""
    if not values:
        return {}
    result: dict[str, Any] = {}
    for key, value in values.items():
        if SENSITIVE_KEY_RE.search(str(key)):
            result[str(key)] = "[REDACTED]"
        elif isinstance(value, Mapping):
            result[str(key)] = redact_mapping(value)
        else:
            result[str(key)] = value
    return result


def classify_action(action: str) -> RiskClass:
    normalized = action.lower().replace("-", "_").replace(" ", "_")
    if any(word in normalized for word in ("pay", "purchase", "credit_card")):
        return RiskClass.PAYMENT
    if any(word in normalized for word in ("delete", "withdraw", "remove_account")):
        return RiskClass.DESTRUCTIVE
    if "submit" in normalized or "apply_final" in normalized:
        return RiskClass.JOB_SUBMIT
    if "send" in normalized and "email" in normalized:
        return RiskClass.EMAIL_SEND
    if any(word in normalized for word in ("login", "sign_in", "authenticate")):
        return RiskClass.ACCOUNT_LOGIN
    if any(word in normalized for word in ("fill", "type", "draft_form")):
        return RiskClass.FORM_FILL
    if any(word in normalized for word in ("read", "search", "view", "extract", "navigate")):
        return RiskClass.READ_ONLY
    return RiskClass.UNKNOWN


def assert_action_allowed(
    risk: RiskClass,
    *,
    confirmed: bool = False,
    manual: bool = False,
) -> None:
    if risk in BLOCKED_RISKS:
        raise PolicyViolation(f"{risk.value} actions are blocked by default.")
    if risk in MANUAL_RISKS and not manual:
        raise PolicyViolation("Account login must be performed manually in the visible browser.")
    if risk in CONFIRMATION_RISKS and not confirmed:
        raise PolicyViolation(f"{risk.value} requires explicit user confirmation.")


def require_typed_confirmation(actual: str, expected: str) -> None:
    if actual.strip() != expected:
        raise PolicyViolation(f"Confirmation did not match required phrase: {expected}")
