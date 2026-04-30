"""HTTPHost /notify/<agent> SSE route: subscribe + initial wake + stream events."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_core.bus.http_host import HTTPHost
from agent_core.bus.notify_broker import NotificationBroker


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
                    await asyncio.sleep(0.2)
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
async def test_notify_route_emits_initial_snapshot_when_pending_exists():
    broker = NotificationBroker()

    def fake_snapshot(name: str) -> dict | None:
        if name == "agent-a":
            return {"content": "INBOX: 1 pending", "meta": {"count": 1, "endpoint": "agent-a"}}
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
        assert events[0]["meta"]["count"] == 1
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_notify_route_skips_initial_snapshot_when_count_zero():
    broker = NotificationBroker()

    def fake_snapshot(_name: str) -> dict | None:
        return {"content": "INBOX: 0 pending", "meta": {"count": 0, "endpoint": "agent-a"}}

    host = HTTPHost(bind_port=0, notify_broker=broker, notify_snapshot=fake_snapshot)
    await host.start()
    try:
        url = f"http://127.0.0.1:{host.port}/notify/agent-a"
        async with httpx.AsyncClient(timeout=2.0) as client:
            async with client.stream("GET", url) as resp:
                # No initial snapshot. Drive a real publish to confirm the
                # stream is live but the snapshot was suppressed.
                async def push_after_delay():
                    await asyncio.sleep(0.2)
                    await broker.publish("agent-a", {"meta": {"count": 1}})

                pump = asyncio.create_task(push_after_delay())
                events = []
                try:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[len("data: ") :]))
                            break
                finally:
                    pump.cancel()
        # The first event we received was the post-connect publish, not the
        # zero-count snapshot.
        assert events == [{"meta": {"count": 1}}]
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
        await asyncio.sleep(0.2)
        # The agent-a subscriber set should be cleaned up.
        assert "agent-a" not in broker._subs
    finally:
        await host.stop()
