"""Append-only JSONL audit log for webcam tool invocations.

Each ``capture_webcam_frame`` and ``list_cameras`` call writes one line.
Schema is documented in the design spec. Failures are swallowed so an
audit failure never breaks a capture.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditEvent:
    """One line in the webcam audit log."""

    timestamp: datetime
    tool: str
    result: str  # "ok" | "error"
    data: dict[str, Any]


class AuditLog:
    """Append-only JSONL audit log."""

    def __init__(self, path: Path):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    @staticmethod
    def default_path(endpoint_name: str) -> Path:
        """Returns ``~/.agent-core/webcam/<endpoint_name>/audit.jsonl``."""
        return Path.home() / ".agent-core" / "webcam" / endpoint_name / "audit.jsonl"

    async def write(self, event: AuditEvent) -> None:
        try:
            line = self._serialize(event)
            await asyncio.to_thread(self._append_line, self._path, line)
        except Exception as exc:
            msg = f"agent_core_webcam.audit: write failed for {self._path}: {exc}"
            log.warning(msg)
            print(msg, file=sys.stderr)

    @staticmethod
    def _append_line(path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")

    @staticmethod
    def _serialize(event: AuditEvent) -> str:
        payload = {
            "timestamp": event.timestamp.isoformat(),
            "tool": event.tool,
            "result": event.result,
            "data": event.data,
        }
        return json.dumps(payload, default=str, ensure_ascii=False)


__all__ = ["AuditEvent", "AuditLog"]
