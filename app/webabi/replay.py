"""Read-only replay summaries for prior WebABI audit logs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def summarize_log(path: Path) -> str:
    records = read_log(path)
    lines = [f"# Audit Replay: {path.name}", "", f"Actions recorded: {len(records)}", ""]
    for record in records:
        lines.append(
            f"- {record.get('timestamp', '?')} | {record.get('risk_classification', '?')} | "
            f"{record.get('selected_action', '?')} | {record.get('result', '?')}"
        )
    return "\n".join(lines) + "\n"
