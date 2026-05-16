# Daemon Bounce MCP Session Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fragile direct-HTTP `agent-core` MCP surface with a self-contained stdio FastMCP proxy so a daemon bounce/refresh can no longer strand a live Claude Code agent session (issue #91).

**Architecture:** A new `packages/agent-core-busproxy/` package runs a `FastMCPProxy` over **stdio**, backed by the daemon's per-agent HTTP endpoint (`http://127.0.0.1:8789/mcp/<agent>`) with a **fresh backend session per request**. Claude Code talks stdio to a process whose lifetime equals the Claude Code session; the backend session is reborn on every tool call, so a restarted daemon is never presented a stale `mcp-session-id`. A `TransientErrorMiddleware` converts backend-unreachable failures into a structured retryable tool result (fail-fast). The existing `agent-core-channel` wake relay is untouched.

**Tech Stack:** Python 3.12+, uv workspace, FastMCP 3.2.2 (`fastmcp.server.providers.proxy.FastMCPProxy` / `ProxyClient`, `fastmcp.server.middleware.Middleware`, `fastmcp.tools.base.ToolResult`), Typer CLI, pytest (`asyncio_mode=auto`).

**Reference spec:** `docs/superpowers/specs/2026-05-16-daemon-mcp-session-recovery-design.md`

---

## File Structure

**New package — `packages/agent-core-busproxy/`:**

- `pyproject.toml` — project metadata, `agent-core-busproxy` console script, hatchling build, deps.
- `src/agent_core_busproxy/__init__.py` — empty package marker.
- `src/agent_core_busproxy/transient.py` — `TransientErrorMiddleware`, `classify_backend_error`, `redact`, the transient-result builder + constants. One responsibility: turn backend-unreachable into a structured retryable tool result.
- `src/agent_core_busproxy/proxy.py` — `build_busproxy(agent, daemon_url)` → a `FastMCPProxy` with a per-request client factory + the transient middleware attached. One responsibility: assemble the proxy.
- `src/agent_core_busproxy/__main__.py` — Typer CLI (`--agent`, `--daemon-url`); runs the proxy over stdio. One responsibility: process entrypoint.
- `tests/test_scaffold.py`, `tests/test_proxy_api_contract.py`, `tests/test_proxy_build.py`, `tests/test_transient.py`, `tests/test_down_window.py`, `tests/test_cli.py`, `tests/test_regression_daemon_bounce.py` — one test file per behavior.

**Modified (existing):**

- `pyproject.toml` (repo root) — add `agent-core-busproxy = { workspace = true }` under `[tool.uv.sources]`; add `"packages/agent-core-busproxy/tests"` to `[tool.pytest.ini_options].testpaths`.
- `docs/setup/daemon.md` — replace the #91 stopgap warning with the resilient `.mcp.json` shape + behavior; add a cutover runbook.

---

## Task 1: Scaffold the `agent-core-busproxy` package

**Files:**
- Create: `packages/agent-core-busproxy/pyproject.toml`
- Create: `packages/agent-core-busproxy/src/agent_core_busproxy/__init__.py`
- Create: `packages/agent-core-busproxy/tests/test_scaffold.py`
- Modify: `pyproject.toml` (repo root) — `[tool.uv.sources]` and `[tool.pytest.ini_options].testpaths`

- [ ] **Step 1: Create the package `pyproject.toml`**

Create `packages/agent-core-busproxy/pyproject.toml`:

```toml
[project]
name = "agent-core-busproxy"
version = "0.1.0"
description = "Stdio MCP proxy — bridges Claude Code to the agent-core daemon bus tool surface with per-request backend sessions so a daemon bounce never strands the session."
requires-python = ">=3.12"
dependencies = [
    "fastmcp>=3.2",
    "mcp>=1.0",
    "anyio>=4.0",
    "httpx>=0.27",
    "typer>=0.12",
]

[project.scripts]
agent-core-busproxy = "agent_core_busproxy.__main__:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core_busproxy"]
```

- [ ] **Step 2: Create the package marker**

Create `packages/agent-core-busproxy/src/agent_core_busproxy/__init__.py`:

```python
"""agent-core-busproxy — stdio MCP proxy to the daemon bus tool surface."""
```

- [ ] **Step 3: Register the package in the workspace**

In the repo-root `pyproject.toml`, under `[tool.uv.sources]`, add this line adjacent to the other `workspace = true` entries (e.g., right after the `agent-core-channel` line):

```toml
agent-core-busproxy = { workspace = true }
```

In the repo-root `pyproject.toml`, under `[tool.pytest.ini_options]`, add this entry to the `testpaths` array adjacent to the other `packages/agent-core-*/tests` entries:

```toml
    "packages/agent-core-busproxy/tests",
```

- [ ] **Step 4: Write the scaffold test**

Create `packages/agent-core-busproxy/tests/test_scaffold.py`:

```python
"""Package scaffold smoke test."""

from __future__ import annotations


def test_package_imports() -> None:
    import agent_core_busproxy

    assert agent_core_busproxy.__doc__ is not None
```

- [ ] **Step 5: Sync the workspace and run the test**

Run: `uv sync 2>&1 | tail -3 && uv run --package agent-core-busproxy pytest packages/agent-core-busproxy -q`
Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-busproxy/pyproject.toml packages/agent-core-busproxy/src/agent_core_busproxy/__init__.py packages/agent-core-busproxy/tests/test_scaffold.py pyproject.toml
git commit -m "feat(busproxy): scaffold agent-core-busproxy package (#91)"
```

---

## Task 2: Pin the FastMCP proxy API with a characterization test

The exact stdio-run call and the exact exception type raised when the backend URL is dead are FastMCP-version-specific. This task pins both against the installed `fastmcp` (3.2.2) with a real test, so later tasks use concrete names rather than guesses. This is API discovery that produces concrete code — not a placeholder.

**Files:**
- Create: `packages/agent-core-busproxy/tests/test_proxy_api_contract.py`

- [ ] **Step 1: Write the characterization test**

Create `packages/agent-core-busproxy/tests/test_proxy_api_contract.py`:

```python
"""Pin the installed FastMCP proxy API surface this package depends on.

If FastMCP changes these, this test fails first and points at exactly
what to update in proxy.py / transient.py.
"""

from __future__ import annotations

import inspect

import pytest


def test_fastmcpproxy_and_proxyclient_importable() -> None:
    from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient

    # FastMCPProxy takes a client_factory kwarg.
    sig = inspect.signature(FastMCPProxy.__init__)
    assert "client_factory" in sig.parameters

    # ProxyClient accepts a URL string target and supports .new().
    pc = ProxyClient("http://127.0.0.1:65535/mcp/nobody")
    assert hasattr(pc, "new")


def test_client_supports_init_timeout() -> None:
    from fastmcp import Client

    sig = inspect.signature(Client.__init__)
    assert "init_timeout" in sig.parameters


def test_middleware_has_on_call_tool() -> None:
    from fastmcp.server.middleware import Middleware

    assert hasattr(Middleware, "on_call_tool")


def test_toolresult_accepts_structured_content() -> None:
    from fastmcp.tools.base import ToolResult

    r = ToolResult(structured_content={"error": "x", "transient": True})
    assert r.structured_content == {"error": "x", "transient": True}


@pytest.mark.asyncio
async def test_client_surfaces_structured_content_attribute() -> None:
    """Pin the CLIENT-side accessor: a tool returning ToolResult(
    structured_content=...) is readable as result.structured_content.
    Every later test asserts on this attribute."""
    from fastmcp import Client, FastMCP
    from fastmcp.tools.base import ToolResult

    srv = FastMCP("pin")

    @srv.tool()
    async def t() -> ToolResult:
        return ToolResult(structured_content={"transient": True})

    async with Client(srv) as c:
        res = await c.call_tool("t", {})

    assert res.structured_content == {"transient": True}


@pytest.mark.asyncio
async def test_dead_backend_raises_on_tool_call() -> None:
    """Calling a tool through a proxy whose backend is unreachable raises.

    Records the concrete exception type so transient.py can catch it.
    Port 65535 with nothing listening => connect failure.
    """
    from fastmcp import Client
    from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient

    base = ProxyClient("http://127.0.0.1:65535/mcp/nobody", init_timeout=2.0)
    proxy = FastMCPProxy(client_factory=lambda: base.new(), name="probe")

    with pytest.raises(BaseException) as excinfo:  # noqa: PT011 - characterizing
        async with Client(proxy) as c:
            await c.call_tool("list_endpoints", {})

    # Concrete type pinned for transient.classify_backend_error.
    # Assert it is NOT a clean MCP tool-result (i.e. the failure surfaces
    # as an exception, which is what the middleware will intercept).
    assert excinfo.value is not None
    chain = []
    e: BaseException | None = excinfo.value
    while e is not None:
        chain.append(type(e).__name__)
        e = e.__cause__ or e.__context__
    # Connection-class failure somewhere in the chain.
    assert any(
        n in chain
        for n in (
            "ConnectError",
            "ConnectionError",
            "ConnectTimeout",
            "McpError",
            "HTTPError",
            "OSError",
            "TimeoutError",
        )
    ), f"unexpected exception chain: {chain}"
```

- [ ] **Step 2: Run it**

Run: `uv run --package agent-core-busproxy pytest packages/agent-core-busproxy/tests/test_proxy_api_contract.py -q`
Expected: `6 passed`. If any assertion about the exception chain fails, read the printed `chain` and record the actual type names — those exact names are used in Task 4's `classify_backend_error`.

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-busproxy/tests/test_proxy_api_contract.py
git commit -m "test(busproxy): pin installed FastMCP proxy API contract (#91)"
```

---

## Task 3: `proxy.py` — assemble the per-request proxy (tool-surface fidelity)

**Files:**
- Create: `packages/agent-core-busproxy/src/agent_core_busproxy/proxy.py`
- Create: `packages/agent-core-busproxy/tests/test_proxy_build.py`

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-busproxy/tests/test_proxy_build.py`:

```python
"""build_busproxy assembles a proxy that mirrors the daemon tool surface."""

from __future__ import annotations

import pytest
from fastmcp import Client

from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core_busproxy.proxy import build_busproxy


class _StubHandle:
    async def ack(self, envelope_id: str) -> None: ...
    async def publish(self, envelope, to=None) -> None: ...
    async def nack(self, envelope_id, requeue=True) -> None: ...
    def endpoints(self) -> list:
        return []


# The bus tool surface ClaudeCodeMCPEndpoint guarantees (see
# claude_code_mcp.py _register_tools). Asserting these by name avoids
# depending on any FastMCP-internal tool-enumeration API.
_EXPECTED_BUS_TOOLS = {
    "send",
    "list_endpoints",
    "describe_endpoint",
    "list_pending",
    "handle",
    "ack",
    "nack",
    "consume",
    "reply",
    "peek",
    "show_my_day",
}


@pytest.mark.asyncio
async def test_proxy_mirrors_backend_tool_surface() -> None:
    """tools/list through the proxy exposes the daemon endpoint's tools."""
    backend = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    await backend.start(_StubHandle())  # type: ignore[arg-type]
    try:
        # Proxy pointed directly at the in-process backend server.
        proxy = build_busproxy(agent="agent", daemon_url=None, _backend=backend._mcp)
        async with Client(proxy) as c:
            proxied = {t.name for t in await c.list_tools()}

        missing = _EXPECTED_BUS_TOOLS - proxied
        assert not missing, f"proxy missing bus tools: {missing}"
    finally:
        await backend.stop()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --package agent-core-busproxy pytest packages/agent-core-busproxy/tests/test_proxy_build.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_core_busproxy.proxy'`.

- [ ] **Step 3: Implement `proxy.py`**

Create `packages/agent-core-busproxy/src/agent_core_busproxy/proxy.py`:

```python
"""Assemble the stdio bus proxy.

The proxy forwards the daemon's per-agent MCP tool surface. Each tool
call mints a FRESH backend session (ProxyClient.new()), so a restarted
daemon is never presented a stale mcp-session-id — issue #91 is removed
by construction, not by recovery logic.

`init_timeout` keeps a down/bouncing daemon from hanging the call: the
backend connect fails fast and the TransientErrorMiddleware (attached in
Task 5) turns that into a structured retryable tool result.
"""

from __future__ import annotations

from typing import Any

from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient

# Fail-fast: a down daemon must not hang a tool call. Small connect/init
# budget; the agent owns the retry decision (spec: fail-fast retryable).
_BACKEND_INIT_TIMEOUT_SECONDS = 5.0
# Per-call request budget once connected (a healthy daemon answers in ms;
# this only bounds a half-open connection).
_BACKEND_REQUEST_TIMEOUT_SECONDS = 60.0


def build_busproxy(
    *,
    agent: str,
    daemon_url: str | None,
    _backend: Any | None = None,
) -> FastMCPProxy:
    """Return a FastMCPProxy over the daemon's per-agent endpoint.

    Args:
        agent: bus agent name (URL path segment).
        daemon_url: e.g. ``http://127.0.0.1:8789``. Ignored when
            ``_backend`` is supplied.
        _backend: test seam — an in-process FastMCP server to proxy
            instead of an HTTP URL. Production always passes a URL.
    """
    if _backend is not None:
        base_client = ProxyClient(_backend)
    else:
        if not daemon_url:
            raise ValueError("daemon_url is required when no _backend is given")
        url = f"{daemon_url.rstrip('/')}/mcp/{agent}"
        base_client = ProxyClient(
            url,
            init_timeout=_BACKEND_INIT_TIMEOUT_SECONDS,
            timeout=_BACKEND_REQUEST_TIMEOUT_SECONDS,
        )

    def client_factory() -> Any:
        # Fresh session per request — the #91 fix.
        return base_client.new()

    return FastMCPProxy(client_factory=client_factory, name=f"agent-core[{agent}]")
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run --package agent-core-busproxy pytest packages/agent-core-busproxy/tests/test_proxy_build.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-busproxy/src/agent_core_busproxy/proxy.py packages/agent-core-busproxy/tests/test_proxy_build.py
git commit -m "feat(busproxy): build_busproxy with per-request backend sessions (#91)"
```

---

## Task 4: `transient.py` — structured retryable error contract

**Files:**
- Create: `packages/agent-core-busproxy/src/agent_core_busproxy/transient.py`
- Create: `packages/agent-core-busproxy/tests/test_transient.py`

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-busproxy/tests/test_transient.py`:

```python
"""TransientErrorMiddleware: backend-down -> structured retryable result;
genuine tool errors pass through verbatim."""

from __future__ import annotations

import httpx
import pytest
from fastmcp.exceptions import ToolError

from agent_core_busproxy.transient import (
    TRANSIENT_ERROR_CODE,
    TransientErrorMiddleware,
    classify_backend_error,
    redact,
)


def test_redact_strips_query_string() -> None:
    # Same discipline as the #76 signed-CDN redaction.
    assert redact("connect to https://cdn.example.com/x?sig=SECRET&t=9") == (
        "connect to https://cdn.example.com/x?<redacted>"
    )


def test_classify_connection_error_is_transient() -> None:
    assert classify_backend_error(httpx.ConnectError("refused")) is True
    assert classify_backend_error(ConnectionError("refused")) is True
    assert classify_backend_error(TimeoutError()) is True


def test_classify_tool_error_is_not_transient() -> None:
    assert classify_backend_error(ToolError("bad argument")) is False
    assert classify_backend_error(ValueError("nope")) is False


@pytest.mark.asyncio
async def test_middleware_wraps_backend_down_as_structured_result() -> None:
    mw = TransientErrorMiddleware()

    async def call_next(_ctx):
        raise httpx.ConnectError("Connection refused to http://h/x?token=ABC")

    result = await mw.on_call_tool(object(), call_next)

    assert result.structured_content["error"] == TRANSIENT_ERROR_CODE
    assert result.structured_content["transient"] is True
    assert isinstance(result.structured_content["retry_after_seconds"], int)
    assert "token=ABC" not in result.structured_content["detail"]
    assert "<redacted>" in result.structured_content["detail"]


@pytest.mark.asyncio
async def test_middleware_passes_genuine_tool_error_through() -> None:
    mw = TransientErrorMiddleware()

    async def call_next(_ctx):
        raise ToolError("envelope_id not found")

    with pytest.raises(ToolError, match="envelope_id not found"):
        await mw.on_call_tool(object(), call_next)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --package agent-core-busproxy pytest packages/agent-core-busproxy/tests/test_transient.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_core_busproxy.transient'`.

- [ ] **Step 3: Implement `transient.py`**

Create `packages/agent-core-busproxy/src/agent_core_busproxy/transient.py`:

```python
"""Translate backend-unreachable failures into a structured, retryable
tool result. Genuine backend tool errors pass through verbatim so the
agent never retry-loops on a real failure.

Discriminator: a failure to *reach/handshake* the daemon (connect
refused, connect/init timeout, transport drop, MCP protocol error from a
missing session) is transient. An error the daemon itself raised while
running the tool (ToolError / value errors surfaced as tool errors) is
genuine and re-raised unchanged.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult
from mcp.shared.exceptions import McpError

log = logging.getLogger(__name__)

TRANSIENT_ERROR_CODE = "bus_unavailable"
_RETRY_AFTER_SECONDS = 5

# Reuse the #76 redaction shape: drop the entire query string (signed CDN
# tokens / session ids live there).
_URL_QS_RE = re.compile(r"(https?://[^\s?]+)\?\S*")


def redact(text: str) -> str:
    """Strip query strings from any URLs in a human string."""
    return _URL_QS_RE.sub(r"\1?<redacted>", text)


# Genuine tool errors: the daemon connected and ran the tool, it failed.
_GENUINE_TYPES: tuple[type[BaseException], ...] = (ToolError,)

# Transient: could not reach / handshake the daemon.
_TRANSIENT_TYPES: tuple[type[BaseException], ...] = (
    httpx.HTTPError,
    ConnectionError,
    TimeoutError,
    OSError,
    McpError,
)


def classify_backend_error(exc: BaseException) -> bool:
    """True => transient (daemon unreachable). False => genuine tool error."""
    if isinstance(exc, _GENUINE_TYPES):
        return False
    if isinstance(exc, _TRANSIENT_TYPES):
        return True
    # Unknown: treat as genuine so real bugs are never masked as retryable.
    return False


def _transient_result(exc: BaseException) -> ToolResult:
    detail = redact(f"{type(exc).__name__}: {exc}")
    log.warning("busproxy: backend unavailable (transient): %s", detail)
    return ToolResult(
        structured_content={
            "error": TRANSIENT_ERROR_CODE,
            "transient": True,
            "retry_after_seconds": _RETRY_AFTER_SECONDS,
            "detail": detail,
        }
    )


class TransientErrorMiddleware(Middleware):
    """Intercept tool calls; map daemon-unreachable to a retryable result."""

    async def on_call_tool(self, context: Any, call_next: Any) -> ToolResult:
        try:
            return await call_next(context)
        except BaseException as exc:  # noqa: BLE001 - classify then re-raise
            if classify_backend_error(exc):
                return _transient_result(exc)
            raise
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run --package agent-core-busproxy pytest packages/agent-core-busproxy/tests/test_transient.py -q`
Expected: PASS (5 passed). If `test_classify_connection_error_is_transient` fails, the exception chain pinned in Task 2 Step 2 names the real type — add it to `_TRANSIENT_TYPES`.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-busproxy/src/agent_core_busproxy/transient.py packages/agent-core-busproxy/tests/test_transient.py
git commit -m "feat(busproxy): structured retryable transient-error contract (#91)"
```

---

## Task 5: Wire the middleware into the proxy + down-window fail-fast test

**Files:**
- Modify: `packages/agent-core-busproxy/src/agent_core_busproxy/proxy.py`
- Create: `packages/agent-core-busproxy/tests/test_down_window.py`

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-busproxy/tests/test_down_window.py`:

```python
"""Daemon-down window: a tool call returns the structured transient
result PROMPTLY (fail-fast, no long hang)."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client

from agent_core_busproxy.proxy import build_busproxy
from agent_core_busproxy.transient import TRANSIENT_ERROR_CODE


@pytest.mark.asyncio
async def test_dead_backend_returns_transient_result_fast() -> None:
    # Nothing listening on 65535 => connect refused/timeout.
    proxy = build_busproxy(agent="agent", daemon_url="http://127.0.0.1:65535")

    async with Client(proxy) as c:
        # Whole call must finish well under the 5s init budget * slack.
        result = await asyncio.wait_for(
            c.call_tool("list_endpoints", {}), timeout=15.0
        )

    assert result.structured_content["error"] == TRANSIENT_ERROR_CODE
    assert result.structured_content["transient"] is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --package agent-core-busproxy pytest packages/agent-core-busproxy/tests/test_down_window.py -q`
Expected: FAIL — without the middleware the dead backend raises instead of returning the structured result (the assertion on `structured_content` fails, or the call raises).

- [ ] **Step 3: Attach the middleware in `build_busproxy`**

In `packages/agent-core-busproxy/src/agent_core_busproxy/proxy.py`, add the import near the top with the other imports:

```python
from agent_core_busproxy.transient import TransientErrorMiddleware
```

Then change the final `return` of `build_busproxy` from:

```python
    return FastMCPProxy(client_factory=client_factory, name=f"agent-core[{agent}]")
```

to:

```python
    proxy = FastMCPProxy(client_factory=client_factory, name=f"agent-core[{agent}]")
    proxy.add_middleware(TransientErrorMiddleware())
    return proxy
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run --package agent-core-busproxy pytest packages/agent-core-busproxy/tests/test_down_window.py packages/agent-core-busproxy/tests/test_proxy_build.py -q`
Expected: PASS (both — the middleware must not break the fidelity test).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-busproxy/src/agent_core_busproxy/proxy.py packages/agent-core-busproxy/tests/test_down_window.py
git commit -m "feat(busproxy): attach transient middleware; fail-fast down-window (#91)"
```

---

## Task 6: `__main__.py` — stdio CLI entrypoint

**Files:**
- Create: `packages/agent-core-busproxy/src/agent_core_busproxy/__main__.py`
- Create: `packages/agent-core-busproxy/tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-busproxy/tests/test_cli.py`:

```python
"""Smoke tests for the busproxy Typer CLI."""

from __future__ import annotations

from typer.testing import CliRunner

from agent_core_busproxy.__main__ import app


def test_cli_help_runs() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--agent" in result.output
    assert "--daemon-url" in result.output


def test_cli_requires_agent() -> None:
    result = CliRunner().invoke(app, [])
    assert result.exit_code != 0
    assert (
        "Missing option" in result.output
        or "required" in result.output.lower()
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --package agent-core-busproxy pytest packages/agent-core-busproxy/tests/test_cli.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_core_busproxy.__main__'`.

- [ ] **Step 3: Implement `__main__.py`**

Create `packages/agent-core-busproxy/src/agent_core_busproxy/__main__.py`:

```python
"""Typer CLI for agent-core-busproxy.

Runs a FastMCP proxy over stdio, backed by the daemon's per-agent HTTP
endpoint. Spawned by Claude Code as a stdio MCP server; its lifetime is
the Claude Code session, decoupled from the daemon's lifetime.
"""

from __future__ import annotations

import anyio
import typer

app = typer.Typer(add_completion=False)


@app.command()
def main(
    agent: str = typer.Option(..., "--agent", help="Agent name on the bus."),
    daemon_url: str = typer.Option(
        "http://127.0.0.1:8789",
        "--daemon-url",
        help="agent-core daemon URL (default: http://127.0.0.1:8789).",
    ),
) -> None:
    """Run the agent-core stdio bus proxy."""
    from agent_core_busproxy.proxy import build_busproxy

    proxy = build_busproxy(agent=agent, daemon_url=daemon_url)

    async def _run() -> None:
        await proxy.run_async(transport="stdio")

    anyio.run(_run)


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run --package agent-core-busproxy pytest packages/agent-core-busproxy/tests/test_cli.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Verify the console script resolves**

Run: `uv run agent-core-busproxy --help 2>&1 | tail -5`
Expected: usage text containing `--agent` and `--daemon-url`. (If `run_async` rejects `transport="stdio"`, the Task 2 contract test's FastMCP version differs — read `FastMCP.run_async` signature in the installed source and use the documented stdio invocation; the default of `run_async()` is stdio.)

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-busproxy/src/agent_core_busproxy/__main__.py packages/agent-core-busproxy/tests/test_cli.py
git commit -m "feat(busproxy): stdio Typer CLI entrypoint (#91)"
```

---

## Task 7: #91 regression — survive a real daemon bounce

This is the load-bearing proof. Stand up a real `ClaudeCodeMCPEndpoint` FastMCP server on an ephemeral TCP port (HTTP, exactly like the daemon), connect one long-lived busproxy `Client` to it, call a tool, **stop and restart** the backend server, then call again — it must succeed with no client-side re-handshake. Deterministic; no real sleeps (poll readiness with a short bounded loop).

**Files:**
- Create: `packages/agent-core-busproxy/tests/test_regression_daemon_bounce.py`

- [ ] **Step 1: Write the regression test**

Create `packages/agent-core-busproxy/tests/test_regression_daemon_bounce.py`:

```python
"""#91 regression: a daemon restart must not strand a live session.

A long-lived busproxy Client stays connected across a full backend
stop+start. Because every tool call mints a fresh backend session, the
post-restart call succeeds without any client re-initialize.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

import pytest
import uvicorn
from fastmcp import Client

from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core_busproxy.proxy import build_busproxy


class _StubHandle:
    async def ack(self, envelope_id: str) -> None: ...
    async def publish(self, envelope, to=None) -> None: ...
    async def nack(self, envelope_id, requeue=True) -> None: ...
    def endpoints(self) -> list:
        return [type("E", (), {"name": "discord", "description": "d"})()]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.asynccontextmanager
async def _run_backend(port: int):
    """Serve a real ClaudeCodeMCPEndpoint over HTTP on `port`."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    await ep.start(_StubHandle())  # type: ignore[arg-type]
    app = ep.asgi_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    # Bounded readiness poll (no fixed sleep).
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    try:
        yield ep
    finally:
        server.should_exit = True
        await task
        await ep.stop()


@pytest.mark.asyncio
async def test_session_survives_backend_bounce() -> None:
    port = _free_port()
    proxy = build_busproxy(
        agent="agent", daemon_url=f"http://127.0.0.1:{port}"
    )

    async with Client(proxy) as client:  # one long-lived client
        async with _run_backend(port):
            r1 = await client.call_tool("list_endpoints", {})
            assert any(e["name"] == "discord" for e in r1.data)

        # Backend is now DOWN. A call here is the daemon-down window.
        mid = await client.call_tool("list_endpoints", {})
        assert mid.structured_content["transient"] is True

        # Bring a brand-new backend process up on the same port.
        async with _run_backend(port):
            r2 = await client.call_tool("list_endpoints", {})
            # Succeeds with NO client re-handshake — #91 fixed.
            assert any(e["name"] == "discord" for e in r2.data)
```

- [ ] **Step 2: Ensure `uvicorn` is available to the test**

`uvicorn` is already a transitive dep of the daemon HTTP host. Confirm it imports in the busproxy env:

Run: `uv run --package agent-core-busproxy python -c "import uvicorn; print(uvicorn.__version__)"`
Expected: a version prints. If `ModuleNotFoundError`, add `"uvicorn>=0.30"` to `packages/agent-core-busproxy/pyproject.toml` `dependencies`, re-run `uv sync`, and re-run this step.

- [ ] **Step 3: Run the regression**

Run: `uv run --package agent-core-busproxy pytest packages/agent-core-busproxy/tests/test_regression_daemon_bounce.py -q`
Expected: PASS. This passing is the definition of #91 being fixed. If it hangs, the per-request session is not actually fresh — re-check `client_factory` calls `base_client.new()` per call (Task 3).

- [ ] **Step 4: Run the whole package suite**

Run: `uv run --package agent-core-busproxy pytest packages/agent-core-busproxy -q`
Expected: all green.

- [ ] **Step 5: Wake path untouched (regression)**

This change adds a new package and does not modify `agent-core-channel`.
Prove the wake relay still passes unchanged.

Run: `uv run --package agent-core-channel pytest packages/agent-core-channel -q`
Expected: all green (same count as before this branch — 93 passed at base).
If anything fails, the change leaked outside its package — stop and fix.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-busproxy/tests/test_regression_daemon_bounce.py packages/agent-core-busproxy/pyproject.toml
git commit -m "test(busproxy): #91 regression — session survives daemon bounce"
```

---

## Task 8: Docs — replace the #91 stopgap, document the resilient `.mcp.json`

**Files:**
- Modify: `docs/setup/daemon.md` (the warning block added on the #79 branch)

- [ ] **Step 1: Replace the stopgap warning**

In `docs/setup/daemon.md`, find the block that begins:

> **Live agent sessions must be restarted after a bounce.**

Replace that entire block-quote with:

```markdown
> **Live agent sessions survive a bounce (#91).** Agents connect to the
> bus tool surface through the `agent-core-busproxy` stdio MCP server
> (not directly over HTTP). Each tool call mints a fresh backend session,
> so `daemon refresh`/`install`/`start`/`stop` (and crashes) no longer
> strand a live Claude Code session: in-flight calls during the down
> window return a structured `{"error":"bus_unavailable","transient":true,
> "retry_after_seconds":5}` result that the agent retries, and the next
> call after the daemon is back succeeds with no session restart. The
> `agent-core-channel` wake relay already reconnects on its own.
```

- [ ] **Step 2: Add the resilient `.mcp.json` shape**

Append to `docs/setup/daemon.md` a new section:

````markdown
## Agent `.mcp.json` (resilient shape)

Each agent's `<agent_root>/.mcp.json` must point the bus tool surface at
the stdio busproxy — never the daemon HTTP URL directly:

```json
{
  "mcpServers": {
    "agent-core": {
      "type": "stdio", "command": "uv",
      "args": ["run", "--project", "<AGENT_CORE_REPO>",
               "agent-core-busproxy", "--agent", "<AGENT>",
               "--daemon-url", "http://127.0.0.1:8789"]
    },
    "agent-core-channel": {
      "type": "stdio", "command": "uv",
      "args": ["run", "--project", "<AGENT_CORE_REPO>",
               "agent-core-channel", "--agent", "<AGENT>",
               "--daemon-url", "http://127.0.0.1:8789"]
    }
  }
}
```

Both surfaces are now stdio and reconnect independently. The old
`{"type":"http","url":".../mcp/<agent>"}` form is the #91 failure mode —
do not use it. Cut over per agent: a fresh/throwaway test agent first,
validate across several real `daemon refresh` cycles, then Pepper last
(rollback = restore the backed-up `.mcp.json`, a single file).
````

- [ ] **Step 3: Commit**

```bash
git add docs/setup/daemon.md
git commit -m "docs(daemon): replace #91 stopgap with resilient busproxy .mcp.json (#91)"
```

---

## Task 9: Manual acceptance + Claude Code respawn verification (operator-run — no commit)

Final acceptance before close-out. Same shape as #79's manual step / #76 T7. The operator runs this; nothing is committed. Test agent first, Pepper last (per the standing hands-off rule).

- [ ] **Step 1:** On a **fresh/throwaway test agent**, set `<agent_root>/.mcp.json` to the resilient shape from Task 8 Step 2 (back up the old file first). Start the agent's Claude Code session.
- [ ] **Step 2:** Confirm the agent can call a bus tool (e.g. ask it to `list_endpoints` / `consume`) — baseline works through the busproxy.
- [ ] **Step 3:** While that session stays open, run `uv run agent-core daemon refresh` (real bounce). Per #79's cache hazard, verify the daemon actually runs current code (check the installed file, not just the stamp).
- [ ] **Step 4:** Without restarting the agent session, have the agent call a bus tool again. Expected: it succeeds (possibly after one `{transient:true}` retry during the down window) — **no session restart**. Repeat the refresh→call cycle 3× to cover slow refreshes.
- [ ] **Step 5:** Confirm wake still works: send the test agent a message, confirm it wakes (the `agent-core-channel` path) and can read/reply via the busproxy.
- [ ] **Step 6 (residual-SPOF verification, spec §Error handling item 3b):** Kill the busproxy *child process itself* (find it via the OS process list; it is a `uv run … agent-core-busproxy` child of the Claude Code session). Observe whether Claude Code re-spawns the stdio MCP server on the next tool call. Record the finding in `docs/setup/daemon.md` under the resilient-`.mcp.json` section: if Claude Code auto-respawns, state so; if not, document the operator action (restart the session) and note this is the seam the future always-on/reboot work must automate.
- [ ] **Step 7:** Only after Steps 1–6 pass on the test agent: cut Pepper's `.mcp.json` over to the resilient shape (back up first), restart her session once, and re-run Steps 3–5 against her. Rollback if anything regresses (restore the backup, restart).
- [ ] **Step 8:** Capture the test-agent transcript (a refresh-survived tool call + the down-window `{transient:true}` retry) as PR/issue acceptance evidence, then close #91.

---

## After all tasks: finish the branch

Use **superpowers:finishing-a-development-branch**. Do not push/open/merge or cut Pepper's `.mcp.json` over without Jeff's explicit go-ahead — same gate as #79/#76. Task 9 is operator-run and gates close-out; #91 closes only after the test-agent validation passes.

## Notes for the implementer

- **DRY/YAGNI:** no shared/persistent backend session, no in-proxy retry/queue — per-request + fail-fast is the whole design (spec decisions 1–2).
- **No real sleeps in tests:** all retry/backoff lives in the agent, not the proxy. Readiness is a bounded poll. (This avoids the looptime no-yield hang that bit #76 Task 5.)
- **Self-contained package:** `agent-core-busproxy` never reaches into sibling-project venvs; it depends only on published deps + (in tests) the in-repo `agent-core` endpoint for a real backend.
- **Redaction:** `detail` strips URL query strings (signed tokens/session ids) — same discipline as #76.
- **Bus services are their own package:** mirrors `agent-core-channel`'s shape exactly.
- **Wake×tool interleave — deliberate scoping:** the spec's testing list mentions an automated wake/tool interleave test. The two surfaces are independent OS processes with **no shared state** (busproxy = per-request sessions; channel relay = its own `/notify` SSE), so an automated interleave adds little over the per-surface tests (Tasks 5/7) + the channel suite staying green (Task 7 Step 5). The genuine end-to-end interleave is validated by the operator in Task 9 Step 5 (real wake + real busproxy tool call after a real refresh). This is a conscious YAGNI call, recorded here so it is not a silent gap.
```
