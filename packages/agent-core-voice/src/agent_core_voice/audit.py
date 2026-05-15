"""Append-only JSONL audit log for voice synthesis calls.

One line per ``synthesize_speech`` call (success or failure). Schema is
documented in the design spec. Audit-write failures are swallowed so an
audit problem never breaks a synthesis call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


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


class AuditLog:
    """Append-only JSONL audit log."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    async def write(self, event: AuditEvent) -> None:
        try:
            line = self._serialize(event)
            await asyncio.to_thread(self._append_line, self._path, line)
        except Exception as exc:
            msg = f"agent_core_voice.audit: write failed for {self._path}: {exc}"
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
