"""BusTailMCPEndpoint — read-only MCP surface for bus-state debugging.

Standalone endpoint type (not auto-attached to agents). Hosts its own
FastMCP server, mounted on the bus's existing HTTPHost at its configured
path. Persistence is resolved via BusHandle.persistence() during start().

The four MCP tools (tail, get_envelope, trace_correlation, metrics) are
registered on the FastMCP server in this endpoint's __init__ via
register_bus_tail_tools — see bus_tail/mcp.py.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from agent_core.bus_tail.reader import PersistenceReader

if TYPE_CHECKING:
    from agent_core.bus.envelope import Envelope
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)

DEFAULT_MOUNT = "/mcp/bus-tail"


class BusTailMCPEndpoint:
    """Read-only MCP endpoint exposing bus state for debugging."""

    def __init__(self, *, name: str, mount: str | None = None) -> None:
        self.name = name
        self.mount = mount if mount is not None else DEFAULT_MOUNT
        self._reader: PersistenceReader | None = None
        self._handle: BusHandle | None = None
        self._mcp: FastMCP = FastMCP(
            name,
            instructions=(
                "Read-only audit/tail surface for the bus. Tools: tail (recent "
                "envelope listing with schema-summary previews), get_envelope "
                "(full payload of one envelope by id), trace_correlation (full "
                "chain by correlation_id), metrics (last-24h aggregates)."
            ),
        )
        # Tools are registered in Task 5.

    # --- Endpoint Protocol ---

    async def start(self, bus: BusHandle) -> None:
        self._handle = bus
        self._reader = PersistenceReader(bus.persistence())
        log.info("BusTailMCPEndpoint(name=%s) started at mount=%s", self.name, self.mount)

    async def deliver(self, envelope: Envelope) -> None:
        # Nothing should address bus-tail. If something does, ack to avoid
        # bus-side requeue/back-off. This is a defensive no-op.
        if self._handle is None:
            log.warning(
                "BusTailMCPEndpoint(name=%s) received envelope before start: %s",
                self.name,
                envelope.id,
            )
            return
        await self._handle.ack(envelope.id)
        log.debug(
            "BusTailMCPEndpoint(name=%s) auto-acked unexpected delivery: %s",
            self.name,
            envelope.id,
        )

    async def stop(self) -> None:
        self._reader = None
        self._handle = None
        log.info("BusTailMCPEndpoint(name=%s) stopped", self.name)

    # --- MCPHostable Protocol ---

    def asgi_app(self):
        """Return the ASGI app for this endpoint's FastMCP server."""
        return self._mcp.http_app(path="/")


__all__ = ["BusTailMCPEndpoint", "DEFAULT_MOUNT"]
