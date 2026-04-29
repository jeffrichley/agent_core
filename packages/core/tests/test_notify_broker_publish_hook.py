"""ClaudeCodeMCPEndpoint publishes to the broker on each push."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


def _env(eid: str = "e1") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="src",
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=eid),
        urgency="green",
        created_at=datetime.now(timezone.utc),
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
    ep._pending = [_env("e1")]

    await ep._notify_mail_arrived()
    await asyncio.sleep(0.1)  # let debounce fire

    event = q.get_nowait()
    assert event["meta"]["count"] == 1
    assert event["meta"]["endpoint"] == "agent-a"


@pytest.mark.asyncio
async def test_endpoint_publishes_to_broker_even_when_no_session():
    """Even with no Claude Code session attached, the relay should still
    receive notifications — that's the whole point of the relay path.
    """
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")

    ep = ClaudeCodeMCPEndpoint(name="agent-a", mount="/mcp/a", notify_broker=broker)
    # No session registered.
    ep._pending = [_env("e1")]

    await ep._notify_mail_arrived()
    await asyncio.sleep(0.1)

    event = q.get_nowait()
    assert event["meta"]["count"] == 1


@pytest.mark.asyncio
async def test_endpoint_with_no_broker_still_works():
    """Back-compat: endpoint constructed without a broker is fine."""
    ep = ClaudeCodeMCPEndpoint(name="agent-a", mount="/mcp/a")  # no broker
    ep._pending = [_env("e1")]

    # Should not raise.
    await ep._notify_mail_arrived()
    await asyncio.sleep(0.1)
