"""Compile user-level actions into policy-checked execution intents."""

from __future__ import annotations

from typing import Any

from app.policy import RiskClass, assert_action_allowed

from .risk import assess_risk, needs_final_approval
from .schema import ActionIntent


def compile_intent(
    workflow: str,
    action: str,
    *,
    values: dict[str, Any] | None = None,
    preconditions: list[str] | None = None,
    postconditions: list[str] | None = None,
    confirmed: bool = False,
    manual: bool = False,
) -> ActionIntent:
    risk = assess_risk(action)
    assert_action_allowed(risk, confirmed=confirmed, manual=manual)
    return ActionIntent(
        workflow=workflow,
        action=action,
        risk=risk,
        input_values=values or {},
        preconditions=preconditions or [],
        postconditions=postconditions or [],
        requires_confirmation=needs_final_approval(risk),
    )


def explicitly_blocked_intent(workflow: str, action: str) -> ActionIntent:
    """Represent an unsafe request for logging without authorizing execution."""
    risk = assess_risk(action)
    if risk not in {RiskClass.DESTRUCTIVE, RiskClass.PAYMENT}:
        risk = RiskClass.UNKNOWN
    return ActionIntent(workflow=workflow, action=action, risk=risk, requires_confirmation=True)
