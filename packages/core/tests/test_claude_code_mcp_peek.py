"""Issue #70: peek(envelope_id) returns one envelope without acking."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _RecordingHandle:
    def __init__(self) -> None:
        self.published: list[Envelope] = []
        self.acked: list[str] = []

    async def publish(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None:
        self.acked.append(envelope_id)

    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...

    def endpoints(self) -> list:
        return []


def _inbound_text(env_id: str, *, text: str = "hi", from_: str = "discord") -> Envelope:
    return Envelope(
        id=env_id,
        correlation_id=f"corr-{env_id}",
        in_reply_to=None,
        from_=from_,
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        urgency="green",
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_peek_returns_envelope_when_pending() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("e-1", text="hello"))

        async with Client(ep._mcp) as client:
            res = await client.call_tool("peek", {"envelope_id": "e-1"})

        envelope = res.data["envelope"]  # type: ignore[index]
        assert envelope["id"] == "e-1"
        assert envelope["payload"]["text"] == "hello"
        assert envelope["from"] == "discord"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_peek_does_not_ack_or_remove_from_pending() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("e-2"))

        async with Client(ep._mcp) as client:
            await client.call_tool("peek", {"envelope_id": "e-2"})

        assert handle.acked == []
        assert any(e.id == "e-2" for e in ep._pending)
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_peek_is_idempotent() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("e-3", text="same"))

        async with Client(ep._mcp) as client:
            r1 = await client.call_tool("peek", {"envelope_id": "e-3"})
            r2 = await client.call_tool("peek", {"envelope_id": "e-3"})

        assert r1.data == r2.data  # type: ignore[index]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_peek_raises_when_envelope_id_missing() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        async with Client(ep._mcp) as client:
            with pytest.raises(ToolError, match="not in queue"):
                await client.call_tool("peek", {"envelope_id": "ghost"})
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_peek_does_not_consult_recent_inbounds_cache() -> None:
    """The _recent_inbounds cache holds routing only, not full payload.
    peek() is a payload-fetch tool, so it must look only at _pending."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        # Manually populate _recent_inbounds without queueing — simulates an
        # envelope that was acked but routing still cached.
        ep._recent_inbounds["cached-only"] = {
            "from": "discord",
            "to": "agent",
            "kind": "TextMessage",
            "metadata": {},
            "urgency": "green",
            "correlation_id": "corr-cached-only",
            "registered_at": 0.0,
        }
        async with Client(ep._mcp) as client:
            with pytest.raises(ToolError, match="not in queue"):
                await client.call_tool("peek", {"envelope_id": "cached-only"})
    finally:
        await ep.stop()
