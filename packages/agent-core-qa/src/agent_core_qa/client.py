"""DaemonClient — thin HTTP wrapper for the agent_core daemon.

Tool-shaped: stateless across calls, no connection pooling beyond what
httpx does internally, no retry, no session caching.

Transport discovery (Preflight Step 0, Task 3):
    The daemon's HTTPHost (Starlette+Uvicorn) mounts only MCPHostable
    endpoint paths — there is NO /bus/publish or /bus/inbox route.
    Endpoints that implement MCPHostable (i.e. ClaudeCodeMCPEndpoint)
    are mounted at /mcp/<name>/ and expose a FastMCP Streamable HTTP
    server. StubEndpoint is NOT MCPHostable — it has no HTTP surface.

    To publish an envelope from the QA runner, we call the `send` MCP
    tool on a ClaudeCodeMCPEndpoint (e.g. the `qa` agent endpoint). The
    tool stamps from_=<agent-name> and dispatches to `stub`. The stub's
    auto_ack=True calls bus.ack() (marks it handled in persistence) but
    does NOT publish an Acknowledgment envelope back to the sender.

    Round-trip proof: the `send` tool call returns
    {"status": "published", "id": <envelope_id>} synchronously after
    the bus has dispatched and stub has delivered+acked. The bus is
    single-threaded and dispatches in-process, so delivery is complete
    before the tool returns. We then call `list_pending` on the same
    agent endpoint to confirm the queue count=0 (nothing waiting).

    Transport: fastmcp.Client connected to
    http://<host>:<port>/mcp/<agent>/ (Streamable HTTP). Async methods
    because fastmcp.Client is an async context manager.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
import urllib.parse
from collections.abc import Callable
from typing import Any, cast

try:
    from fastmcp.client import Client as _FastMCPClient

    _HAS_FASTMCP = True
except ImportError:  # pragma: no cover
    _HAS_FASTMCP = False

from mcp.types import TextContent


class _FakeTCPResponse:
    """Mimics a minimal httpx.Response for callers of health_check().

    Used when health_check() falls back to a TCP-connect check because
    the daemon has no dedicated HTTP liveness route. status_code=200
    means the port is accepting connections; status_code=503 means it
    isn't (or the connect timed out).
    """

    def __init__(self, *, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _MCPToolResult:
    """Thin wrapper around a fastmcp tool-call result.

    Mimics the httpx.Response API used by callers so scenario code reads
    the same regardless of whether the underlying transport is raw HTTP
    or MCP-over-HTTP.  status_code=200 means the tool call succeeded;
    status_code=500 means the tool raised.
    """

    def __init__(self, *, status_code: int, data: Any = None, text: str = ""):
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self) -> Any:
        return self._data


class DaemonClient:
    """HTTP client for the agent_core daemon.

    Targets the daemon's HTTP API (default http://127.0.0.1:8787 for the
    Phase 3.5 test instance). One client per scenario; no shared state.

    Transport: MCP tools over FastMCP Streamable HTTP at /mcp/<agent>/.
    send_envelope() and poll_envelopes() are async — call them with
    `await` from async test functions (pytest-asyncio asyncio_mode=auto).

    Preflight finding: the daemon's HTTPHost (Starlette+Uvicorn) has no
    dedicated liveness HTTP route — it only mounts MCP endpoint paths.
    health_check() therefore uses a TCP-connect check on the bind port
    rather than a GET to a specific path. A successful TCP connect means
    the daemon process is up and its HTTP server is accepting connections.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        mcp_agent: str = "qa",
    ):
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._mcp_agent = mcp_agent

    @property
    def _mcp_url(self) -> str:
        """URL of the agent's MCP endpoint on the daemon."""
        return f"{self.base_url}/mcp/{self._mcp_agent}/"

    def health_check(self) -> _FakeTCPResponse:
        """TCP-connect check on the daemon's bind port.

        TCP-connect fallback: the daemon's HTTPHost (Starlette+Uvicorn)
        exposes no dedicated liveness route — only MCP endpoint mounts.
        A successful TCP connect on the bind port indicates the HTTP
        server is accepting connections. Returns a response-like object
        with status_code=200 on success or status_code=503 on failure.
        """
        parsed = urllib.parse.urlparse(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        try:
            conn = socket.create_connection((host, port), timeout=self._timeout)
            conn.close()
            return _FakeTCPResponse(status_code=200, text="TCP connect OK")
        except OSError as exc:
            return _FakeTCPResponse(status_code=503, text=str(exc))

    async def send_envelope(self, envelope: dict[str, Any]) -> _MCPToolResult:
        """Call the `send` MCP tool on the agent's ClaudeCodeMCPEndpoint.

        Transport: fastmcp.Client connected to /mcp/<agent>/ (Streamable
        HTTP). The `send` tool accepts the envelope fields as kwargs and
        publishes to the bus. The bus stamps from_=<agent-name>.

        The tool returns {"status": "published", "id": <envelope_id>}
        synchronously after the bus dispatches and the recipient endpoint
        delivers+acks. This is the round-trip signal for the stub endpoint
        (stub auto-acks but does NOT publish an Acknowledgment reply).

        envelope fields consumed:
            to, kind, payload, correlation_id, in_reply_to, metadata,
            urgency, expires_at. The id field is advisory (daemon
            generates a fresh uuid4 hex for each send call).
        """
        if not _HAS_FASTMCP:  # pragma: no cover
            return _MCPToolResult(
                status_code=500,
                text="fastmcp not installed; add fastmcp>=3.0 to agent-core-qa deps",
            )

        try:
            async with _FastMCPClient(self._mcp_url, timeout=self._timeout) as mcp:
                result = await mcp.call_tool(
                    "send",
                    arguments={
                        "to": envelope["to"],
                        "kind": envelope["kind"],
                        "payload": envelope.get("payload", {}),
                        "correlation_id": envelope.get("correlation_id"),
                        "in_reply_to": envelope.get("in_reply_to"),
                        "metadata": envelope.get("metadata", {}),
                        "urgency": envelope.get("urgency", "green"),
                        "expires_at": envelope.get("expires_at"),
                    },
                )
            # fastmcp 3.x returns a CallToolResult; text content lives in .content.
            data: Any = None
            if result.content and isinstance(result.content[0], TextContent):
                first = result.content[0]
                try:
                    data = json.loads(first.text)
                except json.JSONDecodeError:
                    data = first.text
            return _MCPToolResult(status_code=200, data=data, text=str(data))
        except Exception as exc:
            return _MCPToolResult(status_code=500, text=str(exc))

    async def poll_envelopes(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float = 5.0,
        interval: float = 0.2,
    ) -> dict[str, Any] | None:
        """Poll the agent's pickup queue for an envelope matching predicate.

        Calls the `list_pending` MCP tool on the agent's
        ClaudeCodeMCPEndpoint in a loop until predicate matches or
        timeout expires. Returns the first matching envelope dict,
        or None if nothing matched within timeout.

        Note: for the builtin.stub round-trip, stub does NOT publish an
        Acknowledgment envelope back to the sender — it only calls
        bus.ack() (marks the envelope handled in persistence). Therefore
        list_pending on the sender's queue should return count=0 after a
        successful send. Use poll_envelopes only when a true reply
        envelope is expected (e.g. scheduler, discord).
        """
        if not _HAS_FASTMCP:  # pragma: no cover
            return None

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                async with _FastMCPClient(self._mcp_url, timeout=self._timeout) as mcp:
                    result = await mcp.call_tool("list_pending", arguments={})

                data: Any = None
                if result.content and isinstance(result.content[0], TextContent):
                    try:
                        data = json.loads(result.content[0].text)
                    except json.JSONDecodeError:
                        # Justified: a malformed tool payload is treated as "no
                        # data" and the poll loop retries on the next tick.
                        data = None
                if isinstance(data, dict):
                    for item in data.get("items", []):
                        if predicate(item):
                            return cast(dict[str, Any], item)
            except Exception:
                # Justified: this is a best-effort poll loop over a flaky MCP
                # connection; transient errors are swallowed so it keeps retrying
                # until the deadline, then returns None below.
                pass
            await asyncio.sleep(interval)
        return None

    async def list_pending(self) -> dict[str, Any]:
        """Return the agent's current pending queue snapshot.

        Calls the `list_pending` MCP tool. Returns the raw result dict
        {"meta": {...}, "items": [...]} or {"meta": {}, "items": []} on
        failure.
        """
        if not _HAS_FASTMCP:  # pragma: no cover
            return {"meta": {}, "items": []}

        try:
            async with _FastMCPClient(self._mcp_url, timeout=self._timeout) as mcp:
                result = await mcp.call_tool("list_pending", arguments={})

            if result.content and isinstance(result.content[0], TextContent):
                try:
                    return cast(dict[str, Any], json.loads(result.content[0].text))
                except json.JSONDecodeError:
                    # Justified: a malformed payload falls through to the empty
                    # snapshot returned below.
                    pass
        except Exception:
            # Justified: list_pending is best-effort; any MCP/transport failure
            # degrades to the documented empty snapshot returned below.
            pass
        return {"meta": {}, "items": []}

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> _MCPToolResult:
        """Call any MCP tool on the agent's ClaudeCodeMCPEndpoint.

        Generic escape hatch for tools beyond `send` and `list_pending`.
        Used by Scenario 4 to call `compose_brief` and `submit_brief`
        on the `qa` endpoint (which has the briefs tools mounted via
        ``briefs_orchestrator`` wiring in the test daemon's config).

        Returns an _MCPToolResult with status_code=200 on success (the
        tool returned without raising) and status_code=500 on transport
        error or tool exception. The parsed result is available via
        ``.json()``; the raw text via ``.text``.
        """
        if not _HAS_FASTMCP:  # pragma: no cover
            return _MCPToolResult(
                status_code=500,
                text="fastmcp not installed; add fastmcp>=3.0 to agent-core-qa deps",
            )

        try:
            async with _FastMCPClient(self._mcp_url, timeout=self._timeout) as mcp:
                result = await mcp.call_tool(tool_name, arguments=arguments or {})

            data: Any = None
            if result.content and isinstance(result.content[0], TextContent):
                first = result.content[0]
                try:
                    data = json.loads(first.text)
                except json.JSONDecodeError:
                    data = first.text
            return _MCPToolResult(status_code=200, data=data, text=str(data))
        except Exception as exc:
            return _MCPToolResult(status_code=500, text=str(exc))
