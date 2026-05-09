"""Issue #70: persistent MCP client connection from the relay to the bus.

The relay calls consume(auto_ack=False, batch_window_seconds=N) on every
wake to fetch the queue snapshot. This client wraps fastmcp.Client with
the relay-specific contract (no ack, parameter shape).

Connection lifecycle: opened on relay startup, kept alive for the relay's
lifetime. Reconnects on connection loss are handled by fastmcp.Client's
underlying transport — when call_tool raises a transport error, the
caller (stdio_server) catches and falls back to the bare-wake shape for
that wake. Subsequent wakes get a fresh attempt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from fastmcp import Client

if TYPE_CHECKING:
    from fastmcp import FastMCP


class BusClient:
    """Persistent MCP client to the bus daemon's per-agent endpoint.

    Two construction modes:

    - ``BusClient(daemon_url, agent)`` — production: connects to
      ``{daemon_url}/mcp/{agent}`` over HTTP.
    - ``BusClient.from_in_process(fastmcp_server)`` — tests: connects
      directly to a FastMCP server instance, no HTTP transport.

    Use as an async context manager. ``consume_no_ack`` is the single
    operation the relay needs; further tools (peek, etc.) can be added
    here as the relay grows.
    """

    # Typed as Any to accommodate the two Client transport variants:
    # Client[StreamableHttpTransport] (HTTP mode) and
    # Client[FastMCPTransport] (in-process mode). Both share the same
    # call_tool / __aenter__ / __aexit__ interface.
    _client: Any

    def __init__(self, daemon_url: str, agent: str) -> None:
        url = f"{daemon_url.rstrip('/')}/mcp/{agent}"
        self._client = Client(url)

    @classmethod
    def from_in_process(cls, server: FastMCP) -> Self:
        instance = cls.__new__(cls)
        instance._client = Client(server)
        return instance

    async def __aenter__(self) -> Self:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        await self._client.__aexit__(exc_type, exc, tb)

    async def consume_no_ack(self, *, batch_window_seconds: int = 30) -> dict:
        """Fetch the queue snapshot without acking any envelopes.

        Returns the consume() response shape:
        ``{"meta": {...}, "items": [...]}``.
        """
        result = await self._client.call_tool(
            "consume",
            {
                "auto_ack": False,
                "batch_window_seconds": batch_window_seconds,
            },
        )
        return result.data
