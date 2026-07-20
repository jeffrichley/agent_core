"""Append-only JSONL audit log for brief submissions.

The submit handler (T13) writes one or more events per ``submit_brief``
call:

- ``submit.validate.fail`` — validation rejected the submission. The
  ``data`` payload carries the issue list; no delivery was attempted
  and the session token is NOT consumed (the agent can retry).
- ``submit.deliver`` — one destination's delivery outcome (success or
  failure). Carries ``destination_type``, ``success``, ``ref``,
  ``error``.
- ``submit.complete`` — end-of-flow summary. Carries totals and an
  ``overall_success`` flag (true iff at least one destination
  succeeded).

Format
------
Each event is one JSON line. ``timestamp`` is serialized as an ISO
8601 string. ``session_token`` is truncated to 8 chars (``first8``)
for both privacy and readability — the full 32-char token is the
agent's secret to hold and shouldn't show up in shared log files.

Default location is ``~/.agent-core/briefs/audit.jsonl``; parent
directories are created if missing on first write.

Async I/O
---------
Writes go through :func:`asyncio.to_thread` to honor T4's cancellation
contract — same pattern as
:class:`agent_core_briefs.destinations.markdown_file.MarkdownFileDestination`.
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
    """One line in the briefs audit log.

    ``session_token`` should already be truncated to 8 chars by the
    submit handler before construction — :meth:`AuditLog.write` does
    NOT re-truncate, so a caller that passes the full token would
    leak it into the log.
    """

    timestamp: datetime
    event_type: str
    session_token: str
    brief_type: str
    target_agent: str
    data: dict[str, Any]


class AuditLog(JsonlAuditLog[AuditEvent]):
    """Append-only JSONL audit log.

    Construction takes a path; :meth:`write` appends one JSON line per
    event. Failures (disk full, permission denied) are logged to
    stderr via the module logger and swallowed — the audit log is
    observability, not the critical path of brief delivery.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(path)

    @staticmethod
    def default_path() -> Path:
        """Returns ``~/.agent-core/briefs/audit.jsonl``."""
        return Path.home() / ".agent-core" / "briefs" / "audit.jsonl"

    def _serialize(self, event: AuditEvent) -> str:
        payload = {
            "timestamp": event.timestamp.isoformat(),
            "event_type": event.event_type,
            "session_token": event.session_token,
            "brief_type": event.brief_type,
            "target_agent": event.target_agent,
            "data": event.data,
        }
        return json.dumps(payload, default=str, ensure_ascii=False)


__all__ = ["AuditEvent", "AuditLog"]
