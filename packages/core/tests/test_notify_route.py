"""HTTPHost /notify/<agent> SSE route: subscribe + initial wake + stream events."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_core.bus.http_host import HTTPHost
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.testing import wait_until


@pytest.mark.asyncio
async def test_notify_route_streams_published_events():
    broker = NotificationBroker()
    host = HTTPHost(bind_port=0, notify_broker=broker, notify_snapshot=lambda _name: None)
    await host.start()
    try:
        url = f"http://127.0.0.1:{host.port}/notify/agent-a"
        events = []
        async with httpx.AsyncClient(timeout=5.0) as client:
            async with client.stream("GET", url) as resp:
                # Drive a publish on a background task once the stream is open.
                async def push_after_delay():
                    await wait_until(
                        lambda: broker._subs.get("agent-a"),
                        message="notify route subscribed to agent-a before publish",
                    )
                    await broker.publish("agent-a", {"meta": {"count": 1}})

                pump = asyncio.create_task(push_after_delay())
                try:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[len("data: ") :]))
                            break  # got our event
                finally:
                    pump.cancel()
        assert events == [{"meta": {"count": 1}}]
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_notify_route_emits_initial_snapshot_on_connect():
    """Under the new minimal wake contract (issue #33), the route always emits
    the initial snapshot when one is provided. The snapshot is a pure "go look"
    signal — its meta carries only ``endpoint`` and ``fired_at``, never a count.
    """
    broker = NotificationBroker()

    def fake_snapshot(name: str) -> dict | None:
        if name == "agent-a":
            return {
                "content": "INBOX: pending (agent-a)",
                "meta": {"endpoint": "agent-a", "fired_at": "2026-05-08T00:00:00+00:00"},
            }
        return None

    host = HTTPHost(bind_port=0, notify_broker=broker, notify_snapshot=fake_snapshot)
    await host.start()
    try:
        url = f"http://127.0.0.1:{host.port}/notify/agent-a"
        events = []
        async with httpx.AsyncClient(timeout=5.0) as client:
            async with client.stream("GET", url) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: ") :]))
                        break  # initial snapshot received
        assert len(events) == 1
        # New-contract drift-guard: meta carries exactly endpoint + fired_at,
        # nothing else. No count, no queue-state metadata.
        assert events[0]["meta"]["endpoint"] == "agent-a"
        assert set(events[0]["meta"].keys()) == {"endpoint", "fired_at"}
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_notify_route_emits_initial_snapshot_even_when_inbox_is_empty():
    """Inverse of the old count-zero suppression test.

    Under the new contract the wake snapshot has no count field — its sole
    purpose on relay-connect is to nudge the agent to call ``list_pending``.
    Whether the inbox actually has mail is the agent's problem, not the
    route's. So when ``snapshot()`` returns a snapshot, the route MUST emit
    it on connect, even if the underlying queue would be empty.
    """
    broker = NotificationBroker()

    # Snapshot shape no longer carries count; the route can't know (and must
    # not care) whether the inbox is empty. _build_wake_summary returns a
    # snapshot regardless — see Bus.snapshot_for_agent.
    def fake_snapshot(_name: str) -> dict | None:
        return {
            "content": "INBOX: pending (agent-a)",
            "meta": {"endpoint": "agent-a", "fired_at": "2026-05-08T00:00:00+00:00"},
        }

    host = HTTPHost(bind_port=0, notify_broker=broker, notify_snapshot=fake_snapshot)
    await host.start()
    try:
        url = f"http://127.0.0.1:{host.port}/notify/agent-a"
        events = []
        async with httpx.AsyncClient(timeout=5.0) as client:
            async with client.stream("GET", url) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: ") :]))
                        break  # initial snapshot received
        # The first frame on connect is the snapshot — emitted regardless of
        # whether the inbox actually has pending mail.
        assert len(events) == 1
        assert events[0]["meta"]["endpoint"] == "agent-a"
        assert set(events[0]["meta"].keys()) == {"endpoint", "fired_at"}
        # No count field anywhere in the new contract.
        assert "count" not in events[0]["meta"]
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_notify_route_unsubscribes_on_disconnect():
    broker = NotificationBroker()
    host = HTTPHost(bind_port=0, notify_broker=broker, notify_snapshot=lambda _: None)
    await host.start()
    try:
        url = f"http://127.0.0.1:{host.port}/notify/agent-a"
        async with httpx.AsyncClient(timeout=2.0) as client:
            async with client.stream("GET", url):
                # Open and close immediately.
                pass
        # Give the server a moment to run the unsubscribe finally block.
        await wait_until(
            lambda: "agent-a" not in broker._subs,
            message="agent-a subscriber set cleaned up after disconnect",
        )
        # The agent-a subscriber set should be cleaned up.
        assert "agent-a" not in broker._subs
    finally:
        await host.stop()
