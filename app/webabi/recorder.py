"""Append-only, permission-protected JSONL audit recorder."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .schema import ActionRecord


class AuditRecorder:
    def __init__(self, runs_dir: Path, run_id: str | None = None) -> None:
        runs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        runs_dir.chmod(0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = run_id or f"{stamp}-{uuid4().hex[:8]}"
        self.path = runs_dir / f"{self.run_id}.jsonl"

    def record(self, action: ActionRecord) -> Path:
        if action.run_id != self.run_id:
            raise ValueError("Action record run_id does not match recorder run.")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(self.path, flags, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(action.to_dict(), ensure_ascii=True) + "\n")
        self.path.chmod(0o600)
        return self.path
