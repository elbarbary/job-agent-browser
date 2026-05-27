"""Risk assessment facade for WebABI records."""

from __future__ import annotations

from app.policy import RiskClass, classify_action


def assess_risk(action: str) -> RiskClass:
    return classify_action(action)


def needs_final_approval(risk: RiskClass) -> bool:
    return risk in {RiskClass.EMAIL_SEND, RiskClass.JOB_SUBMIT, RiskClass.UNKNOWN}
