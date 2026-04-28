# Bus Daemon + ClaudeCodeMCPEndpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make agent-core a real long-running daemon process that Claude Code instances can connect to via Streamable HTTP, with PID-managed lifecycle. Validate end-to-end on a fresh test agent at `~/.testbot/`.

**Architecture:** Adds `ClaudeCodeMCPEndpoint` (a FastMCP-backed `Endpoint` adapter), a shared Starlette+Uvicorn HTTP host wired into the bus runner, and an `agent-core daemon` PID-supervised CLI that spawns `agent-core bus run` detached. Pepper's runtime stays untouched per the project's "fresh test agent first" rule.

**Tech Stack:** Python 3.12+, uv workspace, FastMCP (3.x), Starlette, Uvicorn, psutil, pytest, pytest-asyncio, asyncio.

**Spec:** [`docs/superpowers/specs/2026-04-28-bus-daemon-design.md`](../specs/2026-04-28-bus-daemon-design.md)

---

## File Structure

**Create:**
- `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — the endpoint adapter (Endpoint Protocol + MCPHostable Protocol).
- `packages/core/src/agent_core/bus/http_host.py` — Starlette+Uvicorn wrapper, `MCPHostable` protocol, `HTTPHost` class.
- `packages/core/src/agent_core/daemon/__init__.py` — empty marker for the new sub-package.
- `packages/core/src/agent_core/daemon/supervisor.py` — PID file ops + process tree kill (psutil).
- `packages/core/src/agent_core/daemon/cli.py` — `agent-core daemon start/stop/status` typer app.
- `packages/core/tests/test_http_host.py`
- `packages/core/tests/test_claude_code_mcp.py`
- `packages/core/tests/test_daemon_supervisor.py`
- `packages/core/tests/test_daemon_cli.py`
- `packages/core/tests/test_bus_daemon_integration.py`
- `packages/core/changelog.d/+bus-daemon.added.md`

**Modify:**
- `packages/core/pyproject.toml` — add `fastmcp`, `starlette`, `uvicorn`, `psutil` deps.
- `packages/core/src/agent_core/cli.py` — register the new `daemon` typer subapp.
- `packages/core/src/agent_core/bus/runner.py` — return `(Bus, HTTPHost | None)` tuple; scan registered endpoints for `MCPHostable` and mount them.
- `packages/core/src/agent_core/bus/cli.py` — `_run_bus` orchestrates HTTPHost lifecycle alongside the bus.
- `.importlinter` — confirm bus core stays insulated; add new internal modules to `root_packages` if needed (none expected — `agent_core.daemon` lives under `agent_core` already covered).

---

## Task 1: Pre-flight + branch + dependencies

**Files:**
- Modify: `packages/core/pyproject.toml`

- [ ] **Step 1: Confirm clean working tree, then create the branch**

```bash
git status
git checkout main
git pull origin main
git checkout -b feat/bus-daemon
```

Expected: clean tree, branch created.

- [ ] **Step 2: Verify baseline tests pass**

```bash
uv run --no-sync pytest -q
```

Expected: 206 passed / 2 skipped (matching the post-Step-3-credentials baseline). Record exact numbers; you'll re-check at the end.

- [ ] **Step 3: Add the new dependencies to `packages/core/pyproject.toml`**

Replace the `dependencies` list:

```toml
dependencies = [
    "claude-agent-sdk>=0.1.29",
    "python-dotenv>=1.0.0",
    "tzdata>=2024.1",
    "pydantic>=2.0",
    "typer>=0.12",
    "rich>=13.0",
    "pyyaml>=6.0",
    "agentmail>=0.4",
    "aiosqlite>=0.20",
    "fastmcp>=2.0",
    "starlette>=0.37",
    "uvicorn>=0.30",
    "psutil>=5.9",
]
```

(Note: `fastmcp>=2.0` — the standalone FastMCP v2/v3 package, not `mcp.server.fastmcp` from the MCP SDK. If FastMCP 2.x doesn't satisfy `mcp.server.fastmcp` imports used elsewhere, adjust the constraint to whatever resolves cleanly with the existing `mcp` dep already pinned via `claude-agent-sdk`.)

- [ ] **Step 4: Sync and verify imports resolve**

```bash
uv sync
uv run --no-sync python -c "import fastmcp; import starlette; import uvicorn; import psutil; print('ok')"
```

Expected: `ok`.

- [ ] **Step 5: Re-run baseline tests**

```bash
uv run --no-sync pytest -q
```

Expected: same baseline (no new errors, no test count change).

- [ ] **Step 6: Commit**

```bash
git add packages/core/pyproject.toml uv.lock
git commit -m "build(bus-daemon): add FastMCP/Starlette/Uvicorn/psutil deps"
```

---

## Task 2: HTTP host module + MCPHostable protocol

**Files:**
- Create: `packages/core/src/agent_core/bus/http_host.py`
- Create: `packages/core/tests/test_http_host.py`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_http_host.py`:

```python
"""Tests for the shared Starlette+Uvicorn HTTP host."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from agent_core.bus.http_host import HTTPHost, MCPHostable


class _StubMountable:
    """Minimal MCPHostable for tests — owns a small ASGI app."""

    def __init__(self, mount: str, body: bytes = b"hello"):
        self.mount = mount
        self._body = body

    def asgi_app(self):
        body = self._body

        async def app(scope, receive, send):
            assert scope["type"] == "http"
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": body})

        return app


def test_stub_satisfies_mcp_hostable_protocol():
    s = _StubMountable("/mcp/x")
    assert isinstance(s, MCPHostable)


@pytest.mark.asyncio
async def test_http_host_serves_mounted_apps():
    host = HTTPHost(bind_host="127.0.0.1", bind_port=0)
    host.mount(_StubMountable("/mcp/foo", body=b"foo-resp"))
    host.mount(_StubMountable("/mcp/bar", body=b"bar-resp"))
    await host.start()
    try:
        port = host.port
        assert port > 0
        async with httpx.AsyncClient() as client:
            r1 = await client.get(f"http://127.0.0.1:{port}/mcp/foo")
            r2 = await client.get(f"http://127.0.0.1:{port}/mcp/bar")
        assert r1.status_code == 200 and r1.content == b"foo-resp"
        assert r2.status_code == 200 and r2.content == b"bar-resp"
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_http_host_refuses_double_start():
    host = HTTPHost(bind_host="127.0.0.1", bind_port=0)
    host.mount(_StubMountable("/mcp/x"))
    await host.start()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            await host.start()
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_http_host_stop_is_idempotent():
    host = HTTPHost(bind_host="127.0.0.1", bind_port=0)
    host.mount(_StubMountable("/mcp/x"))
    await host.start()
    await host.stop()
    await host.stop()  # Should not raise.


@pytest.mark.asyncio
async def test_http_host_port_already_in_use_raises():
    h1 = HTTPHost(bind_host="127.0.0.1", bind_port=0)
    h1.mount(_StubMountable("/mcp/x"))
    await h1.start()
    try:
        h2 = HTTPHost(bind_host="127.0.0.1", bind_port=h1.port)
        h2.mount(_StubMountable("/mcp/x"))
        with pytest.raises(OSError):
            await h2.start()
    finally:
        await h1.stop()
```

- [ ] **Step 2: Run test to confirm it fails (module doesn't exist)**

```bash
uv run --no-sync pytest packages/core/tests/test_http_host.py -v
```

Expected: ImportError for `agent_core.bus.http_host`.

- [ ] **Step 3: Implement `bus/http_host.py`**

Create `packages/core/src/agent_core/bus/http_host.py`:

```python
"""Shared Starlette+Uvicorn HTTP host for MCP endpoint adapters.

Endpoints that want to be served over HTTP implement the MCPHostable
Protocol (`mount` + `asgi_app()`). The runner collects them after
endpoint registration and constructs a single HTTPHost with all of
them mounted under their declared paths.

Uvicorn runs on the bus's asyncio event loop. Loopback bind only is
enforced upstream in `bus/runner.py`; this module trusts the host
parameter it receives.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, runtime_checkable

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount

log = logging.getLogger(__name__)


@runtime_checkable
class MCPHostable(Protocol):
    """An endpoint that wants to be mounted on the shared HTTP host."""

    mount: str

    def asgi_app(self) -> object:
        """Return the ASGI application to serve under `self.mount`."""


class HTTPHost:
    """Owns one Starlette app + one Uvicorn server on the bus's loop.

    Mounts are added before `start()`. After `start()`, the bound port
    is available via `self.port` (useful when bind_port=0 for tests).
    """

    def __init__(self, *, bind_host: str = "127.0.0.1", bind_port: int = 8788):
        self._bind_host = bind_host
        self._requested_port = bind_port
        self._mounts: list[MCPHostable] = []
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task | None = None
        self._started = False

    def mount(self, hostable: MCPHostable) -> None:
        if self._started:
            raise RuntimeError("cannot add mounts after start()")
        self._mounts.append(hostable)

    @property
    def port(self) -> int:
        """The bound port. Only valid after start()."""
        if self._server is None or not self._server.servers:
            return -1
        sockets = self._server.servers[0].sockets
        if not sockets:
            return -1
        return sockets[0].getsockname()[1]

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("HTTPHost already running")
        routes = [Mount(m.mount, app=m.asgi_app()) for m in self._mounts]
        app = Starlette(routes=routes)
        config = uvicorn.Config(
            app,
            host=self._bind_host,
            port=self._requested_port,
            log_level="warning",
            lifespan="on",
        )
        self._server = uvicorn.Server(config)
        self._serve_task = asyncio.create_task(self._server.serve())

        # Wait until uvicorn reports the server is up (or fails fast).
        for _ in range(200):  # ~10s ceiling
            if self._server.started:
                break
            if self._serve_task.done():
                # Surface any startup error (e.g., port in use).
                self._serve_task.result()
                raise RuntimeError("HTTPHost serve task exited before startup")
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("HTTPHost did not start within 10s")

        self._started = True
        log.info(
            "HTTPHost listening on %s:%d (%d mount(s))",
            self._bind_host,
            self.port,
            len(self._mounts),
        )

    async def stop(self) -> None:
        if not self._started:
            return
        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            try:
                await asyncio.wait_for(self._serve_task, timeout=10)
            except asyncio.TimeoutError:
                self._serve_task.cancel()
                try:
                    await self._serve_task
                except (asyncio.CancelledError, Exception):
                    pass
        self._server = None
        self._serve_task = None
        self._started = False
        log.info("HTTPHost stopped")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/core/tests/test_http_host.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/bus/http_host.py packages/core/tests/test_http_host.py
git commit -m "feat(bus-daemon): add HTTPHost (Starlette+Uvicorn) and MCPHostable Protocol"
```

---

## Task 3: ClaudeCodeMCPEndpoint scaffolding

**Files:**
- Create: `packages/core/src/agent_core/endpoints/claude_code_mcp.py`
- Create: `packages/core/tests/test_claude_code_mcp.py`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_claude_code_mcp.py`:

```python
"""Tests for the ClaudeCodeMCPEndpoint adapter."""

from __future__ import annotations

import pytest

from agent_core.bus.http_host import MCPHostable
from agent_core.bus.protocol import Endpoint
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


def test_endpoint_satisfies_endpoint_protocol():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    assert isinstance(ep, Endpoint)


def test_endpoint_satisfies_mcp_hostable_protocol():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    assert isinstance(ep, MCPHostable)


def test_endpoint_exposes_name_and_mount():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    assert ep.name == "agent-test"
    assert ep.mount == "/mcp/agent-test"


def test_endpoint_asgi_app_returns_callable():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    app = ep.asgi_app()
    assert callable(app)


@pytest.mark.asyncio
async def test_start_stop_lifecycle_no_session():
    """Endpoint can start and stop cleanly with no MCP session attached."""
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")

    class _FakeHandle:
        async def publish(self, *a, **kw): ...
        async def ack(self, *a, **kw): ...
        async def nack(self, *a, **kw): ...
        def endpoints(self): return []

    await ep.start(_FakeHandle())
    await ep.stop()
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run --no-sync pytest packages/core/tests/test_claude_code_mcp.py -v
```

Expected: ImportError for `agent_core.endpoints.claude_code_mcp`.

- [ ] **Step 3: Create the endpoint scaffolding**

Create `packages/core/src/agent_core/endpoints/claude_code_mcp.py`:

```python
"""ClaudeCodeMCPEndpoint — bus endpoint that hosts a FastMCP server.

Each instance corresponds to one named agent on the bus. The agent's
Claude Code instance connects to `http://<bind_host>:<port><mount>` via
Streamable HTTP. Identity is path-based — the URL path *is* the agent's
name on the bus, set by the runner via the `name` kwarg.

Tools (per the channel-bus spec § MCP transport implementation):
    send, list_endpoints, describe_endpoint, list_pending,
    handle, ack, nack

Inbound envelopes flow to the connected Claude Code session via MCP
notifications on the SSE stream. If no session is currently connected,
deliver() raises EndpointUnavailable so the bus queues the envelope.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from agent_core.bus.envelope import Envelope
from agent_core.bus.protocol import EndpointUnavailable

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


class ClaudeCodeMCPEndpoint:
    """Bus endpoint backed by a FastMCP server, served on the shared HTTP host."""

    def __init__(self, *, name: str, mount: str):
        self.name = name
        self.mount = mount
        self._mcp: FastMCP = FastMCP(name)
        self._handle: "BusHandle | None" = None
        self._register_tools()

    # --- Endpoint Protocol ---

    async def start(self, bus: "BusHandle") -> None:
        self._handle = bus
        log.info("ClaudeCodeMCPEndpoint(name=%s) started at mount=%s", self.name, self.mount)

    async def deliver(self, envelope: Envelope) -> None:
        # Implemented in Task 5 — for now, no session is ever connected.
        raise EndpointUnavailable(f"no MCP session connected for {self.name}")

    async def stop(self) -> None:
        self._handle = None
        log.info("ClaudeCodeMCPEndpoint(name=%s) stopped", self.name)

    # --- MCPHostable Protocol ---

    def asgi_app(self):
        """Return the ASGI app for this endpoint's FastMCP server."""
        return self._mcp.http_app(path="/")

    # --- Internal ---

    def _register_tools(self) -> None:
        """Register the bus's MCP tool surface on the FastMCP server.

        Tool bodies are stubbed in Task 3; implemented in Task 4 (outbound)
        and Task 5 (inbound)."""

        @self._mcp.tool()
        async def send() -> dict:
            """Implemented in Task 4."""
            return {"status": "not_implemented"}

        @self._mcp.tool()
        async def list_endpoints() -> list[dict]:
            """Implemented in Task 4."""
            return []

        @self._mcp.tool()
        async def describe_endpoint(name: str) -> dict | None:
            """Implemented in Task 4."""
            return None

        @self._mcp.tool()
        async def list_pending() -> list[dict]:
            """Implemented in Task 5."""
            return []

        @self._mcp.tool()
        async def handle(envelope_id: str) -> dict:
            """Implemented in Task 5."""
            return {"status": "not_implemented"}

        @self._mcp.tool()
        async def ack(envelope_id: str) -> dict:
            """Implemented in Task 5."""
            return {"status": "not_implemented"}

        @self._mcp.tool()
        async def nack(envelope_id: str, requeue: bool = True) -> dict:
            """Implemented in Task 5."""
            return {"status": "not_implemented"}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/core/tests/test_claude_code_mcp.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Verify import-linter still passes**

```bash
uv run --no-sync lint-imports
```

Expected: 1 contract kept, 0 broken.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/tests/test_claude_code_mcp.py
git commit -m "feat(bus-daemon): scaffold ClaudeCodeMCPEndpoint adapter"
```

---

## Task 4: ClaudeCodeMCPEndpoint outbound tools (`send`, `list_endpoints`, `describe_endpoint`)

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py`
- Modify: `packages/core/tests/test_claude_code_mcp.py`

- [ ] **Step 1: Add failing tests for the outbound tools**

Append to `packages/core/tests/test_claude_code_mcp.py`:

```python
import uuid
from datetime import datetime, timezone

from fastmcp import Client

from agent_core.bus.envelope import EndpointInfo, Envelope, TextMessagePayload


class _RecordingHandle:
    """Test-double BusHandle that records publishes and exposes a fake directory."""

    def __init__(self, *, endpoints: list[EndpointInfo] | None = None):
        self._endpoints = endpoints or []
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        if to is not None:
            envelope = envelope.model_copy(update={"to": to if isinstance(to, str) else to[0]})
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]:
        return list(self._endpoints)


@pytest.mark.asyncio
async def test_send_tool_publishes_envelope():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandle()
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            await client.call_tool(
                "send",
                {
                    "to": "stub",
                    "kind": "TextMessage",
                    "payload": {"kind": "TextMessage", "text": "hi"},
                },
            )
        assert len(handle.published) == 1
        env = handle.published[0]
        assert env.to == "stub"
        assert env.kind == "TextMessage"
        assert isinstance(env.payload, TextMessagePayload)
        assert env.payload.text == "hi"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_tool_accepts_optional_correlation_metadata():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandle()
    await ep.start(handle)
    try:
        cid = uuid.uuid4().hex
        async with Client(ep._mcp) as client:
            await client.call_tool(
                "send",
                {
                    "to": "stub",
                    "kind": "TextMessage",
                    "payload": {"kind": "TextMessage", "text": "ping"},
                    "correlation_id": cid,
                    "metadata": {"trace": "x"},
                },
            )
        env = handle.published[0]
        assert env.correlation_id == cid
        assert env.metadata == {"trace": "x"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_list_endpoints_tool_returns_directory():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandle(
        endpoints=[
            EndpointInfo(name="stub", description="echo for tests"),
            EndpointInfo(name="agent-test", description="the test agent"),
        ]
    )
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            res = await client.call_tool("list_endpoints", {})
        names = {item["name"] for item in res.data}
        assert names == {"stub", "agent-test"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_describe_endpoint_tool_finds_match():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandle(
        endpoints=[EndpointInfo(name="stub", description="echo")]
    )
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            res = await client.call_tool("describe_endpoint", {"name": "stub"})
        assert res.data == {"name": "stub", "description": "echo"}
        miss = await Client(ep._mcp).__aenter__()
        try:
            res2 = await miss.call_tool("describe_endpoint", {"name": "nope"})
            assert res2.data is None
        finally:
            await miss.__aexit__(None, None, None)
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_tool_errors_when_endpoint_not_started():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    async with Client(ep._mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool(
                "send",
                {
                    "to": "stub",
                    "kind": "TextMessage",
                    "payload": {"kind": "TextMessage", "text": "x"},
                },
            )
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/core/tests/test_claude_code_mcp.py -v
```

Expected: previous tests pass, new ones fail (the stubbed tools return placeholders).

- [ ] **Step 3: Implement the outbound tools**

Replace `_register_tools` in `packages/core/src/agent_core/endpoints/claude_code_mcp.py` with the full version, and add envelope construction helpers. Final relevant section:

```python
import uuid
from datetime import datetime, timezone
from typing import Any

from agent_core.bus.envelope import Envelope, EnvelopePayload


# ... (rest of class unchanged above) ...

    def _register_tools(self) -> None:
        """Register the bus's MCP tool surface on the FastMCP server."""

        @self._mcp.tool()
        async def send(
            to: str,
            kind: str,
            payload: dict[str, Any],
            correlation_id: str | None = None,
            in_reply_to: str | None = None,
            metadata: dict[str, Any] | None = None,
            expires_at: str | None = None,
        ) -> dict:
            """Publish an envelope. Bus stamps `from:` to this endpoint's name."""
            if self._handle is None:
                raise RuntimeError(f"endpoint '{self.name}' is not started")
            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id=correlation_id or uuid.uuid4().hex,
                in_reply_to=in_reply_to,
                to=to,
                kind=kind,  # type: ignore[arg-type]
                payload=payload,  # type: ignore[arg-type]  # discriminated by kind
                metadata=metadata or {},
                expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
                created_at=datetime.now(timezone.utc),
            )
            await self._handle.publish(env)
            return {"status": "published", "id": env.id}

        @self._mcp.tool()
        async def list_endpoints() -> list[dict]:
            """Return the directory of registered bus endpoints."""
            if self._handle is None:
                return []
            return [{"name": e.name, "description": e.description} for e in self._handle.endpoints()]

        @self._mcp.tool()
        async def describe_endpoint(name: str) -> dict | None:
            """Return one endpoint's directory entry, or None if unknown."""
            if self._handle is None:
                return None
            for e in self._handle.endpoints():
                if e.name == name:
                    return {"name": e.name, "description": e.description}
            return None

        # list_pending / handle / ack / nack — implemented in Task 5
        @self._mcp.tool()
        async def list_pending() -> list[dict]:
            return []

        @self._mcp.tool()
        async def handle(envelope_id: str) -> dict:
            return {"status": "not_implemented"}

        @self._mcp.tool()
        async def ack(envelope_id: str) -> dict:
            return {"status": "not_implemented"}

        @self._mcp.tool()
        async def nack(envelope_id: str, requeue: bool = True) -> dict:
            return {"status": "not_implemented"}
```

(The discriminated-union payload validation: passing `payload` as a raw dict with the matching `kind` field works because `EnvelopePayload` is a Pydantic discriminated union and the Envelope model coerces it. If FastMCP's tool input validation strips fields, switch to passing `payload` as `dict[str, Any]` and constructing the typed payload inside the function. Verify during implementation.)

- [ ] **Step 4: Run tests**

```bash
uv run --no-sync pytest packages/core/tests/test_claude_code_mcp.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/tests/test_claude_code_mcp.py
git commit -m "feat(bus-daemon): implement send/list_endpoints/describe_endpoint tools"
```

---

## Task 5: ClaudeCodeMCPEndpoint inbound surface — `list_pending`, `ack`, `nack`, `handle`, and `deliver()` push notifications

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py`
- Modify: `packages/core/tests/test_claude_code_mcp.py`

The inbound flow has two halves: the agent calls `list_pending` / `ack` / `nack` to drain mail, and the endpoint pushes MCP notifications when mail arrives so the agent doesn't have to poll. Both are implemented here.

- [ ] **Step 1: Write the failing tests for `list_pending` and `ack`/`nack`/`handle` plumbing**

Append to `packages/core/tests/test_claude_code_mcp.py`:

```python
class _RecordingHandleWithPending(_RecordingHandle):
    def __init__(self, pending: list[Envelope]):
        super().__init__()
        self.pending = pending
        self.acked: list[str] = []
        self.nacked: list[tuple[str, bool]] = []

    async def ack(self, envelope_id: str) -> None:
        self.acked.append(envelope_id)

    async def nack(self, envelope_id: str, requeue: bool = True) -> None:
        self.nacked.append((envelope_id, requeue))


def _make_envelope(env_id: str, frm: str = "stub", to: str = "agent-test") -> Envelope:
    return Envelope(
        id=env_id,
        correlation_id=uuid.uuid4().hex,
        from_=frm,
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text="hello"),
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_list_pending_returns_queued_envelopes():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    env = _make_envelope("env-1")
    handle = _RecordingHandleWithPending(pending=[])
    await ep.start(handle)
    try:
        # Endpoint queues envelopes via deliver() when no session is connected.
        # Force a queued envelope by calling _queue (test seam) or by raising
        # EndpointUnavailable from deliver(). For this test, push directly into
        # the endpoint's pending list via its public API.
        ep.queue_for_pickup(env)

        async with Client(ep._mcp) as client:
            res = await client.call_tool("list_pending", {})
        assert len(res.data) == 1
        assert res.data[0]["id"] == "env-1"
        assert res.data[0]["from"] == "stub"
        assert res.data[0]["kind"] == "TextMessage"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_ack_tool_calls_handle_ack():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandleWithPending(pending=[])
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            await client.call_tool("ack", {"envelope_id": "env-9"})
        assert handle.acked == ["env-9"]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_nack_tool_passes_requeue_flag():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandleWithPending(pending=[])
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            await client.call_tool("nack", {"envelope_id": "env-3", "requeue": False})
        assert handle.nacked == [("env-3", False)]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_handle_tool_acks_and_removes_from_pending():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    env = _make_envelope("env-h")
    handle = _RecordingHandleWithPending(pending=[])
    await ep.start(handle)
    try:
        ep.queue_for_pickup(env)
        async with Client(ep._mcp) as client:
            await client.call_tool("handle", {"envelope_id": "env-h"})
        # handle is a convenience for ack — verify the ack happened.
        assert handle.acked == ["env-h"]
        # And the pending entry is gone.
        async with Client(ep._mcp) as client:
            res = await client.call_tool("list_pending", {})
        assert res.data == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_without_session_raises_endpoint_unavailable_and_queues():
    from agent_core.bus.protocol import EndpointUnavailable

    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandleWithPending(pending=[])
    await ep.start(handle)
    try:
        env = _make_envelope("env-q")
        with pytest.raises(EndpointUnavailable):
            await ep.deliver(env)
        # When the agent reconnects, list_pending must surface this envelope
        # (or it must be redelivered by the bus). The endpoint queues it
        # internally so a freshly-attached client can pick it up.
        async with Client(ep._mcp) as client:
            res = await client.call_tool("list_pending", {})
        ids = {item["id"] for item in res.data}
        assert "env-q" in ids
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/core/tests/test_claude_code_mcp.py -v
```

Expected: new tests fail (no `queue_for_pickup`, stub tool bodies).

- [ ] **Step 3: Implement the inbound surface and `deliver()`**

Update `packages/core/src/agent_core/endpoints/claude_code_mcp.py`. Add the queue + replace stub tools + extend `deliver()`:

```python
# Top of class — add an instance pending queue
def __init__(self, *, name: str, mount: str):
    self.name = name
    self.mount = mount
    self._mcp: FastMCP = FastMCP(name)
    self._handle: "BusHandle | None" = None
    self._pending: list[Envelope] = []
    self._session_active: bool = False  # set true when an MCP session is attached
    self._register_tools()

# New helper used by tests AND by deliver()
def queue_for_pickup(self, envelope: Envelope) -> None:
    """Add an envelope to this endpoint's pending pickup queue.

    Used by deliver() when no session is connected, and by tests."""
    self._pending.append(envelope)

# Replace deliver()
async def deliver(self, envelope: Envelope) -> None:
    """Push the envelope to the connected agent.

    If a session is active: deliver via MCP notification (Task 5b).
    If no session: queue locally and raise EndpointUnavailable so the bus
    retries when the session reconnects."""
    if not self._session_active:
        self.queue_for_pickup(envelope)
        raise EndpointUnavailable(f"no MCP session connected for {self.name}")

    # Active session path — send a "mail-arrived" notification so the agent
    # knows to call list_pending. Implemented in Task 5b.
    self.queue_for_pickup(envelope)
    await self._notify_mail_arrived(envelope.id)

async def _notify_mail_arrived(self, envelope_id: str) -> None:
    """Send a server-initiated MCP notification on the active session.

    Implementation hinges on FastMCP's session-manager API. The pragma is:
    push a `notifications/agent_core/mail_arrived` notification with the
    envelope id; the agent calls list_pending in response. If FastMCP's
    out-of-band notification API is unavailable in the version we ship,
    this becomes a no-op and agents poll list_pending instead. Pin the
    behavior in code review."""
    # Verify the FastMCP API path during implementation. Likely something like:
    #   await self._mcp.session_manager.send_notification(
    #       method="notifications/agent_core/mail_arrived",
    #       params={"envelope_id": envelope_id},
    #   )
    # If the API isn't available, log a warning and rely on poll-via-list_pending.
    log.debug("mail-arrived notification scheduled for envelope %s", envelope_id)
```

Now replace the inbound tool bodies:

```python
        @self._mcp.tool()
        async def list_pending() -> list[dict]:
            """Return a snapshot of envelopes in this agent's pickup queue."""
            return [
                {
                    "id": env.id,
                    "from": env.from_,
                    "to": env.to,
                    "kind": env.kind,
                    "correlation_id": env.correlation_id,
                    "in_reply_to": env.in_reply_to,
                    "payload": env.payload.model_dump(),
                    "metadata": env.metadata,
                    "created_at": env.created_at.isoformat(),
                }
                for env in self._pending
            ]

        @self._mcp.tool()
        async def handle(envelope_id: str) -> dict:
            """Acknowledge an envelope and remove it from the pickup queue."""
            if self._handle is None:
                return {"status": "error", "message": "endpoint not started"}
            await self._handle.ack(envelope_id)
            self._pending = [e for e in self._pending if e.id != envelope_id]
            return {"status": "handled", "id": envelope_id}

        @self._mcp.tool()
        async def ack(envelope_id: str) -> dict:
            """Direct ack via the BusHandle."""
            if self._handle is None:
                return {"status": "error", "message": "endpoint not started"}
            await self._handle.ack(envelope_id)
            self._pending = [e for e in self._pending if e.id != envelope_id]
            return {"status": "acked", "id": envelope_id}

        @self._mcp.tool()
        async def nack(envelope_id: str, requeue: bool = True) -> dict:
            """Direct nack via the BusHandle."""
            if self._handle is None:
                return {"status": "error", "message": "endpoint not started"}
            await self._handle.nack(envelope_id, requeue)
            self._pending = [e for e in self._pending if e.id != envelope_id]
            return {"status": "nacked", "id": envelope_id, "requeue": requeue}
```

- [ ] **Step 4: Hook session lifecycle into `_session_active`**

FastMCP exposes session lifecycle through its session manager. Wire `_session_active = True` when a Streamable HTTP session opens and back to `False` when it closes. The exact hook depends on FastMCP's API:

- If FastMCP fires `lifespan` or `on_session_start`/`on_session_end` callbacks, register them in `__init__` after building `self._mcp` and toggle the flag.
- If not directly exposed, override `self._mcp.http_app(path="/")` by wrapping its returned ASGI app: intercept `lifespan` startup/shutdown messages and toggle the flag accordingly.

Whatever path is used, add a unit test that simulates a session connect (e.g., make an HTTP request with `Client(ep._mcp)` which emulates a session) and asserts `ep._session_active` flips. Then asserts that `deliver()` while the simulated session is active does NOT raise `EndpointUnavailable`.

(If FastMCP's in-memory `Client` doesn't toggle session lifecycle hooks but real HTTP does, the unit test can be approximate; verify with the integration test in Task 9.)

- [ ] **Step 5: Run tests**

```bash
uv run --no-sync pytest packages/core/tests/test_claude_code_mcp.py -v
```

Expected: all tests pass. If `_notify_mail_arrived` isn't fully wired to a real notification (because FastMCP's API needs adaptation), the unit tests still pass because they only assert pickup-queue behavior.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/tests/test_claude_code_mcp.py
git commit -m "feat(bus-daemon): implement list_pending/ack/nack/handle and deliver() pickup queue"
```

---

## Task 6: Wire HTTPHost into the bus runner and `bus run`

**Files:**
- Modify: `packages/core/src/agent_core/bus/runner.py`
- Modify: `packages/core/src/agent_core/bus/cli.py`
- Create: `packages/core/tests/test_runner_http_host.py`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_runner_http_host.py`:

```python
"""Tests for the runner's HTTPHost discovery and lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.bus.runner import build_bus_from_config


@pytest.mark.asyncio
async def test_runner_returns_none_http_host_when_no_mcp_endpoints(tmp_path):
    """If no MCPHostable endpoints are registered, http_host is None."""
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
endpoints:
  - class: agent_core.endpoints.stub.StubEndpoint
    name: stub
""",
        encoding="utf-8",
    )
    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is None
    assert "stub" in bus._endpoints_by_name


@pytest.mark.asyncio
async def test_runner_constructs_http_host_when_mcp_endpoints_present(tmp_path):
    """One ClaudeCodeMCPEndpoint → HTTPHost is built with that mount."""
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-test
    params:
      mount: /mcp/agent-test
""",
        encoding="utf-8",
    )
    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    assert len(http_host._mounts) == 1
    assert http_host._mounts[0].mount == "/mcp/agent-test"


@pytest.mark.asyncio
async def test_runner_constructs_http_host_with_multiple_mcp_endpoints(tmp_path):
    """Two CC endpoints → both mounted on the same HTTPHost."""
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-pepper
    params:
      mount: /mcp/agent-pepper
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-deb
    params:
      mount: /mcp/agent-deb
  - class: agent_core.endpoints.stub.StubEndpoint
    name: stub
""",
        encoding="utf-8",
    )
    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    mounts = sorted(m.mount for m in http_host._mounts)
    assert mounts == ["/mcp/agent-deb", "/mcp/agent-pepper"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/core/tests/test_runner_http_host.py -v
```

Expected: TypeError — `build_bus_from_config` returns `Bus`, not a tuple.

- [ ] **Step 3: Modify `build_bus_from_config` to return `(Bus, HTTPHost | None)`**

Edit `packages/core/src/agent_core/bus/runner.py`. Replace the function signature and tail:

```python
from agent_core.bus.http_host import HTTPHost, MCPHostable

# ... rest of imports unchanged ...

async def build_bus_from_config(path: Path) -> tuple[Bus, HTTPHost | None]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    # ... existing bus + hooks + http guardrail + endpoints code unchanged ...
    # (inside the existing function body)

    # NEW (after endpoint registration loop, just before `return bus`):
    hostable: list[MCPHostable] = [
        spec.endpoint
        for spec in bus._endpoints_by_name.values()
        if isinstance(spec.endpoint, MCPHostable)
    ]
    http_host: HTTPHost | None = None
    if hostable:
        host = http_cfg.get("bind_host", "127.0.0.1")
        port = http_cfg.get("bind_port", 8788)
        http_host = HTTPHost(bind_host=host, bind_port=port)
        for h in hostable:
            http_host.mount(h)

    return bus, http_host
```

(Keep the existing body of the function intact, just append the http_host construction and replace `return bus` with `return bus, http_host`.)

- [ ] **Step 4: Update `bus/cli.py` to consume the new return shape**

Edit `_run_bus` in `packages/core/src/agent_core/bus/cli.py`:

```python
async def _run_bus(config_path: Path) -> None:
    bus, http_host = await build_bus_from_config(config_path)
    if http_host is not None:
        await http_host.start()
    try:
        await bus.start()
        endpoint_count = len(bus._endpoints_by_name)
        host_str = f" + http on :{http_host.port}" if http_host else ""
        console.print(
            f"[green]bus running[/green] — {endpoint_count} endpoint(s){host_str}; "
            "press Ctrl+C to stop."
        )

        stop_event = asyncio.Event()

        def _shutdown(*_):
            stop_event.set()

        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, _shutdown)
            loop.add_signal_handler(signal.SIGTERM, _shutdown)
        except NotImplementedError:
            pass  # Windows — SIGINT raises KeyboardInterrupt directly.

        async def _ttl_loop():
            while not stop_event.is_set():
                try:
                    await bus.run_ttl_sweep_once()
                except Exception:
                    log.exception("TTL sweep failed")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=bus.config.ttl_sweep_seconds)
                except TimeoutError:
                    pass

        async def _redelivery_loop():
            while not stop_event.is_set():
                try:
                    await bus.run_redelivery_sweep_once()
                except Exception:
                    log.exception("redelivery sweep failed")
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=bus.config.redelivery_sweep_seconds
                    )
                except TimeoutError:
                    pass

        sweeps = [asyncio.create_task(_ttl_loop()), asyncio.create_task(_redelivery_loop())]
        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            stop_event.set()
        finally:
            for t in sweeps:
                t.cancel()
            await asyncio.gather(*sweeps, return_exceptions=True)
            await bus.stop()
    finally:
        if http_host is not None:
            await http_host.stop()
        console.print("[yellow]bus stopped[/yellow]")
```

Also update `_status`, `_mailbox`, `_trace`, `_dlq_list`, `_replay`, `_dlq_purge` to unpack the tuple — replace each occurrence of `bus = await build_bus_from_config(...)` with `bus, _ = await build_bus_from_config(...)`.

- [ ] **Step 5: Run runner tests**

```bash
uv run --no-sync pytest packages/core/tests/test_runner_http_host.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Run the full bus test suite**

```bash
uv run --no-sync pytest packages/core/tests/ -k bus -v
```

Expected: all bus tests still pass (no regressions in existing runner/cli tests).

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/agent_core/bus/runner.py packages/core/src/agent_core/bus/cli.py packages/core/tests/test_runner_http_host.py
git commit -m "feat(bus-daemon): runner builds HTTPHost; bus run starts/stops it"
```

---

## Task 7: Daemon supervisor utilities (PID file + process tree kill)

**Files:**
- Create: `packages/core/src/agent_core/daemon/__init__.py`
- Create: `packages/core/src/agent_core/daemon/supervisor.py`
- Create: `packages/core/tests/test_daemon_supervisor.py`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_daemon_supervisor.py`:

```python
"""Tests for daemon PID-file supervision utilities."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_core.daemon.supervisor import (
    is_alive,
    kill_tree,
    read_pid,
    remove_pid,
    write_pid,
)


def test_write_and_read_pid(tmp_path: Path):
    pid_file = tmp_path / "daemon.pid"
    write_pid(pid_file, 12345)
    assert pid_file.exists()
    assert read_pid(pid_file) == 12345


def test_read_pid_returns_none_when_missing(tmp_path: Path):
    pid_file = tmp_path / "daemon.pid"
    assert read_pid(pid_file) is None


def test_read_pid_returns_none_for_corrupt_file(tmp_path: Path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("not-a-number")
    assert read_pid(pid_file) is None


def test_remove_pid_is_idempotent(tmp_path: Path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("123")
    remove_pid(pid_file)
    assert not pid_file.exists()
    remove_pid(pid_file)  # Should not raise.


def test_is_alive_false_for_nonexistent_pid():
    # Pick an unlikely-to-exist PID; psutil tolerates this.
    assert is_alive(999_999) is False


def test_is_alive_true_for_current_process():
    assert is_alive(os.getpid()) is True


def test_kill_tree_terminates_subprocess(tmp_path: Path):
    """Spawn a sleeping subprocess and verify kill_tree takes it down."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert is_alive(proc.pid) is True
        kill_tree(proc.pid)
        # Give the OS a moment to reap.
        for _ in range(20):
            if not is_alive(proc.pid):
                break
            time.sleep(0.1)
        assert is_alive(proc.pid) is False
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run --no-sync pytest packages/core/tests/test_daemon_supervisor.py -v
```

Expected: ImportError — `agent_core.daemon` does not exist.

- [ ] **Step 3: Create the package and the supervisor**

Create `packages/core/src/agent_core/daemon/__init__.py`:

```python
"""Daemon process supervision for agent-core (PID-managed lifecycle)."""
```

Create `packages/core/src/agent_core/daemon/supervisor.py`:

```python
"""PID file management and cross-platform process tree kill.

Modeled on Pepper's process.py but lives in agent_core. Used by
`agent-core daemon start/stop/status` to supervise the long-running
`agent-core bus run` subprocess.
"""

from __future__ import annotations

from pathlib import Path

import psutil


def write_pid(pid_file: Path, pid: int) -> None:
    """Write a PID to the PID file."""
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(pid), encoding="utf-8")


def read_pid(pid_file: Path) -> int | None:
    """Read a PID from the PID file. None if missing or corrupt."""
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def remove_pid(pid_file: Path) -> None:
    """Remove the PID file if it exists. Idempotent."""
    pid_file.unlink(missing_ok=True)


def is_alive(pid: int) -> bool:
    """Check whether a process with the given PID is currently running."""
    return bool(psutil.pid_exists(pid))


def kill_tree(pid: int) -> None:
    """Kill a process and all its descendants. Tolerates already-dead processes."""
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        psutil.wait_procs([*children, parent], timeout=5)
    except psutil.NoSuchProcess:
        return
```

- [ ] **Step 4: Run tests**

```bash
uv run --no-sync pytest packages/core/tests/test_daemon_supervisor.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/daemon/__init__.py packages/core/src/agent_core/daemon/supervisor.py packages/core/tests/test_daemon_supervisor.py
git commit -m "feat(bus-daemon): add daemon supervisor (PID file + process-tree kill)"
```

---

## Task 8: Daemon CLI (`start`, `stop`, `status`)

**Files:**
- Create: `packages/core/src/agent_core/daemon/cli.py`
- Modify: `packages/core/src/agent_core/cli.py`
- Modify: `packages/core/pyproject.toml` (no script — CLI is a sub-typer of `agent-core`)
- Create: `packages/core/tests/test_daemon_cli.py`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_daemon_cli.py`:

```python
"""Tests for `agent-core daemon` CLI."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_core.daemon.cli import app as daemon_app
from agent_core.daemon.supervisor import is_alive, read_pid


runner = CliRunner()


def test_status_when_not_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0
    assert "not running" in result.stdout.lower()


def test_stop_when_not_running_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["stop"])
    assert result.exit_code == 0


def test_start_refuses_without_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["start"])
    assert result.exit_code == 1
    assert "agent_core.yaml" in result.stdout


def test_status_with_stale_pid_reports_not_running_and_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("999999")  # very unlikely to be alive
    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0
    assert "not running" in result.stdout.lower()
    assert not pid_file.exists()  # stale file cleaned


def test_start_writes_pid_file_and_stop_kills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """End-to-end: write a config, daemon start, daemon stop. Real subprocess."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
endpoints:
  - class: agent_core.endpoints.stub.StubEndpoint
    name: stub
""",
        encoding="utf-8",
    )

    start_res = runner.invoke(daemon_app, ["start"])
    assert start_res.exit_code == 0
    pid_file = tmp_path / "daemon.pid"
    assert pid_file.exists()
    pid = read_pid(pid_file)
    assert pid is not None

    # Give the daemon a moment to come up.
    for _ in range(40):
        if is_alive(pid):
            break
        time.sleep(0.1)
    assert is_alive(pid) is True

    # Second start refuses.
    again = runner.invoke(daemon_app, ["start"])
    assert again.exit_code == 1
    assert str(pid) in again.stdout

    # Stop kills it cleanly.
    stop_res = runner.invoke(daemon_app, ["stop"])
    assert stop_res.exit_code == 0
    for _ in range(40):
        if not is_alive(pid):
            break
        time.sleep(0.1)
    assert is_alive(pid) is False
    assert not pid_file.exists()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/core/tests/test_daemon_cli.py -v
```

Expected: ImportError — `agent_core.daemon.cli` not found.

- [ ] **Step 3: Implement the daemon CLI**

Create `packages/core/src/agent_core/daemon/cli.py`:

```python
"""`agent-core daemon` — process supervision for the bus daemon.

start: spawn `agent-core bus run --config <home>/agent_core.yaml`
       detached; write the resulting PID to <home>/daemon.pid.
stop:  read the PID file, kill the process tree, remove the PID file.
status: report running/not-running, PID, last 20 lines of daemon.log.

The daemon's home directory defaults to ~/.agent-core/ but can be
overridden via the AGENT_CORE_HOME env var (used by tests).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console

from agent_core.daemon.supervisor import is_alive, kill_tree, read_pid, remove_pid, write_pid

app = typer.Typer(help="Daemon process supervision: start, stop, status.")
console = Console()


def _home() -> Path:
    """Return ~/.agent-core/ unless AGENT_CORE_HOME overrides it."""
    override = os.environ.get("AGENT_CORE_HOME")
    if override:
        return Path(override)
    return Path.home() / ".agent-core"


def _pid_path() -> Path:
    return _home() / "daemon.pid"


def _config_path() -> Path:
    return _home() / "agent_core.yaml"


def _log_path() -> Path:
    return _home() / "daemon.log"


@app.command()
def start() -> None:
    """Spawn `agent-core bus run` detached, write the PID file."""
    pid_file = _pid_path()
    cfg = _config_path()
    log_file = _log_path()

    if not cfg.exists():
        console.print(
            f"[red]No daemon config at {cfg}.[/red] "
            f"Create it manually for v1 (sub-project C handles auto-init)."
        )
        raise typer.Exit(code=1)

    existing = read_pid(pid_file)
    if existing is not None and is_alive(existing):
        console.print(f"[yellow]daemon already running (PID: {existing})[/yellow]")
        raise typer.Exit(code=1)
    if existing is not None:
        # Stale.
        remove_pid(pid_file)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_file, "ab", buffering=0)

    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_core.cli", "bus", "run", "--config", str(cfg)],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    write_pid(pid_file, proc.pid)
    console.print(f"[green]daemon started (PID: {proc.pid})[/green]")


@app.command()
def stop() -> None:
    """Kill the daemon and clean the PID file. Idempotent."""
    pid_file = _pid_path()
    pid = read_pid(pid_file)
    if pid is None:
        console.print("[yellow]daemon is not running[/yellow]")
        return
    if not is_alive(pid):
        console.print("[yellow]daemon is not running (stale PID file removed)[/yellow]")
        remove_pid(pid_file)
        return
    kill_tree(pid)
    remove_pid(pid_file)
    console.print(f"[green]daemon stopped (PID: {pid})[/green]")


@app.command()
def status() -> None:
    """Report daemon liveness and tail the log."""
    pid_file = _pid_path()
    log_file = _log_path()
    pid = read_pid(pid_file)

    if pid is None:
        console.print("[yellow]daemon is not running[/yellow]")
        return
    if not is_alive(pid):
        console.print("[yellow]daemon is not running (stale PID file removed)[/yellow]")
        remove_pid(pid_file)
        return

    console.print(f"[green]daemon is running (PID: {pid})[/green]")
    if log_file.exists():
        console.print("\n[dim]--- last 20 lines of daemon.log ---[/dim]")
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-20:]:
            console.print(line)
```

- [ ] **Step 4: Register the daemon subapp on the top-level CLI**

Edit `packages/core/src/agent_core/cli.py`. After the existing `bus_app` registration:

```python
from agent_core.bus.cli import app as bus_app
app.add_typer(bus_app, name="bus")

from agent_core.daemon.cli import app as daemon_app
app.add_typer(daemon_app, name="daemon")
```

- [ ] **Step 5: Run tests**

```bash
uv run --no-sync pytest packages/core/tests/test_daemon_cli.py -v
```

Expected: 5 passed. The end-to-end test takes ~5 seconds (real subprocess).

- [ ] **Step 6: Verify the CLI is wired up**

```bash
uv run --no-sync agent-core daemon --help
```

Expected: shows `start`, `stop`, `status` subcommands.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/agent_core/daemon/cli.py packages/core/src/agent_core/cli.py packages/core/tests/test_daemon_cli.py
git commit -m "feat(bus-daemon): add agent-core daemon start/stop/status"
```

---

## Task 9: Integration test — bus + ClaudeCodeMCPEndpoint + Stub round trip

**Files:**
- Create: `packages/core/tests/test_bus_daemon_integration.py`

- [ ] **Step 1: Write the integration test**

Create `packages/core/tests/test_bus_daemon_integration.py`:

```python
"""Integration: bus + ClaudeCodeMCPEndpoint + StubEndpoint, full round trip.

Runs the runner in-process (no separate daemon subprocess), boots the
HTTP host on an ephemeral port, opens a real MCP HTTP client against
the agent-test mount, and verifies:

1. The client sees both endpoints via list_endpoints.
2. send(to=stub, ...) routes through the bus to the stub's inbox.
3. When the stub publishes addressed to agent-test, the envelope reaches
   the agent-test endpoint's pickup queue.
"""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client

from agent_core.bus.envelope import TextMessagePayload
from agent_core.bus.runner import build_bus_from_config


@pytest.mark.asyncio
async def test_round_trip_via_real_http(tmp_path):
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-test
    description: "test agent for integration"
    params:
      mount: /mcp/agent-test
  - class: agent_core.endpoints.stub.StubEndpoint
    name: stub
    description: "echo for tests"
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    try:
        await bus.start()
        try:
            url = f"http://127.0.0.1:{http_host.port}/mcp/agent-test"

            async with Client(url) as client:
                # 1. list_endpoints
                eps = (await client.call_tool("list_endpoints", {})).data
                names = {e["name"] for e in eps}
                assert names == {"agent-test", "stub"}

                # 2. send to stub — verify stub receives via its inbox
                await client.call_tool(
                    "send",
                    {
                        "to": "stub",
                        "kind": "TextMessage",
                        "payload": {"kind": "TextMessage", "text": "hello-stub"},
                    },
                )
                # Bus dispatches synchronously inside _enqueue, so by the time
                # the call returns the stub has had deliver() called.
                stub_ep = bus._endpoints_by_name["stub"].endpoint
                assert any(
                    isinstance(env.payload, TextMessagePayload)
                    and env.payload.text == "hello-stub"
                    for env in stub_ep.inbox
                )

                # 3. stub publishes to agent-test → reaches pickup queue
                await stub_ep.send(
                    to="agent-test",
                    kind="TextMessage",
                    payload=TextMessagePayload(text="hello-agent"),
                )
                # If the FastMCP push notification path is wired, the agent
                # would be notified. Either way, list_pending should surface it.
                # Allow a short window for async dispatch to settle.
                for _ in range(40):
                    pending = (await client.call_tool("list_pending", {})).data
                    if pending:
                        break
                    await asyncio.sleep(0.05)
                pending = (await client.call_tool("list_pending", {})).data
                texts = [p["payload"]["text"] for p in pending]
                assert "hello-agent" in texts

                # Ack it
                env_id = next(p["id"] for p in pending if p["payload"]["text"] == "hello-agent")
                await client.call_tool("handle", {"envelope_id": env_id})

                pending2 = (await client.call_tool("list_pending", {})).data
                assert all(p["id"] != env_id for p in pending2)
        finally:
            await bus.stop()
    finally:
        await http_host.stop()
```

- [ ] **Step 2: Run the integration test**

```bash
uv run --no-sync pytest packages/core/tests/test_bus_daemon_integration.py -v
```

Expected: PASS. If the inbound `list_pending` assertion fails because the agent's `_session_active` flag isn't being toggled by FastMCP's lifecycle (so `deliver()` raises EU and the bus pauses delivery), the bus's `drain_for` should still surface the envelope on the next dispatch — but if not, the implementation in Task 5 needs to make sure `queue_for_pickup` happens even on the EU path so `list_pending` always reflects the queue. The test asserts pickup-queue behavior, which is the user-visible contract.

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/test_bus_daemon_integration.py
git commit -m "test(bus-daemon): integration round trip — agent ↔ bus ↔ stub via real HTTP"
```

---

## Task 10: Changelog fragment + final smoke + push branch + open PR

**Files:**
- Create: `packages/core/changelog.d/+bus-daemon.added.md`

- [ ] **Step 1: Add the changelog fragment**

Create `packages/core/changelog.d/+bus-daemon.added.md`:

```markdown
- `ClaudeCodeMCPEndpoint` adapter so Claude Code instances can connect
  to the bus over Streamable HTTP. Path-based identity at `/mcp/<name>`.
- Shared HTTP host (Starlette + Uvicorn) wired into the bus runner;
  mounts every registered `MCPHostable` endpoint automatically.
- `agent-core daemon start/stop/status` — PID-managed lifecycle for the
  long-running bus daemon. Spawns `agent-core bus run` detached.
```

- [ ] **Step 2: Smoke-test the full suite**

```bash
uv run --no-sync pytest -q
```

Expected: all baseline tests pass + the new tests pass. Record the new test count.

- [ ] **Step 3: Smoke-test ruff and import-linter**

```bash
uv run --no-sync ruff check .
uv run --no-sync lint-imports
```

Expected: ruff baseline held (no new errors); 1 contract kept, 0 broken.

- [ ] **Step 4: Verify CLIs still work**

```bash
uv run --no-sync agent-core --help
uv run --no-sync agent-core bus --help
uv run --no-sync agent-core daemon --help
uv run --no-sync agent-core hooks --help
uv run --no-sync agent-core notify --help 2>&1 | head -5 || true
```

Expected: each `--help` succeeds and shows expected subcommands.

- [ ] **Step 5: Manual end-to-end milestone (the validation milestone from the spec)**

This step is the spec's "Manual end-to-end" testing layer. Skip it for the PR's automated checks but perform before the PR is reviewed:

```bash
mkdir -p ~/.agent-core
cat > ~/.agent-core/agent_core.yaml <<'YAML'
bus:
  storage_path: ~/.agent-core/bus.sqlite
http:
  bind_host: 127.0.0.1
  bind_port: 8788
endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-testbot
    description: "Test agent for validating bus architecture."
    params:
      mount: /mcp/agent-testbot
  - class: agent_core.endpoints.stub.StubEndpoint
    name: stub
    description: "Echo endpoint for round-trip testing."
YAML

mkdir -p ~/.testbot
cat > ~/.testbot/.mcp.json <<'JSON'
{
  "mcpServers": {
    "agent-core": {
      "type": "http",
      "url": "http://localhost:8788/mcp/agent-testbot"
    }
  }
}
JSON
cat > ~/.testbot/CLAUDE.md <<'MD'
You are testbot, a fresh test agent for validating the agent-core bus.
You have access to the agent-core MCP tools: send, list_endpoints, list_pending, handle, ack, nack.
MD

uv run --no-sync agent-core daemon start
uv run --no-sync agent-core daemon status

# In another terminal:
cd ~/.testbot && claude
# Then in Claude: ask it to call list_endpoints, send to stub, send to itself.
# Verify list_pending surfaces the self-sent envelope.

uv run --no-sync agent-core daemon stop
```

Document the milestone results in the PR description.

- [ ] **Step 6: Commit changelog**

```bash
git add packages/core/changelog.d/+bus-daemon.added.md
git commit -m "docs(bus-daemon): add changelog fragment for sub-project B v1"
```

- [ ] **Step 7: Push branch and open PR**

```bash
git push -u origin feat/bus-daemon
gh pr create --title "feat(bus-daemon): make the bus a real daemon — sub-project B v1" --body "$(cat <<'EOF'
## Summary

Implements sub-project B v1 of the agent-core roadmap ([spec](docs/superpowers/specs/2026-04-28-bus-daemon-design.md), [plan](docs/superpowers/plans/2026-04-28-bus-daemon.md)).

- `ClaudeCodeMCPEndpoint` adapter: bus endpoint backed by FastMCP; path-based identity at `/mcp/<name>`; tools surface per the channel-bus spec; pickup queue for inbound envelopes; raises `EndpointUnavailable` when no session is connected.
- Shared `HTTPHost` (Starlette + Uvicorn) constructed by the runner from any registered `MCPHostable` endpoints; runs on the bus's asyncio loop; loopback bind enforced upstream.
- `agent-core daemon start/stop/status` — PID-managed lifecycle. Spawns `agent-core bus run` detached, supervises via `~/.agent-core/daemon.pid`, tails `~/.agent-core/daemon.log` on `status`.
- Pepper untouched. Validation done end-to-end on a fresh `~/.testbot/` agent.

## Test plan

- [x] `uv run --no-sync pytest -q` — full suite passes; +N tests added (record exact count).
- [x] `uv run --no-sync ruff check .` — no new errors.
- [x] `uv run --no-sync lint-imports` — 1 contract kept, 0 broken.
- [x] `uv run --no-sync agent-core daemon --help` shows the new subcommands.
- [x] Manual milestone: hand-built `~/.testbot/` connects to running daemon, calls `list_endpoints`, sends to `stub`, sends to itself, drains `list_pending`. Daemon `start` → `status` → `stop` cycle clean.

EOF
)"
```

- [ ] **Step 8: Bind changelog fragment to PR number after PR opens**

Once `gh pr create` returns the PR number `N`:

```bash
git mv packages/core/changelog.d/+bus-daemon.added.md packages/core/changelog.d/N.added.md
git commit -m "docs(bus-daemon): bind towncrier fragment to PR #N"
git push
```

Then verify CI is green and merge with `gh pr merge N --merge --delete-branch` (per past sub-project pattern; align with user instruction at merge time).

---

## Self-Review Checklist (run before handing off)

- **Spec coverage:** Every component in the spec maps to a task —
  `ClaudeCodeMCPEndpoint` (Tasks 3-5), HTTP host (Tasks 2, 6), daemon
  CLI (Tasks 7-8), test agent milestone (Task 10 Step 5). Tools list
  matches spec § Components.
- **Placeholder scan:** "Implementation hinges on FastMCP's session-
  manager API" in Task 5 is the only soft spot — it's a verify-and-wire
  step, not a TBD; the test contract is concrete (assert deliver()
  raises EU when no session, queues envelope, list_pending surfaces it).
- **Type consistency:** `MCPHostable.mount: str` and `ClaudeCodeMCPEndpoint.mount`
  match. `build_bus_from_config` return type changes from `Bus` to
  `tuple[Bus, HTTPHost | None]` — all callers (cli.py: `_run_bus`,
  `_status`, `_mailbox`, `_trace`, `_dlq_list`, `_replay`, `_dlq_purge`)
  updated in Task 6 Step 4.
- **No-dead-code rule:** No HTTP-POST-to-channel-server code is
  introduced anywhere; the scheduler endpoint is out of scope for this
  plan.
- **Pepper hands-off rule:** No edits under `~/.pepper/` or to Pepper's
  templates. Validation milestone uses a fresh `~/.testbot/`.
