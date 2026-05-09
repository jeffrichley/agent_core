"""Issue #67: consume + reply tools to drop the per-round-trip floor to 2.

``consume`` combines ``list_pending`` + ``handle``; ``reply`` combines ``send``
+ ``handle(inbound)``. Both are additive — the existing tools stay registered.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from agent_core.bus.envelope import AcknowledgmentPayload, Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _RecordingHandle:
    def __init__(self) -> None:
        self.published: list[Envelope] = []
        self.acked: list[str] = []
        self.nacked: list[tuple[str, bool]] = []

    async def publish(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None:
        self.acked.append(envelope_id)

    async def nack(self, envelope_id: str, requeue: bool = True) -> None:
        self.nacked.append((envelope_id, requeue))

    def endpoints(self) -> list:
        return []


def _inbound_text(
    env_id: str,
    *,
    from_: str = "discord",
    text: str = "hi",
    metadata: dict[str, Any] | None = None,
    urgency: str = "green",
) -> Envelope:
    return Envelope(
        id=env_id,
        correlation_id=f"corr-{env_id}",
        in_reply_to=None,
        from_=from_,
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        metadata=metadata or {},
        urgency=urgency,  # type: ignore[arg-type]
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_consume_returns_same_shape_as_list_pending() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("a"))
        ep.queue_for_pickup(_inbound_text("b"))

        async with Client(ep._mcp) as client:
            # Use matching batch_window so the shapes line up directly.
            cons = await client.call_tool(
                "consume", {"auto_ack": False, "batch_window_seconds": 0}
            )
            lp = await client.call_tool("list_pending", {})

        assert cons.data["meta"]["count"] == 2  # type: ignore[index]
        assert {i["id"] for i in cons.data["items"]} == {"a", "b"}  # type: ignore[index]
        assert cons.data["meta"]["count"] == lp.data["meta"]["count"]  # type: ignore[index]
        assert {i["id"] for i in cons.data["items"]} == {  # type: ignore[index]
            i["id"] for i in lp.data["items"]  # type: ignore[index]
        }
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_consume_auto_ack_true_acks_and_clears() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("a"))
        ep.queue_for_pickup(_inbound_text("b"))

        async with Client(ep._mcp) as client:
            await client.call_tool("consume", {})  # auto_ack=True default

        assert sorted(handle.acked) == ["a", "b"]
        assert ep._pending == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_consume_auto_ack_false_does_not_ack() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("a"))

        async with Client(ep._mcp) as client:
            await client.call_tool("consume", {"auto_ack": False})

        assert handle.acked == []
        assert [e.id for e in ep._pending] == ["a"]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_consume_max_items_zero_acks_nothing() -> None:
    """max_items=0 returns no items and acks none — useful for callers that
    want to peek at meta without committing to processing."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("a", from_="src-a"))
        ep.queue_for_pickup(_inbound_text("b", from_="src-b"))

        async with Client(ep._mcp) as client:
            cons = await client.call_tool("consume", {"max_items": 0})

        assert cons.data["items"] == []  # type: ignore[index]
        assert cons.data["meta"]["count"] == 2  # type: ignore[index]
        assert handle.acked == []
        assert {e.id for e in ep._pending} == {"a", "b"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_consume_max_items_trims_items_meta_unchanged() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        # Use distinct senders so the items don't collapse into one batch
        # under the default 30s batch_window.
        ep.queue_for_pickup(_inbound_text("a", from_="src-a"))
        ep.queue_for_pickup(_inbound_text("b", from_="src-b"))
        ep.queue_for_pickup(_inbound_text("c", from_="src-c"))

        async with Client(ep._mcp) as client:
            cons = await client.call_tool("consume", {"auto_ack": False, "max_items": 2})

        assert len(cons.data["items"]) == 2  # type: ignore[index]
        # meta still reports total queue state (3) — trimming is presentational
        assert cons.data["meta"]["count"] == 3  # type: ignore[index]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_consume_auto_ack_walks_batched_items() -> None:
    """With batch_window_seconds > 0, items can collapse into batch entries.
    auto_ack must reach into batch envelopes and ack each underlying id."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        # Two envelopes from same sender, same kind, same urgency, near in time
        # → should batch under list_pending(batch_window_seconds=30) shape.
        now = datetime.now(UTC)
        e1 = _inbound_text("a").model_copy(update={"created_at": now})
        e2 = _inbound_text("b").model_copy(update={"created_at": now})
        ep.queue_for_pickup(e1)
        ep.queue_for_pickup(e2)

        async with Client(ep._mcp) as client:
            await client.call_tool("consume", {"batch_window_seconds": 30})

        assert sorted(handle.acked) == ["a", "b"]
        assert ep._pending == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_reply_publishes_and_acks_atomically() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        inbound = _inbound_text(
            "in-1",
            from_="discord-pepper",
            metadata={"discord": {"channel_id": "C1", "guild_id": "G1"}},
        )
        ep.queue_for_pickup(inbound)

        async with Client(ep._mcp) as client:
            res = await client.call_tool(
                "reply",
                {
                    "in_reply_to": "in-1",
                    "payload": {"kind": "TextMessage", "text": "got it"},
                },
            )

        assert res.data["acked_envelope_id"] == "in-1"  # type: ignore[index]
        published_id = res.data["published_envelope_id"]  # type: ignore[index]
        assert "in-1" in handle.acked
        assert len(handle.published) == 1
        out = handle.published[0]
        assert out.id == published_id
        assert out.in_reply_to == "in-1"
        assert out.to == "discord-pepper"
        assert out.kind == "TextMessage"
        assert out.metadata == {"discord": {"channel_id": "C1", "guild_id": "G1"}}
        # urgency default is green (does NOT inherit inbound urgency)
        assert out.urgency == "green"
        assert out.correlation_id == inbound.correlation_id
        assert ep._pending == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_reply_default_urgency_is_green_not_inherited() -> None:
    """Pepper pushback: a reply to a red message should not auto-promote to red.
    urgency default is 'green' (matches send()); 'auto' is opt-in inherit."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("in-r", urgency="red"))

        async with Client(ep._mcp) as client:
            await client.call_tool(
                "reply",
                {"in_reply_to": "in-r", "payload": {"kind": "TextMessage", "text": "ack"}},
            )

        assert handle.published[0].urgency == "green"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_reply_urgency_auto_inherits_inbound() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("in-r", urgency="red"))

        async with Client(ep._mcp) as client:
            await client.call_tool(
                "reply",
                {
                    "in_reply_to": "in-r",
                    "payload": {"kind": "TextMessage", "text": "escalating"},
                    "urgency": "auto",
                },
            )

        assert handle.published[0].urgency == "red"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_reply_explicit_urgency_overrides() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("in-y", urgency="green"))

        async with Client(ep._mcp) as client:
            await client.call_tool(
                "reply",
                {
                    "in_reply_to": "in-y",
                    "payload": {"kind": "TextMessage", "text": "warn"},
                    "urgency": "yellow",
                },
            )

        assert handle.published[0].urgency == "yellow"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_reply_metadata_override_wins_per_key() -> None:
    """Shallow merge: top-level keys in the override replace inbound's.
    Documented limitation — see the tool's instructions for details."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        inbound_meta = {"discord": {"channel_id": "X"}, "trace_id": "T1"}
        ep.queue_for_pickup(_inbound_text("in-m", metadata=inbound_meta))

        async with Client(ep._mcp) as client:
            await client.call_tool(
                "reply",
                {
                    "in_reply_to": "in-m",
                    "payload": {"kind": "TextMessage", "text": "ok"},
                    "metadata": {"trace_id": "T2"},  # override only trace_id
                },
            )

        out_meta = handle.published[0].metadata
        assert out_meta["trace_id"] == "T2"
        assert out_meta["discord"] == {"channel_id": "X"}  # untouched
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_reply_after_consume_uses_recent_inbounds_cache() -> None:
    """The most important compose-case: consume(auto_ack=True) + reply.
    consume removes the inbound from _pending; reply must still find its
    routing via the _recent_inbounds cache."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(
            _inbound_text(
                "in-1",
                from_="discord-pepper",
                metadata={"discord": {"channel_id": "C1"}},
            )
        )

        async with Client(ep._mcp) as client:
            await client.call_tool("consume", {})  # auto_ack=True
            assert ep._pending == []
            # inbound is no longer in _pending; reply must use the cache
            res = await client.call_tool(
                "reply",
                {
                    "in_reply_to": "in-1",
                    "payload": {"kind": "TextMessage", "text": "after consume"},
                },
            )

        assert res.data["acked_envelope_id"] == "in-1"  # type: ignore[index]
        assert handle.published[0].to == "discord-pepper"
        assert handle.published[0].metadata == {"discord": {"channel_id": "C1"}}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_reply_after_inbound_evicted_from_cache_raises() -> None:
    """If the inbound has aged out of _recent_inbounds and is no longer in
    _pending, reply() must surface a clear error rather than silently
    fabricating routing."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("in-evict"))

        async with Client(ep._mcp) as client:
            await client.call_tool("consume", {})  # auto_ack=True drains _pending
            assert "in-evict" in ep._recent_inbounds
            ep._recent_inbounds.clear()  # simulate TTL eviction

            with pytest.raises(ToolError, match="no inbound found"):
                await client.call_tool(
                    "reply",
                    {
                        "in_reply_to": "in-evict",
                        "payload": {"kind": "TextMessage", "text": "too late"},
                    },
                )
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_consume_partial_ack_failure_leaves_pending_intact() -> None:
    """Important issue from review: if a mid-walk ack raises, _pending must
    not be partially mutated — caller can retry the whole consume safely
    and the bus's idempotent acks make the retry correct."""

    class _MidwalkFailingHandle(_RecordingHandle):
        def __init__(self, fail_on: str) -> None:
            super().__init__()
            self._fail_on = fail_on

        async def ack(self, envelope_id: str) -> None:
            if envelope_id == self._fail_on:
                raise RuntimeError("simulated bus ack failure")
            await super().ack(envelope_id)

    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _MidwalkFailingHandle(fail_on="b")
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        for env_id in ("a", "b", "c"):
            ep.queue_for_pickup(_inbound_text(env_id, from_=f"src-{env_id}"))

        async with Client(ep._mcp) as client:
            with pytest.raises(ToolError, match="simulated bus ack failure"):
                await client.call_tool("consume", {})

        # _pending must be untouched so the caller's retry sees the same items
        assert {e.id for e in ep._pending} == {"a", "b", "c"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_reply_unknown_in_reply_to_raises() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        async with Client(ep._mcp) as client:
            with pytest.raises(ToolError, match="no inbound found"):
                await client.call_tool(
                    "reply",
                    {
                        "in_reply_to": "ghost",
                        "payload": {"kind": "TextMessage", "text": "nope"},
                    },
                )
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_reply_registers_outbound_for_missing_ack_timer() -> None:
    """reply() should populate _recent_outbound_ids the same way send() does,
    so the missing-ack-timer path stays wired up."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("in-1"))

        async with Client(ep._mcp) as client:
            res = await client.call_tool(
                "reply",
                {
                    "in_reply_to": "in-1",
                    "payload": {"kind": "TextMessage", "text": "hi"},
                },
            )

        published_id = res.data["published_envelope_id"]  # type: ignore[index]
        assert published_id in ep._recent_outbound_ids
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_existing_tools_remain_registered() -> None:
    """Additive: list_pending, handle, send must still be callable."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        async with Client(ep._mcp) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
        assert {"list_pending", "handle", "send", "consume", "reply"} <= names
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_populates_recent_inbounds() -> None:
    """deliver() of a non-auto-cleared envelope captures routing into the
    cache so subsequent reply() can find it after the inbound is acked."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        # No session connected, so deliver raises EndpointUnavailable after
        # queueing — but the cache should still be populated.
        from agent_core.bus.protocol import EndpointUnavailable

        env = _inbound_text("in-cache", from_="discord")
        with pytest.raises(EndpointUnavailable):
            await ep.deliver(env)
        assert "in-cache" in ep._recent_inbounds
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_auto_cleared_ack_does_not_populate_recent_inbounds() -> None:
    """Auto-cleared routine green acks must not pollute the inbound cache."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ack = Envelope(
            id="ack-x",
            correlation_id="c-ack-x",
            in_reply_to="out-1",
            from_="discord",
            to="agent",
            kind="Acknowledgment",
            payload=AcknowledgmentPayload(of="out-1", note='{"status": "sent"}'),
            urgency="green",
            created_at=datetime.now(UTC),
        )
        await ep.deliver(ack)
        assert "ack-x" not in ep._recent_inbounds
    finally:
        await ep.stop()
