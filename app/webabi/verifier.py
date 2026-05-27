"""Small explicit pre/postcondition verifier for browser snapshots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageSnapshot:
    url: str
    title: str
    visible_text: str = ""


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    checks: list[str]
    errors: list[str]


def verify_snapshot(snapshot: PageSnapshot, conditions: list[str]) -> VerificationResult:
    errors: list[str] = []
    passed_checks: list[str] = []
    for condition in conditions:
        kind, separator, expected = condition.partition(":")
        if not separator:
            errors.append(f"Unsupported condition: {condition}")
            continue
        expected = expected.strip()
        target = {
            "url_contains": snapshot.url,
            "title_contains": snapshot.title,
            "text_contains": snapshot.visible_text,
        }.get(kind.strip())
        if target is None:
            errors.append(f"Unsupported condition: {condition}")
        elif expected.casefold() not in target.casefold():
            errors.append(f"Condition failed: {condition}")
        else:
            passed_checks.append(condition)
    return VerificationResult(passed=not errors, checks=passed_checks, errors=errors)
