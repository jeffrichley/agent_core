"""Append-only JSONL audit log for MCP tools/call invocations.

One line per call across every endpoint, mirrored on the daily-rotation
convention used by ``bus_hooks.daily_raw_jsonl``. The writer is held by
the runner as a singleton and shared across endpoints; concurrency
serialization is per-writer so two endpoints writing to the same daily
file produce intact, non-interleaved lines.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditLine:
    """One audit-log line produced by ``MCPAuditMiddleware`` per tool call.

    ``error`` is ``None`` on the success path. ``session_id`` is ``None``
    for in-memory FastMCP transports that don't carry an mcp-session-id
    header (used in tests).
    """

    timestamp: datetime
    endpoint: str
    session_id: str | None
    request_id: str | None
    tool: str
    args_summary: dict[str, Any]
    duration_ms: int
    result: str  # "ok" | "error"
    error: dict[str, str] | None  # {"type": ..., "message": ...} or None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "timestamp": self.timestamp.isoformat(),
            "endpoint": self.endpoint,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "tool": self.tool,
            "args_summary": self.args_summary,
            "duration_ms": self.duration_ms,
            "result": self.result,
        }
        if self.error is not None:
            d["error"] = self.error
        return d


def daily_path(
    log_root: Path, *, timezone: str = "US/Eastern", when: datetime | None = None
) -> Path:
    """Return ``<log_root>/<YYYY-MM-DD>.jsonl`` for ``when`` in ``timezone``.

    Local-midnight rollover, identical convention to
    ``agent_core.bus_log.writer.daily_path``. A 23:50 ET event lands in
    today's file; 00:10 ET goes to tomorrow's.
    """
    if when is None:
        when = datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    local_date = when.astimezone(ZoneInfo(timezone)).date()
    return log_root / f"{local_date.isoformat()}.jsonl"


class MCPAuditWriter:
    """Append-only JSONL writer with daily rotation and async-safe locking.

    Constructed once by the runner; shared across all
    ``ClaudeCodeMCPEndpoint`` instances. ``write()`` is awaitable and
    serialized via an ``asyncio.Lock`` so concurrent calls produce
    intact lines on disk. Disk I/O runs in a thread via
    ``asyncio.to_thread`` to keep the event loop responsive.

    Failures are swallowed: a broken log directory must never break a
    tool call. Errors are logged at WARNING.
    """

    def __init__(self, *, log_root: Path | str, timezone: str = "US/Eastern") -> None:
        self._log_root = Path(log_root).expanduser()
        self._timezone = timezone
        self._lock = asyncio.Lock()

    @property
    def log_root(self) -> Path:
        return self._log_root

    @property
    def timezone(self) -> str:
        return self._timezone

    async def write(self, line: AuditLine) -> None:
        path = daily_path(self._log_root, timezone=self._timezone, when=line.timestamp)
        try:
            payload = json.dumps(line.to_dict(), default=str, ensure_ascii=False)
        except Exception:
            log.warning("mcp_audit: failed to serialize AuditLine", exc_info=True)
            return
        async with self._lock:
            try:
                await asyncio.to_thread(self._append_line, path, payload)
            except Exception:
                log.warning("mcp_audit: failed to append to %s", path, exc_info=True)

    @staticmethod
    def _append_line(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="") as f:
            f.write(payload)
            f.write("\n")


__all__ = ["AuditLine", "MCPAuditWriter", "daily_path"]
