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


class AuditLog:
    """Append-only JSONL audit log.

    Construction takes a path; :meth:`write` appends one JSON line per
    event. Failures (disk full, permission denied) are logged to
    stderr via the module logger and swallowed — the audit log is
    observability, not the critical path of brief delivery.
    """

    def __init__(self, path: Path):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    @staticmethod
    def default_path() -> Path:
        """Returns ``~/.agent-core/briefs/audit.jsonl``."""
        return Path.home() / ".agent-core" / "briefs" / "audit.jsonl"

    async def write(self, event: AuditEvent) -> None:
        """Append one event as a JSON line.

        Failures are logged and swallowed so an audit failure never
        breaks a brief submission. The actual filesystem write runs
        in :func:`asyncio.to_thread` so a slow disk doesn't block
        the event loop.
        """
        try:
            line = self._serialize(event)
            await asyncio.to_thread(self._append_line, self._path, line)
        except Exception as exc:
            # Audit log is observability; never break the submit flow.
            # Mirror to stderr so operators see the failure even if the
            # logging config is silent.
            msg = f"agent_core_briefs.audit: write failed for {self._path}: {exc}"
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
            "event_type": event.event_type,
            "session_token": event.session_token,
            "brief_type": event.brief_type,
            "target_agent": event.target_agent,
            "data": event.data,
        }
        return json.dumps(payload, default=str, ensure_ascii=False)


__all__ = ["AuditEvent", "AuditLog"]
