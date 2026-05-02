"""ClaudeCodeMCPEndpoint publishes to the broker on each push."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.bus.protocol import EndpointUnavailable
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


def _env(eid: str = "e1", urgency: str = "green") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="src",
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=eid),
        urgency=urgency,
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_endpoint_publishes_to_broker_when_session_active():
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")

    class _RecordingSession:
        async def send_message(self, _msg) -> None:
            pass

    ep = ClaudeCodeMCPEndpoint(name="agent-a", mount="/mcp/a", notify_broker=broker)
    ep._register_session(_RecordingSession())
    ep._pending = [_env("e1", urgency="red")]

    await ep._notify_mail_arrived("red")
    await asyncio.sleep(0.1)  # let debounce fire

    event = q.get_nowait()
    assert event["meta"]["count"] == 1
    assert event["meta"]["endpoint"] == "agent-a"


@pytest.mark.asyncio
async def test_endpoint_publishes_to_broker_even_when_no_session():
    """Even with no Claude Code session attached, deliver() should still fan
    out to the broker — that's the whole point of the relay path. The HTTP
    push leg is unavailable, so deliver() also raises EndpointUnavailable.

    This test calls deliver() directly (not _notify_mail_arrived) so it locks
    in the production contract end-to-end.
    """
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")

    ep = ClaudeCodeMCPEndpoint(name="agent-a", mount="/mcp/a", notify_broker=broker)
    # No session registered.

    with pytest.raises(EndpointUnavailable):
        await ep.deliver(_env("e1", urgency="red"))

    event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert event["meta"]["count"] == 1


@pytest.mark.asyncio
async def test_deliver_publishes_to_broker_when_no_session_attached():
    """deliver() must fan out to the broker even when no HTTP MCP session is connected.

    The relay maintains its own SSE subscription independent of direct HTTP push.
    Without this contract, an agent connected only via the stdio relay (not via
    HTTP MCP) would never wake on new arrivals.
    """
    broker = NotificationBroker()
    queue = await broker.subscribe("agent")

    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent", notify_broker=broker)
    assert not ep._sessions

    env = _env("e1", urgency="red")
    env = env.model_copy(update={"to": "agent"})

    # deliver() should still raise EndpointUnavailable (HTTP push unavailable)
    # but the broker MUST receive the fan-out summary.
    with pytest.raises(EndpointUnavailable):
        await ep.deliver(env)

    # Wait for the red debounce window to fire (default ~50ms; bound generously).
    summary = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert summary["meta"]["count"] == 1
    assert summary["meta"]["endpoint"] == "agent"


@pytest.mark.asyncio
async def test_endpoint_with_no_broker_still_works():
    """Back-compat: endpoint constructed without a broker is fine."""
    ep = ClaudeCodeMCPEndpoint(name="agent-a", mount="/mcp/a")  # no broker
    ep._pending = [_env("e1", urgency="red")]

    # Should not raise.
    await ep._notify_mail_arrived("red")
    await asyncio.sleep(0.1)
