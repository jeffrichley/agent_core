"""NotificationBroker: per-agent fan-out for /notify/<agent> subscribers."""

from __future__ import annotations

import asyncio

import pytest

from agent_core.bus.notify_broker import NotificationBroker


@pytest.mark.asyncio
async def test_subscribe_returns_queue():
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")
    assert isinstance(q, asyncio.Queue)


@pytest.mark.asyncio
async def test_publish_fans_out_to_all_subscribers_for_agent():
    broker = NotificationBroker()
    q1 = await broker.subscribe("agent-a")
    q2 = await broker.subscribe("agent-a")
    await broker.publish("agent-a", {"hello": 1})
    assert q1.get_nowait() == {"hello": 1}
    assert q2.get_nowait() == {"hello": 1}


@pytest.mark.asyncio
async def test_publish_isolates_by_agent():
    broker = NotificationBroker()
    q_a = await broker.subscribe("agent-a")
    q_b = await broker.subscribe("agent-b")
    await broker.publish("agent-a", {"hello": 1})
    assert q_a.get_nowait() == {"hello": 1}
    assert q_b.empty()


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue():
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")
    await broker.unsubscribe("agent-a", q)
    await broker.publish("agent-a", {"hello": 1})
    # Original queue receives nothing because it was unsubscribed.
    assert q.empty()


@pytest.mark.asyncio
async def test_unsubscribe_empty_set_removes_agent_key():
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")
    await broker.unsubscribe("agent-a", q)
    # Internal: agent key cleaned up so dict doesn't grow forever.
    assert "agent-a" not in broker._subs


@pytest.mark.asyncio
async def test_publish_to_unknown_agent_is_noop():
    broker = NotificationBroker()
    # No subscribers, no exception.
    await broker.publish("agent-ghost", {"hello": 1})


@pytest.mark.asyncio
async def test_full_queue_drops_event_with_warning(caplog):
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")
    # Fill the bounded queue (default maxsize=128).
    for i in range(128):
        q.put_nowait({"i": i})
    with caplog.at_level("WARNING"):
        await broker.publish("agent-a", {"overflow": True})
    assert any("dropped" in rec.message for rec in caplog.records)
    # Original 128 still present.
    assert q.qsize() == 128
