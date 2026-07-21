"""Append-only JSONL audit log for voice synthesis calls.

One line per ``synthesize_speech`` call (success or failure). Schema is
documented in the design spec. Audit-write failures are swallowed so an
audit problem never breaks a synthesis call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agent_core.audit import JsonlAuditLog


@dataclass(frozen=True)
class AuditEvent:
    """One line in the voice audit log."""

    timestamp: datetime
    agent: str
    voice_id: str
    text_len: int
    seed: int
    duration_s: float | None
    generation_s: float | None
    wav_path: str | None
    error: str | None


class AuditLog(JsonlAuditLog[AuditEvent]):
    """Append-only JSONL audit log."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)

    def _serialize(self, event: AuditEvent) -> str:
        payload = {
            "ts": event.timestamp.isoformat(),
            "agent": event.agent,
            "voice_id": event.voice_id,
            "text_len": event.text_len,
            "seed": event.seed,
            "duration_s": event.duration_s,
            "generation_s": event.generation_s,
            "wav_path": event.wav_path,
            "error": event.error,
        }
        return json.dumps(payload, ensure_ascii=False)


__all__ = ["AuditEvent", "AuditLog"]
