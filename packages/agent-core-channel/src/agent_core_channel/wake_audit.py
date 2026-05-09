"""Issue #70: per-wake audit log written by the relay.

One JSONL line per wake event. Schema:

    {
        "ts": "2026-05-09T03:29:22+00:00",
        "agent": "pepper",
        "wake_id": "w-abc123",
        "mode": "full",
        "envelopes_inlined": [
            {"id": "e-001", "kind": "TextMessage", "from": "discord-pepper",
             "urgency": "green", "bytes": 87}
        ],
        "queue_total_count": 1,
        "fallback": null
    }

The bus's existing mcp_audit middleware records the agent's subsequent
tool calls. The wake-stats analyzer (Phase 5) joins these two log streams
to compute per-wake outcomes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class WakeAuditWriter:
    """Append-only JSONL writer for relay-side wake events.

    Creates the parent directory and target file lazily on first write.
    Each ``write_*`` call writes exactly one line. Atomic line writes are
    achieved via a single ``write`` call (POSIX append semantics handle
    the rest for our line sizes).
    """

    def __init__(self, target: Path) -> None:
        self._target = Path(target)

    def _emit(self, payload: dict) -> None:
        self._target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, sort_keys=True, default=str) + "\n"
        with self._target.open("a", encoding="utf-8") as f:
            f.write(line)

    def write_inlined(
        self,
        *,
        wake_id: str,
        agent: str,
        mode: str,
        envelopes_inlined: list[dict],
        queue_total_count: int,
    ) -> None:
        self._emit(
            {
                "ts": datetime.now(UTC).isoformat(),
                "agent": agent,
                "wake_id": wake_id,
                "mode": mode,
                "envelopes_inlined": envelopes_inlined,
                "queue_total_count": queue_total_count,
                "fallback": None,
            }
        )

    def write_fallback(
        self,
        *,
        wake_id: str,
        agent: str,
        mode: str,
        reason: str,
        error_message: str | None = None,
    ) -> None:
        payload: dict = {
            "ts": datetime.now(UTC).isoformat(),
            "agent": agent,
            "wake_id": wake_id,
            "mode": mode,
            "envelopes_inlined": [],
            "queue_total_count": 0,
            "fallback": reason,
        }
        if error_message is not None:
            payload["error"] = error_message
        self._emit(payload)
