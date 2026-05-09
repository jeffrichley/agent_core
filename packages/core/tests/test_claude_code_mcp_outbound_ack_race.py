"""Issue #69: regression test for the publish/register ordering race.

The single-loop bus dispatches in-process. When Pepper's ``send`` or
``reply`` tool ``await``s ``handle.publish(outbound)``, the discord-pepper
adapter publishes a routine green Acknowledgment back through the same
handle, and the bus dispatches that ack to Pepper's ``deliver()`` —
**all before** the original ``publish`` call returns.

Pre-fix order in the tool body was: publish → register. That meant the
ack's auto-clear path saw an empty ``_recent_outbound_ids`` and an empty
``_missing_ack_tasks`` (cancellation was a no-op), and the
post-publish ``_register_outbound_sent`` then scheduled a missing-ack
timer for an envelope that had **already been acked**. 30 seconds later
that timer fired a wake (`INBOX: pending`) and pulled Pepper into a
useless model turn.

The fix: register the outbound (and schedule the timer) **before**
publishing, so an in-flight ack's cancellation actually has something
to cancel. On publish failure, clean up the registry + timer.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from agent_core.bus.envelope import AcknowledgmentPayload, Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _AckEchoHandle:
    """Fake BusHandle that simulates the in-process bus: when an outbound
    is published, a routine green Acknowledgment is delivered to the
    endpoint *before* publish returns."""

    def __init__(self, endpoint: ClaudeCodeMCPEndpoint) -> None:
        self.endpoint = endpoint
        self.published: list[Envelope] = []
        self.acked: list[str] = []

    async def publish(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        self.published.append(envelope)
        # Build the routine green ack the way an adapter would
        ack = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=envelope.correlation_id,
            in_reply_to=envelope.id,
            from_="discord",
            to=self.endpoint.name,
            kind="Acknowledgment",
            payload=AcknowledgmentPayload(
                of=envelope.id, note='{"status": "sent", "message_ids": ["m1"]}'
            ),
            urgency="green",
            created_at=datetime.now(UTC),
        )
        await self.endpoint.deliver(ack)

    async def ack(self, envelope_id: str) -> None:
        self.acked.append(envelope_id)

    async def nack(self, envelope_id: str, requeue: bool = True) -> None:
        pass

    def endpoints(self) -> list:
        return []


class _FailingPublishHandle(_AckEchoHandle):
    async def publish(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        raise RuntimeError("simulated bus publish failure")


@pytest.mark.asyncio
async def test_send_does_not_leak_missing_ack_timer_when_ack_arrives_during_publish() -> None:
    """The recipient's deliver() runs while publish() is awaiting. The
    routine ack must be auto-cleared AND the registry/timer must be
    properly empty after send() returns — otherwise a missing-ack timer
    will fire ~30s later and emit the spurious wake observed in #69."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    handle = _AckEchoHandle(ep)
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        async with Client(ep._mcp) as client:
            res = await client.call_tool(
                "send",
                {
                    "to": "discord",
                    "kind": "TextMessage",
                    "payload": {"kind": "TextMessage", "text": "hi"},
                },
            )
        published_id = res.data["id"]  # type: ignore[index]
        assert published_id in {e.id for e in handle.published}
        # The ack was delivered during publish; auto-clear must have run AND
        # the registry / missing-ack timer for the outbound must be empty.
        assert published_id not in ep._recent_outbound_ids, (
            "outbound left in registry — missing-ack timer will fire as a wake"
        )
        assert published_id not in ep._missing_ack_tasks, (
            "missing-ack timer task still scheduled — it will fire as a wake"
        )
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_reply_does_not_leak_missing_ack_timer_when_ack_arrives_during_publish() -> None:
    """Same race for reply() — Pepper's primary outbound path under #67."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    handle = _AckEchoHandle(ep)
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        # Seed a pending inbound that reply() can answer
        inbound = Envelope(
            id="in-1",
            correlation_id="corr-in-1",
            in_reply_to=None,
            from_="discord",
            to="pepper",
            kind="TextMessage",
            payload=TextMessagePayload(text="ping"),
            metadata={"discord": {"channel_id": "C1"}},
            urgency="green",
            created_at=datetime.now(UTC),
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
        published_id = res.data["published_envelope_id"]  # type: ignore[index]
        assert published_id not in ep._recent_outbound_ids
        assert published_id not in ep._missing_ack_tasks
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_publish_failure_cleans_up_registry_and_timer() -> None:
    """If publish raises, the pre-publish registration must be rolled back —
    otherwise a phantom missing-ack timer would fire 30s later for an
    envelope that never went out."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    handle = _FailingPublishHandle(ep)
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        async with Client(ep._mcp) as client:
            with pytest.raises(ToolError, match="simulated bus publish failure"):
                await client.call_tool(
                    "send",
                    {
                        "to": "discord",
                        "kind": "TextMessage",
                        "payload": {"kind": "TextMessage", "text": "hi"},
                    },
                )

        # No outbound should be hanging around in the registry or timer table
        assert ep._recent_outbound_ids == {}
        assert ep._missing_ack_tasks == {}
    finally:
        await ep.stop()


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_send_does_not_emit_wake_through_missing_ack_path_under_in_flight_ack() -> None:
    """End-to-end: send() under the in-flight ack scenario must not produce
    a wake even if we advance virtual time past the missing-ack timeout."""

    class _Recorder:
        def __init__(self) -> None:
            self.sent: list[Any] = []

        async def send_message(self, message) -> None:
            self.sent.append(message)

    ep = ClaudeCodeMCPEndpoint(
        name="pepper", mount="/mcp/pepper", missing_ack_default_seconds=0.05
    )
    # Speed up the debounce timers so any spurious wake would land fast.
    ep._notify_debounce_seconds_by_urgency = {"red": 0.01, "yellow": 0.02, "green": 0.03}
    handle = _AckEchoHandle(ep)
    await ep.start(handle)  # type: ignore[arg-type]
    rec = _Recorder()
    ep._register_session(rec)
    try:
        async with Client(ep._mcp) as client:
            await client.call_tool(
                "send",
                {
                    "to": "discord",
                    "kind": "TextMessage",
                    "payload": {"kind": "TextMessage", "text": "hi"},
                },
            )
        # Advance virtual time well past the missing-ack timeout. With the
        # bug, a wake would fire at ~0.05s. With the fix, no wake.
        await asyncio.sleep(0.5)
        assert rec.sent == [], (
            "missing-ack timer fired a wake even though the routine ack "
            "arrived during publish — issue #69"
        )
    finally:
        await ep.stop()
