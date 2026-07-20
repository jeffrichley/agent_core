"""Append-only JSONL audit log for webcam tool invocations.

Each ``capture_webcam_frame`` and ``list_cameras`` call writes one line.
Schema is documented in the design spec. Failures are swallowed so an
audit failure never breaks a capture.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_core.audit import JsonlAuditLog


@dataclass(frozen=True)
class AuditEvent:
    """One line in the webcam audit log."""

    timestamp: datetime
    tool: str
    result: str  # "ok" | "error"
    data: dict[str, Any]


class AuditLog(JsonlAuditLog[AuditEvent]):
    """Append-only JSONL audit log."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)

    @staticmethod
    def default_path(endpoint_name: str) -> Path:
        """Returns ``~/.agent-core/webcam/<endpoint_name>/audit.jsonl``."""
        return Path.home() / ".agent-core" / "webcam" / endpoint_name / "audit.jsonl"

    def _serialize(self, event: AuditEvent) -> str:
        payload = {
            "timestamp": event.timestamp.isoformat(),
            "tool": event.tool,
            "result": event.result,
            "data": event.data,
        }
        return json.dumps(payload, default=str, ensure_ascii=False)


__all__ = ["AuditEvent", "AuditLog"]
