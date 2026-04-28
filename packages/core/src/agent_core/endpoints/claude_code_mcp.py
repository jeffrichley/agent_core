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
