"""Issue #70: BusClient — persistent MCP client to the bus daemon."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core_channel.bus_client import BusClient


class _StubHandle:
    async def ack(self, envelope_id: str) -> None: ...
    async def publish(self, envelope, to=None) -> None: ...
    async def nack(self, envelope_id, requeue=True) -> None: ...
    def endpoints(self) -> list:
        return []


def _inbound(env_id: str, *, text: str = "hi") -> Envelope:
    return Envelope(
        id=env_id,
        correlation_id=f"corr-{env_id}",
        in_reply_to=None,
        from_="discord",
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        urgency="green",
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_bus_client_consume_no_ack_returns_items() -> None:
    """In-process: BusClient connected directly to the FastMCP server
    returns consume() items without acking them."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound("e-1", text="hello"))

        # Connect a BusClient using the in-process FastMCP server directly.
        async with BusClient.from_in_process(ep._mcp) as bus:
            snapshot = await bus.consume_no_ack(batch_window_seconds=0)

        assert snapshot["meta"]["count"] == 1
        items = snapshot["items"]
        assert len(items) == 1
        # auto_ack=False: envelope is still in the queue.
        assert any(e.id == "e-1" for e in ep._pending)
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_bus_client_passes_batch_window_through() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound("e-a"))
        ep.queue_for_pickup(_inbound("e-b"))

        async with BusClient.from_in_process(ep._mcp) as bus:
            snapshot_flat = await bus.consume_no_ack(batch_window_seconds=0)
            snapshot_batched = await bus.consume_no_ack(batch_window_seconds=30)

        # batch_window_seconds=0 returns flat envelope dicts.
        assert "id" in snapshot_flat["items"][0]
        # batch_window_seconds=30 returns single/batch shapes.
        assert "type" in snapshot_batched["items"][0]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_bus_client_does_not_ack() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound("e-noack"))

        async with BusClient.from_in_process(ep._mcp) as bus:
            await bus.consume_no_ack(batch_window_seconds=0)

        # _pending still has the envelope (not acked).
        assert any(e.id == "e-noack" for e in ep._pending)
    finally:
        await ep.stop()
