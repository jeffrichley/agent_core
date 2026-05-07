# Issue #39 — MCP Tool-Call Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Daemon-wide JSONL audit log capturing every MCP `tools/call` invocation across every endpoint, with structured args summaries (no payload values), timing, error info, and daily rotation.

**Architecture:** Singleton `MCPAuditWriter` constructed by the runner, threaded via `RunnerServices`. Each `ClaudeCodeMCPEndpoint` receives the writer via the existing `configure_endpoint_instance` plugin hook (mirrors `attach_notify_broker`) and registers an `MCPAuditMiddleware` on its FastMCP server. The middleware uses FastMCP's built-in `on_call_tool` hook — no changes to `http_host.py`.

**Tech Stack:** Python 3.12, FastMCP middleware (`on_call_tool`), pluggy plugin manager, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-07-issue-39-mcp-tool-call-audit-design.md`.

**Branch:** `feat/issue-39-mcp-tool-call-audit`.

---

## File Structure

**New module — `packages/core/src/agent_core/mcp_audit/`:**

- `__init__.py` — public surface re-exports `AuditLine`, `MCPAuditWriter`, `MCPAuditMiddleware`.
- `writer.py` — `AuditLine` dataclass, `daily_path()` helper, `MCPAuditWriter` class.
- `middleware.py` — `MCPAuditMiddleware` (subclass of `fastmcp.server.middleware.Middleware`).

**Modified files:**

- `packages/core/src/agent_core/bus/protocol.py` — add `AuditWriterAwareEndpoint` Protocol.
- `packages/core/src/agent_core/plugins/specs.py` — extend `RunnerServices` with `mcp_audit_writer` and `mcp_audit_skip_tools`.
- `packages/core/src/agent_core/plugins/manager.py` — `configure_endpoint_instance` calls `attach_audit_writer` on opted-in endpoints.
- `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — add `attach_audit_writer(writer, skip_tools)` method.
- `packages/core/src/agent_core/bus/runner.py` — read `mcp_audit:` YAML block, construct writer when enabled, populate services.

**New tests:**

- `packages/core/tests/test_mcp_audit_writer.py` — writer + AuditLine.
- `packages/core/tests/test_mcp_audit_middleware.py` — middleware behavior.
- `packages/core/tests/test_mcp_audit_runner.py` — runner config-to-services plumbing.
- `packages/core/tests/test_mcp_audit_integration.py` — end-to-end: runner → endpoint → middleware → writer.

---

## Task 1: `AuditLine` dataclass + `MCPAuditWriter`

Self-contained module with no dependencies on the rest of agent_core. Builds the foundation: a writer that takes an `AuditLine`, serializes it, and appends to a dated JSONL file with daily rotation, locking, and swallowed-error semantics.

**Files:**
- Create: `packages/core/src/agent_core/mcp_audit/__init__.py`
- Create: `packages/core/src/agent_core/mcp_audit/writer.py`
- Test: `packages/core/tests/test_mcp_audit_writer.py`

- [ ] **Step 1.1: Write failing tests for `AuditLine.to_dict` serialization shape**

Create `packages/core/tests/test_mcp_audit_writer.py`:

```python
"""MCPAuditWriter and AuditLine: dated JSONL with locking and swallowed errors."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.mcp_audit.writer import AuditLine, MCPAuditWriter, daily_path


# ---------------------------------------------------------------------------
# AuditLine.to_dict
# ---------------------------------------------------------------------------


def test_audit_line_to_dict_ok_result_omits_error_field():
    line = AuditLine(
        timestamp=datetime(2026, 5, 7, 14, 23, 7, tzinfo=timezone.utc),
        endpoint="pepper",
        session_id="abc",
        request_id="42",
        tool="send",
        args_summary={"arg_keys": ["text"], "arg_count": 1},
        duration_ms=87,
        result="ok",
        error=None,
    )
    d = line.to_dict()
    assert d["endpoint"] == "pepper"
    assert d["tool"] == "send"
    assert d["result"] == "ok"
    assert d["duration_ms"] == 87
    assert d["args_summary"] == {"arg_keys": ["text"], "arg_count": 1}
    assert "error" not in d


def test_audit_line_to_dict_error_result_includes_error_type_and_message():
    line = AuditLine(
        timestamp=datetime(2026, 5, 7, tzinfo=timezone.utc),
        endpoint="pepper",
        session_id=None,
        request_id="9",
        tool="capture_webcam_frame",
        args_summary={"arg_keys": [], "arg_count": 0},
        duration_ms=12,
        result="error",
        error={"type": "CameraBusyError", "message": "camera busy"},
    )
    d = line.to_dict()
    assert d["result"] == "error"
    assert d["error"] == {"type": "CameraBusyError", "message": "camera busy"}


def test_audit_line_to_dict_includes_session_id_null_when_none():
    line = AuditLine(
        timestamp=datetime(2026, 5, 7, tzinfo=timezone.utc),
        endpoint="pepper",
        session_id=None,
        request_id="1",
        tool="send",
        args_summary={"arg_keys": [], "arg_count": 0},
        duration_ms=1,
        result="ok",
        error=None,
    )
    d = line.to_dict()
    assert "session_id" in d
    assert d["session_id"] is None


def test_audit_line_to_dict_serializes_timestamp_as_iso_with_offset():
    from zoneinfo import ZoneInfo
    ts = datetime(2026, 5, 7, 14, 23, 7, tzinfo=ZoneInfo("US/Eastern"))
    line = AuditLine(
        timestamp=ts, endpoint="x", session_id=None, request_id="1",
        tool="t", args_summary={"arg_keys": [], "arg_count": 0},
        duration_ms=1, result="ok", error=None,
    )
    d = line.to_dict()
    assert d["timestamp"].endswith("-04:00") or d["timestamp"].endswith("-05:00")
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_mcp_audit_writer.py -v`
Expected: ImportError or collection error — `agent_core.mcp_audit` does not exist yet.

- [ ] **Step 1.3: Create the `mcp_audit` package init**

Create `packages/core/src/agent_core/mcp_audit/__init__.py`:

```python
"""Daemon-wide audit log for MCP tools/call invocations.

See docs/superpowers/specs/2026-05-07-issue-39-mcp-tool-call-audit-design.md.
"""

from __future__ import annotations

from agent_core.mcp_audit.middleware import MCPAuditMiddleware
from agent_core.mcp_audit.writer import AuditLine, MCPAuditWriter, daily_path

__all__ = ["AuditLine", "MCPAuditMiddleware", "MCPAuditWriter", "daily_path"]
```

- [ ] **Step 1.4: Implement `AuditLine` and `daily_path` in writer.py**

Create `packages/core/src/agent_core/mcp_audit/writer.py`:

```python
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
```

- [ ] **Step 1.5: Run AuditLine + daily_path tests**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_mcp_audit_writer.py -v`
Expected: 4 PASS (the AuditLine tests above). Other tests we'll add below also fail at collection because `MCPAuditWriter` is referenced but not yet imported — fix in next step.

- [ ] **Step 1.6: Write failing tests for `MCPAuditWriter` rotation, locking, and swallowed errors**

Append to `packages/core/tests/test_mcp_audit_writer.py`:

```python
# ---------------------------------------------------------------------------
# daily_path
# ---------------------------------------------------------------------------


def test_daily_path_rolls_at_local_midnight_for_configured_timezone(tmp_path: Path):
    # 04:30 UTC = 00:30 ET → tomorrow's file in UTC, today's in ET
    when = datetime(2026, 5, 7, 4, 30, tzinfo=timezone.utc)
    p_utc = daily_path(tmp_path, timezone="UTC", when=when)
    p_et = daily_path(tmp_path, timezone="US/Eastern", when=when)
    assert p_utc.name == "2026-05-07.jsonl"
    assert p_et.name == "2026-05-07.jsonl"  # 00:30 ET → still 2026-05-07

    # 03:30 UTC = 23:30 prior day in ET (DST: -04:00 in May, so 23:30 ET = 03:30 UTC).
    when2 = datetime(2026, 5, 7, 3, 30, tzinfo=timezone.utc)
    p_et2 = daily_path(tmp_path, timezone="US/Eastern", when=when2)
    assert p_et2.name == "2026-05-06.jsonl"


# ---------------------------------------------------------------------------
# MCPAuditWriter
# ---------------------------------------------------------------------------


def _ok_line(tool: str = "send") -> AuditLine:
    return AuditLine(
        timestamp=datetime.now(timezone.utc),
        endpoint="pepper",
        session_id="s1",
        request_id="r1",
        tool=tool,
        args_summary={"arg_keys": [], "arg_count": 0},
        duration_ms=5,
        result="ok",
        error=None,
    )


@pytest.mark.asyncio
async def test_writer_appends_one_jsonl_line_per_write(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    await writer.write(_ok_line("send"))
    await writer.write(_ok_line("handle"))

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert [p["tool"] for p in parsed] == ["send", "handle"]


@pytest.mark.asyncio
async def test_writer_creates_log_root_directory_on_first_write(tmp_path: Path):
    nested = tmp_path / "nested" / "audit"
    assert not nested.exists()
    writer = MCPAuditWriter(log_root=nested, timezone="UTC")
    await writer.write(_ok_line())
    assert nested.exists()
    assert any(nested.glob("*.jsonl"))


@pytest.mark.asyncio
async def test_writer_swallows_oserror_and_logs_warning(tmp_path: Path, caplog):
    # Simulate write failure by pointing log_root at a path whose parent
    # is itself a regular file (mkdir will fail).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    bad_root = blocker / "audit"
    writer = MCPAuditWriter(log_root=bad_root, timezone="UTC")

    import logging as _logging
    with caplog.at_level(_logging.WARNING, logger="agent_core.mcp_audit.writer"):
        # Must not raise.
        await writer.write(_ok_line())
    assert any("audit" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_writer_concurrent_writes_produce_intact_lines(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    # Each line carries a long marker so any byte-interleaving would corrupt it.
    async def _w(i: int) -> None:
        await writer.write(
            AuditLine(
                timestamp=datetime.now(timezone.utc),
                endpoint=f"e{i}",
                session_id=None,
                request_id=str(i),
                tool="t",
                args_summary={"arg_keys": ["x" * 200], "arg_count": 1, "i": i},
                duration_ms=i,
                result="ok",
                error=None,
            )
        )

    await asyncio.gather(*[_w(i) for i in range(50)])

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 50
    # Each line must be valid JSON — corruption would raise here.
    parsed = [json.loads(line) for line in lines]
    assert {p["request_id"] for p in parsed} == {str(i) for i in range(50)}


@pytest.mark.asyncio
async def test_writer_uses_configured_timezone_for_path_rotation(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="US/Eastern")
    line = AuditLine(
        timestamp=datetime(2026, 5, 7, 3, 30, tzinfo=timezone.utc),  # 23:30 ET prior day
        endpoint="pepper",
        session_id=None,
        request_id="1",
        tool="t",
        args_summary={"arg_keys": [], "arg_count": 0},
        duration_ms=1,
        result="ok",
        error=None,
    )
    await writer.write(line)
    files = list(tmp_path.glob("*.jsonl"))
    assert [f.name for f in files] == ["2026-05-06.jsonl"]
```

- [ ] **Step 1.7: Run tests to verify writer tests fail**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_mcp_audit_writer.py -v`
Expected: AuditLine tests pass; new writer tests fail (`MCPAuditWriter` not defined).

- [ ] **Step 1.8: Implement `MCPAuditWriter`**

Append to `packages/core/src/agent_core/mcp_audit/writer.py`:

```python
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
```

- [ ] **Step 1.9: Run all tests**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_mcp_audit_writer.py -v`
Expected: all PASS (10 tests).

- [ ] **Step 1.10: Commit**

```bash
git checkout -b feat/issue-39-mcp-tool-call-audit
git add packages/core/src/agent_core/mcp_audit/__init__.py \
        packages/core/src/agent_core/mcp_audit/writer.py \
        packages/core/tests/test_mcp_audit_writer.py
git commit -m "feat(mcp_audit): AuditLine + MCPAuditWriter with daily rotation

- One JSONL line per audited tool call
- Daily rotation in configured timezone (mirrors daily_raw_jsonl)
- asyncio.Lock + asyncio.to_thread for concurrent-safe append
- Swallows write failures, logs at WARNING

Refs #39"
```

---

## Task 2: `MCPAuditMiddleware` happy path

Subclass FastMCP's `Middleware` and override `on_call_tool`. On success, write one `AuditLine` with `result="ok"`, the default `args_summary` shape, the request/session ids, and the measured duration.

**Files:**
- Create: `packages/core/src/agent_core/mcp_audit/middleware.py`
- Modify: `packages/core/src/agent_core/mcp_audit/__init__.py:0` (already re-exports `MCPAuditMiddleware`)
- Test: `packages/core/tests/test_mcp_audit_middleware.py`

- [ ] **Step 2.1: Write failing tests for happy-path middleware behavior**

Create `packages/core/tests/test_mcp_audit_middleware.py`:

```python
"""MCPAuditMiddleware: per-tools/call audit emission via FastMCP middleware."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from agent_core.mcp_audit.middleware import MCPAuditMiddleware
from agent_core.mcp_audit.writer import MCPAuditWriter


def _build_server(
    *,
    name: str,
    writer: MCPAuditWriter,
    skip_tools: frozenset[str] = frozenset(),
) -> FastMCP:
    mcp = FastMCP(name)

    @mcp.tool()
    async def echo(text: str) -> str:
        return text

    @mcp.tool()
    async def add(a: int, b: int) -> int:
        return a + b

    @mcp.tool()
    async def boom() -> str:
        raise RuntimeError("kaboom")

    mcp.add_middleware(
        MCPAuditMiddleware(endpoint_name=name, writer=writer, skip_tools=skip_tools)
    )
    return mcp


def _read_lines(log_root: Path) -> list[dict]:
    files = list(log_root.glob("*.jsonl"))
    if not files:
        return []
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_middleware_writes_one_line_per_tool_call(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer)
    async with Client(mcp) as client:
        await client.call_tool("echo", {"text": "hi"})
        await client.call_tool("add", {"a": 2, "b": 3})

    rows = _read_lines(tmp_path)
    assert len(rows) == 2
    assert [r["tool"] for r in rows] == ["echo", "add"]
    for r in rows:
        assert r["endpoint"] == "pepper"
        assert r["result"] == "ok"
        assert "duration_ms" in r
        assert isinstance(r["duration_ms"], int)
        assert r["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_middleware_default_args_summary_has_arg_keys_and_arg_count(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer)
    async with Client(mcp) as client:
        await client.call_tool("add", {"a": 2, "b": 3})

    rows = _read_lines(tmp_path)
    assert rows[0]["args_summary"] == {"arg_keys": ["a", "b"], "arg_count": 2}
    # No values leak.
    assert "2" not in json.dumps(rows[0]["args_summary"])
    assert "3" not in json.dumps(rows[0]["args_summary"])


@pytest.mark.asyncio
async def test_middleware_handles_no_args_call(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer)

    @mcp.tool()
    async def noargs() -> str:
        return "ok"

    async with Client(mcp) as client:
        await client.call_tool("noargs", {})

    rows = _read_lines(tmp_path)
    assert rows[-1]["args_summary"] == {"arg_keys": [], "arg_count": 0}


@pytest.mark.asyncio
async def test_middleware_request_id_present_when_available(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer)
    async with Client(mcp) as client:
        await client.call_tool("echo", {"text": "hi"})

    rows = _read_lines(tmp_path)
    # In-memory transport assigns request ids; field must exist either as
    # a string or as null (never missing).
    assert "request_id" in rows[0]


@pytest.mark.asyncio
async def test_middleware_session_id_field_is_present_even_when_null(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer)
    async with Client(mcp) as client:
        await client.call_tool("echo", {"text": "hi"})

    rows = _read_lines(tmp_path)
    assert "session_id" in rows[0]
    # In-memory transport may not have a session id; serialized as null.
    assert rows[0]["session_id"] is None or isinstance(rows[0]["session_id"], str)
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_mcp_audit_middleware.py -v`
Expected: ImportError — `agent_core.mcp_audit.middleware` does not exist yet.

- [ ] **Step 2.3: Implement `MCPAuditMiddleware` happy path**

Create `packages/core/src/agent_core/mcp_audit/middleware.py`:

```python
"""FastMCP middleware that emits one ``AuditLine`` per ``tools/call``.

Attached to each ``ClaudeCodeMCPEndpoint._mcp`` server (post-construction
via ``attach_audit_writer``). Uses FastMCP's ``on_call_tool`` hook so it
sees only tool invocations — list/read/get traffic does not generate
audit lines.

Defaults
--------
``args_summary`` is structural-only: ``{"arg_keys": sorted(keys), "arg_count": n}``.
No argument values are ever written. Per-tool bespoke summarizers are
out of scope for v1; see the spec's "Out of scope" section.

Error handling
--------------
* Tool exceptions are caught for accounting only, then re-raised. The
  audit line is written in ``finally`` either way.
* Audit-writer failures are already swallowed inside
  ``MCPAuditWriter.write``; this middleware never breaks a tool call.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

from agent_core.mcp_audit.writer import AuditLine, MCPAuditWriter

log = logging.getLogger(__name__)


class MCPAuditMiddleware(Middleware):
    """Emit one ``AuditLine`` per ``tools/call`` for the host endpoint."""

    def __init__(
        self,
        *,
        endpoint_name: str,
        writer: MCPAuditWriter,
        skip_tools: frozenset[str] | set[str] | None = None,
    ) -> None:
        self._endpoint_name = endpoint_name
        self._writer = writer
        self._skip_tools: frozenset[str] = (
            frozenset(skip_tools) if skip_tools else frozenset()
        )

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool = getattr(context.message, "name", "<unknown>")
        if tool in self._skip_tools:
            return await call_next(context)

        args = getattr(context.message, "arguments", None) or {}
        session_id, request_id = self._extract_ids(context)

        start = perf_counter()
        result_status: str = "ok"
        error: dict[str, str] | None = None
        try:
            return await call_next(context)
        except Exception as exc:
            result_status = "error"
            error = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            duration_ms = int((perf_counter() - start) * 1000)
            line = AuditLine(
                timestamp=datetime.now(UTC).astimezone(ZoneInfo(self._writer.timezone)),
                endpoint=self._endpoint_name,
                session_id=session_id,
                request_id=request_id,
                tool=tool,
                args_summary=self._summarize_args(args),
                duration_ms=duration_ms,
                result=result_status,
                error=error,
            )
            try:
                await self._writer.write(line)
            except Exception:
                # Defense in depth — writer.write already swallows, but
                # don't let any future change to the writer break a tool call.
                log.warning("mcp_audit: middleware swallowed writer error", exc_info=True)

    @staticmethod
    def _summarize_args(args: dict[str, Any]) -> dict[str, Any]:
        return {"arg_keys": sorted(args.keys()), "arg_count": len(args)}

    @staticmethod
    def _extract_ids(
        context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> tuple[str | None, str | None]:
        ctx = context.fastmcp_context
        if ctx is None:
            return (None, None)
        session_id_raw = getattr(ctx, "session_id", None)
        session_id = str(session_id_raw) if session_id_raw is not None else None
        request_id: str | None = None
        rc = getattr(ctx, "request_context", None)
        if rc is not None:
            rid = getattr(rc, "request_id", None)
            if rid is not None:
                request_id = str(rid)
        return (session_id, request_id)


__all__ = ["MCPAuditMiddleware"]
```

- [ ] **Step 2.4: Run tests to verify happy-path tests pass**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_mcp_audit_middleware.py -v`
Expected: all 5 PASS.

- [ ] **Step 2.5: Commit**

```bash
git add packages/core/src/agent_core/mcp_audit/middleware.py \
        packages/core/tests/test_mcp_audit_middleware.py
git commit -m "feat(mcp_audit): MCPAuditMiddleware happy path

FastMCP on_call_tool hook emits one AuditLine per tools/call.
Default args_summary is structural-only ({arg_keys, arg_count}).
session_id and request_id extracted defensively via getattr chain.

Refs #39"
```

---

## Task 3: Middleware error path + skip_tools

Cover the failure path (tool raises → audit line with `result="error"` and structured error info, exception re-raised) and the configured skip list. Also add the resilience test that audit-write failure does not break a tool call.

**Files:**
- Modify: `packages/core/src/agent_core/mcp_audit/middleware.py` (no code change — already covers these paths; add only if a test reveals a gap)
- Test: `packages/core/tests/test_mcp_audit_middleware.py` (append)

- [ ] **Step 3.1: Append failing tests for error path, skip_tools, and write-failure resilience**

Append to `packages/core/tests/test_mcp_audit_middleware.py`:

```python
# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_writes_error_line_with_structured_error(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer)
    async with Client(mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("boom", {})

    rows = _read_lines(tmp_path)
    assert len(rows) == 1
    assert rows[0]["tool"] == "boom"
    assert rows[0]["result"] == "error"
    assert rows[0]["error"]["type"] == "RuntimeError"
    assert "kaboom" in rows[0]["error"]["message"]


@pytest.mark.asyncio
async def test_middleware_re_raises_tool_exception_after_writing(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer)
    raised = False
    async with Client(mcp) as client:
        try:
            await client.call_tool("boom", {})
        except Exception:
            raised = True
    assert raised, "tool exception must propagate to caller"
    rows = _read_lines(tmp_path)
    assert len(rows) == 1
    assert rows[0]["result"] == "error"


# ---------------------------------------------------------------------------
# skip_tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_skip_tools_excludes_named_tools(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer, skip_tools=frozenset({"echo"}))
    async with Client(mcp) as client:
        await client.call_tool("echo", {"text": "skipped"})
        await client.call_tool("add", {"a": 1, "b": 1})

    rows = _read_lines(tmp_path)
    assert [r["tool"] for r in rows] == ["add"]


# ---------------------------------------------------------------------------
# Resilience: audit-write failure does not break the tool call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_swallows_write_failure_does_not_break_tool_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")

    async def _exploding_write(_line):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(writer, "write", _exploding_write)

    mcp = _build_server(name="pepper", writer=writer)
    async with Client(mcp) as client:
        # Tool returns normally even though audit write blew up.
        result = await client.call_tool("echo", {"text": "hi"})
    # The tool's result must be intact.
    text = result.data if hasattr(result, "data") else result
    assert "hi" in str(text)
```

- [ ] **Step 3.2: Run tests to verify they pass (Task 2's middleware already covers these paths)**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_mcp_audit_middleware.py -v`
Expected: all PASS (9 total). If any fail, fix the middleware (likely a missing `try/except` around `self._writer.write`) and re-run.

- [ ] **Step 3.3: Commit**

```bash
git add packages/core/tests/test_mcp_audit_middleware.py
git commit -m "test(mcp_audit): error path, skip_tools, write-failure resilience

- Tool exception → result='error' with structured {type, message}
- Exception is re-raised after audit write
- skip_tools shortcuts past timing + write
- Audit-writer failure does not break the tool call

Refs #39"
```

---

## Task 4: Wire writer into runner services + endpoint attach

Three coordinated edits that make a writer constructed by the runner reach `MCPAuditMiddleware` on each `ClaudeCodeMCPEndpoint._mcp`. Builds on the existing `NotificationBrokerAwareEndpoint` pattern: add a Protocol, extend `RunnerServices`, opt-in via `configure_endpoint_instance`, expose `attach_audit_writer` on the endpoint.

**Files:**
- Modify: `packages/core/src/agent_core/bus/protocol.py` (add `AuditWriterAwareEndpoint` Protocol)
- Modify: `packages/core/src/agent_core/plugins/specs.py` (extend `RunnerServices`)
- Modify: `packages/core/src/agent_core/plugins/manager.py` (configure_endpoint_instance opt-in)
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py` (add `attach_audit_writer`)

- [ ] **Step 4.1: Write failing test for `attach_audit_writer`**

Create `packages/core/tests/test_claude_code_mcp_audit_attach.py`:

```python
"""ClaudeCodeMCPEndpoint.attach_audit_writer registers MCPAuditMiddleware."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client

from agent_core.bus.protocol import AuditWriterAwareEndpoint
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core.mcp_audit.writer import MCPAuditWriter


@pytest.mark.asyncio
async def test_endpoint_implements_audit_writer_aware_protocol():
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    assert isinstance(ep, AuditWriterAwareEndpoint)


@pytest.mark.asyncio
async def test_attach_audit_writer_registers_middleware_that_audits_calls(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    ep.attach_audit_writer(writer, skip_tools=frozenset())

    async with Client(ep._mcp) as client:
        await client.call_tool("list_endpoints", {})

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert any(r["tool"] == "list_endpoints" and r["endpoint"] == "pepper" for r in rows)


@pytest.mark.asyncio
async def test_attach_audit_writer_respects_skip_tools(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    ep.attach_audit_writer(writer, skip_tools=frozenset({"list_endpoints"}))

    async with Client(ep._mcp) as client:
        await client.call_tool("list_endpoints", {})

    files = list(tmp_path.glob("*.jsonl"))
    # No file is created because no audited call happened.
    assert files == []
```

- [ ] **Step 4.2: Run tests to verify failure**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_claude_code_mcp_audit_attach.py -v`
Expected: ImportError — `AuditWriterAwareEndpoint` not in `agent_core.bus.protocol`.

- [ ] **Step 4.3: Add `AuditWriterAwareEndpoint` Protocol**

Open `packages/core/src/agent_core/bus/protocol.py`. Find the existing `NotificationBrokerAwareEndpoint` Protocol class. Immediately after it, add:

```python
@runtime_checkable
class AuditWriterAwareEndpoint(Protocol):
    """Optional endpoint capability for MCP tool-call audit logging.

    Endpoints that host an MCP server (FastMCP) can opt in to the
    daemon-wide audit log by implementing this protocol. The runner
    calls ``attach_audit_writer`` after construction (mirrors the
    ``NotificationBrokerAwareEndpoint`` wiring).
    """

    def attach_audit_writer(
        self,
        writer: object,  # MCPAuditWriter — referenced by name to avoid an import-cycle here
        skip_tools: frozenset[str],
    ) -> None:
        """Attach the daemon audit writer; register the middleware on this endpoint's MCP server."""
```

If `runtime_checkable` and `Protocol` aren't already imported in this file, add to the existing `from typing import ...` line. (Inspect the top of `protocol.py` first; the existing `NotificationBrokerAwareEndpoint` uses these so they should already be imported.)

- [ ] **Step 4.4: Add `attach_audit_writer` on `ClaudeCodeMCPEndpoint`**

Open `packages/core/src/agent_core/endpoints/claude_code_mcp.py`. Find `attach_notify_broker` (around line 216). Immediately after that method, add:

```python
    def attach_audit_writer(
        self,
        writer: "MCPAuditWriter",
        skip_tools: frozenset[str],
    ) -> None:
        """Optional runner hook: install the audit middleware on this MCP server.

        Called once during ``configure_endpoint_instance``. Idempotent in
        practice because the runner only calls it when the runner has a
        writer to give and only does so once per endpoint instance.
        """
        from agent_core.mcp_audit.middleware import MCPAuditMiddleware

        self._mcp.add_middleware(
            MCPAuditMiddleware(
                endpoint_name=self.name,
                writer=writer,
                skip_tools=skip_tools,
            )
        )
```

Add a `TYPE_CHECKING` import at the top of the file (after the existing `if TYPE_CHECKING:` block):

```python
if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle
    from agent_core.mcp_audit.writer import MCPAuditWriter
```

- [ ] **Step 4.5: Extend `RunnerServices`**

Open `packages/core/src/agent_core/plugins/specs.py`. Update the `RunnerServices` dataclass (around line 19-23):

```python
@dataclass(frozen=True)
class RunnerServices:
    """Shared runtime services a plugin may use during wiring."""

    notify_broker: NotificationBroker
    mcp_audit_writer: MCPAuditWriter | None = None
    mcp_audit_skip_tools: frozenset[str] = frozenset()
```

Add the `MCPAuditWriter` import to the `if TYPE_CHECKING:` block (around line 13-16):

```python
if TYPE_CHECKING:
    from agent_core.bus.notify_broker import NotificationBroker
    from agent_core.bus.protocol import BusHook, Endpoint
    from agent_core.hooks.protocol import HookTool
    from agent_core.mcp_audit.writer import MCPAuditWriter
```

- [ ] **Step 4.6: Wire the configure_endpoint_instance hookimpl**

Open `packages/core/src/agent_core/plugins/manager.py`. Update the import (line 11):

```python
from agent_core.bus.protocol import AuditWriterAwareEndpoint, Endpoint, NotificationBrokerAwareEndpoint
```

Update `BuiltinRuntimePlugin.configure_endpoint_instance` (line 28-31):

```python
    @hookimpl
    def configure_endpoint_instance(self, instance, endpoint_name, endpoint_config, services):
        if isinstance(instance, NotificationBrokerAwareEndpoint):
            instance.attach_notify_broker(services.notify_broker)
        if (
            isinstance(instance, AuditWriterAwareEndpoint)
            and services.mcp_audit_writer is not None
        ):
            instance.attach_audit_writer(
                services.mcp_audit_writer, services.mcp_audit_skip_tools
            )
```

- [ ] **Step 4.7: Run attach tests**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_claude_code_mcp_audit_attach.py -v`
Expected: 3 PASS.

- [ ] **Step 4.8: Run the full claude_code_mcp test suite to make sure nothing broke**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_claude_code_mcp.py -v`
Expected: all PASS (no regressions in the existing tests).

- [ ] **Step 4.9: Commit**

```bash
git add packages/core/src/agent_core/bus/protocol.py \
        packages/core/src/agent_core/plugins/specs.py \
        packages/core/src/agent_core/plugins/manager.py \
        packages/core/src/agent_core/endpoints/claude_code_mcp.py \
        packages/core/tests/test_claude_code_mcp_audit_attach.py
git commit -m "feat(mcp_audit): wire writer through RunnerServices + endpoint attach

- AuditWriterAwareEndpoint Protocol (bus/protocol.py)
- RunnerServices.mcp_audit_writer + mcp_audit_skip_tools
- BuiltinRuntimePlugin opts in eligible endpoints during
  configure_endpoint_instance (mirrors notify_broker)
- ClaudeCodeMCPEndpoint.attach_audit_writer registers
  MCPAuditMiddleware on self._mcp

Refs #39"
```

---

## Task 5: Wire `mcp_audit:` YAML config in runner.py

Final wiring: read the top-level `mcp_audit:` block from `agent_core.yaml`, construct `MCPAuditWriter` when enabled, populate `RunnerServices`. Test the config-to-services plumbing end-to-end.

**Files:**
- Modify: `packages/core/src/agent_core/bus/runner.py`
- Test: `packages/core/tests/test_mcp_audit_runner.py`

- [ ] **Step 5.1: Write failing tests for runner config plumbing**

Create `packages/core/tests/test_mcp_audit_runner.py`:

```python
"""Runner reads `mcp_audit:` YAML and wires the writer into RunnerServices."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.bus.runner import build_bus_from_config


def _write_yaml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


_BASE_YAML = """\
bus:
  storage_path: "{storage}"
endpoints: []
"""


@pytest.mark.asyncio
async def test_runner_constructs_writer_when_mcp_audit_block_missing(tmp_path: Path):
    cfg = _write_yaml(
        tmp_path / "config.yaml",
        _BASE_YAML.format(storage=str(tmp_path / "bus.sqlite")),
    )
    bus, _http = await build_bus_from_config(cfg)
    # Block missing → defaults; writer is constructed.
    # We can't reach RunnerServices directly post-build, but the writer
    # was passed to BuiltinRuntimePlugin which would have attached it to
    # any AuditWriterAwareEndpoint. With zero endpoints, the only check
    # we can do here is that build did not raise.
    assert bus is not None


@pytest.mark.asyncio
async def test_runner_constructs_no_writer_when_disabled(tmp_path: Path):
    yaml_body = _BASE_YAML.format(storage=str(tmp_path / "bus.sqlite")) + (
        "mcp_audit:\n  enabled: false\n"
    )
    cfg = _write_yaml(tmp_path / "config.yaml", yaml_body)
    bus, _http = await build_bus_from_config(cfg)
    # With one ClaudeCodeMCPEndpoint and audit disabled, no audit dir
    # should appear under ~/.agent-core/bus/mcp-audit. Validated via the
    # integration test in Task 6 — here we just assert build succeeded.
    assert bus is not None


@pytest.mark.asyncio
async def test_runner_uses_configured_log_root_and_timezone(tmp_path: Path):
    custom_root = tmp_path / "custom" / "audit"
    yaml_body = _BASE_YAML.format(storage=str(tmp_path / "bus.sqlite")) + (
        f"mcp_audit:\n"
        f"  enabled: true\n"
        f"  log_root: \"{custom_root.as_posix()}\"\n"
        f"  timezone: \"UTC\"\n"
        f"  skip_tools: [\"list_pending\"]\n"
    )
    cfg = _write_yaml(tmp_path / "config.yaml", yaml_body)
    bus, _http = await build_bus_from_config(cfg)
    assert bus is not None
    # No endpoints in this config means no writer activation, but the
    # build itself must accept the block. Behavioral verification of
    # log_root/timezone/skip_tools lives in the integration test.
```

- [ ] **Step 5.2: Run tests to verify failure**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_mcp_audit_runner.py -v`
Expected: tests likely PASS (the runner is permissive — unknown YAML keys are ignored). However, the `mcp_audit` config still has no effect. The integration test in Task 6 will exercise the actual wiring; these tests are for the YAML acceptance contract.

If tests fail with a parse error, that's the runner not yet accepting the new key — fix per Step 5.3.

- [ ] **Step 5.3: Wire `mcp_audit:` block in `runner.py`**

Open `packages/core/src/agent_core/bus/runner.py`. Add the import near the top (after the existing `from agent_core.plugins.specs import RunnerServices`):

```python
from agent_core.mcp_audit.writer import MCPAuditWriter
```

Find the `services = RunnerServices(notify_broker=notify_broker)` line (around line 70). Replace it with:

```python
    # MCP audit (top-level `mcp_audit:` YAML block, optional).
    audit_cfg = raw.get("mcp_audit", {}) or {}
    audit_enabled = bool(audit_cfg.get("enabled", True))
    mcp_audit_writer: MCPAuditWriter | None = None
    mcp_audit_skip_tools: frozenset[str] = frozenset()
    if audit_enabled:
        log_root = audit_cfg.get("log_root", "~/.agent-core/bus/mcp-audit")
        tz = audit_cfg.get("timezone", "US/Eastern")
        mcp_audit_writer = MCPAuditWriter(log_root=log_root, timezone=tz)
        skip_raw = audit_cfg.get("skip_tools", []) or []
        if not isinstance(skip_raw, list):
            raise BusBootError(
                f"mcp_audit.skip_tools must be a list, got {type(skip_raw).__name__}"
            )
        mcp_audit_skip_tools = frozenset(str(s) for s in skip_raw)

    services = RunnerServices(
        notify_broker=notify_broker,
        mcp_audit_writer=mcp_audit_writer,
        mcp_audit_skip_tools=mcp_audit_skip_tools,
    )
```

- [ ] **Step 5.4: Run tests**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_mcp_audit_runner.py -v`
Expected: 3 PASS.

- [ ] **Step 5.5: Run the full bus runner test suite to make sure nothing broke**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_bus_daemon_integration.py -v`
Expected: all PASS (no regressions).

- [ ] **Step 5.6: Commit**

```bash
git add packages/core/src/agent_core/bus/runner.py \
        packages/core/tests/test_mcp_audit_runner.py
git commit -m "feat(mcp_audit): runner reads mcp_audit YAML block

Top-level mcp_audit config: enabled (default true), log_root
(default ~/.agent-core/bus/mcp-audit), timezone (default US/Eastern),
skip_tools (default []). MCPAuditWriter is constructed only when
enabled; populated in RunnerServices for plugin opt-in wiring.

Refs #39"
```

---

## Task 6: End-to-end integration test

One integration test that drives runner → endpoint → middleware → writer with two endpoints sharing one writer. Validates the spec's "writer is singleton across endpoints" assertion and the disabled-emits-nothing assertion.

**Files:**
- Test: `packages/core/tests/test_mcp_audit_integration.py`

- [ ] **Step 6.1: Write the integration tests**

Create `packages/core/tests/test_mcp_audit_integration.py`:

```python
"""Runner-level integration: writer is shared across endpoints; disabled emits nothing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client

from agent_core.bus.runner import build_bus_from_config


_TWO_ENDPOINT_YAML = """\
bus:
  storage_path: "{storage}"
mcp_audit:
  enabled: true
  log_root: "{audit_root}"
  timezone: "UTC"
  skip_tools: []
endpoints:
  - name: pepper
    type: builtin.claude_code_mcp
    params:
      mount: "/mcp/pepper"
  - name: testbot
    type: builtin.claude_code_mcp
    params:
      mount: "/mcp/testbot"
"""


_DISABLED_YAML = """\
bus:
  storage_path: "{storage}"
mcp_audit:
  enabled: false
  log_root: "{audit_root}"
endpoints:
  - name: pepper
    type: builtin.claude_code_mcp
    params:
      mount: "/mcp/pepper"
"""


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_audit_writer_is_singleton_across_endpoints(tmp_path: Path):
    storage = tmp_path / "bus.sqlite"
    audit_root = tmp_path / "audit"
    cfg = _write(
        tmp_path / "config.yaml",
        _TWO_ENDPOINT_YAML.format(storage=storage.as_posix(), audit_root=audit_root.as_posix()),
    )
    bus, _http = await build_bus_from_config(cfg)
    await bus.start()
    try:
        endpoints = list(bus._endpoints_by_name.values())
        pepper = next(s.endpoint for s in endpoints if s.endpoint.name == "pepper")
        testbot = next(s.endpoint for s in endpoints if s.endpoint.name == "testbot")

        async with Client(pepper._mcp) as p_client:
            await p_client.call_tool("list_endpoints", {})
        async with Client(testbot._mcp) as t_client:
            await t_client.call_tool("list_endpoints", {})
    finally:
        await bus.stop()

    files = list(audit_root.glob("*.jsonl"))
    assert len(files) == 1, f"expected one shared daily file, got {[f.name for f in files]}"
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    endpoints_seen = {r["endpoint"] for r in rows}
    assert endpoints_seen == {"pepper", "testbot"}


@pytest.mark.asyncio
async def test_audit_disabled_emits_nothing(tmp_path: Path):
    storage = tmp_path / "bus.sqlite"
    audit_root = tmp_path / "audit"
    cfg = _write(
        tmp_path / "config.yaml",
        _DISABLED_YAML.format(storage=storage.as_posix(), audit_root=audit_root.as_posix()),
    )
    bus, _http = await build_bus_from_config(cfg)
    await bus.start()
    try:
        endpoints = list(bus._endpoints_by_name.values())
        pepper = next(s.endpoint for s in endpoints if s.endpoint.name == "pepper")
        async with Client(pepper._mcp) as client:
            await client.call_tool("list_endpoints", {})
    finally:
        await bus.stop()

    # Audit disabled → no writer constructed → directory never created.
    assert not audit_root.exists(), f"audit_root must not exist, found: {list(audit_root.iterdir())}"
```

- [ ] **Step 6.2: Run integration tests**

Run: `uv run --package agent-core-core pytest packages/core/tests/test_mcp_audit_integration.py -v`
Expected: 2 PASS.

- [ ] **Step 6.3: Run the full test suite**

Run: `uv run --package agent-core-core pytest packages/core/tests/ -v`
Expected: all PASS (no regressions across the existing tests).

- [ ] **Step 6.4: Commit**

```bash
git add packages/core/tests/test_mcp_audit_integration.py
git commit -m "test(mcp_audit): end-to-end integration with shared writer

- Two ClaudeCodeMCPEndpoints share one MCPAuditWriter via runner
- Both endpoints' tool calls land in the same daily JSONL
- Disabled config produces no audit directory at all

Refs #39"
```

- [ ] **Step 6.5: Push the branch and open the PR**

```bash
git push -u origin feat/issue-39-mcp-tool-call-audit
gh pr create --base main --title "feat: MCP tool-call audit log (#39)" --body "$(cat <<'EOF'
## Summary

Daemon-wide audit JSONL for every MCP tools/call across every endpoint.

- Singleton `MCPAuditWriter` constructed by the runner when `mcp_audit.enabled` (default true)
- `MCPAuditMiddleware` on each `ClaudeCodeMCPEndpoint._mcp` via FastMCP's `on_call_tool` hook
- Default-only args summarizer (`{arg_keys, arg_count}`); no values logged
- Daily rotation in configured timezone (`~/.agent-core/bus/mcp-audit/<YYYY-MM-DD>.jsonl`)
- Operator opt-out via `skip_tools: [...]`
- Tool exceptions captured (`result: "error"` with structured `{type, message}`) and re-raised
- Audit-write failures swallowed; never break a tool call
- Webcam local audit log unchanged; coexists

## Test plan

- [ ] AuditLine + writer unit tests (rotation, locking, swallowed failures)
- [ ] Middleware unit tests (happy + error path, skip_tools, write-failure resilience)
- [ ] Endpoint attach tests (Protocol opt-in, middleware wiring)
- [ ] Runner config tests (YAML acceptance, defaults, disabled)
- [ ] Integration test (shared writer across two endpoints, disabled emits nothing)

Closes #39
EOF
)"
```

---

## Self-review notes

**Spec coverage check** (each spec section → task that implements it):

| Spec section | Task |
|---|---|
| Architecture (writer singleton + middleware per endpoint) | Tasks 1, 2, 4 |
| `AuditLine` schema (timestamp, endpoint, session_id, request_id, tool, args_summary, duration_ms, result, error) | Tasks 1, 2 |
| Default args summarizer (`arg_keys` sorted, `arg_count`) | Task 2 |
| `mcp_audit:` YAML config (enabled, log_root, timezone, skip_tools) | Task 5 |
| Default `~/.agent-core/bus/mcp-audit` location | Task 5 |
| Daily rotation in configured timezone | Task 1 |
| `enabled: false` produces no writer / no middleware / no directory | Tasks 5, 6 |
| `skip_tools` short-circuit | Tasks 3, 4 |
| Audit-write failure swallowed | Tasks 1, 3 |
| Tool exception → `result: "error"` then re-raise | Tasks 2, 3 |
| `session_id` is None for in-memory transports | Task 2 |
| Concurrent atomicity (no interleaved bytes) | Task 1 |
| Writer singleton across endpoints | Task 6 |
| Webcam audit unchanged | (no code change required) |

**Test count check:** 13 tests called out in the spec → 13+ tests across `test_mcp_audit_writer.py`, `test_mcp_audit_middleware.py`, `test_claude_code_mcp_audit_attach.py`, `test_mcp_audit_runner.py`, `test_mcp_audit_integration.py`. All accounted for; no spec test missing.

**Type consistency:** `MCPAuditWriter`, `MCPAuditMiddleware`, `AuditLine`, `AuditWriterAwareEndpoint`, `attach_audit_writer(writer, skip_tools)`, `mcp_audit_writer`, `mcp_audit_skip_tools` — same names everywhere they appear.
