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
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

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
