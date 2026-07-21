"""End-to-end: real bus + HTTPHost + relay coroutine driving fake stdio.

Validates the complete chain:
    bus.publish -> ClaudeCodeMCPEndpoint.deliver -> broker.publish ->
    /notify/<agent> SSE -> relay sse_pump -> emit_channel_notification ->
    MCP stdio write stream.

The relay's two halves (`iter_notify_events` from sse_client and
`emit_channel_notification` from stdio_server) run in-process. Instead of
real stdin/stdout, an `anyio.create_memory_object_stream` pair stands in
for the MCP write stream. No HTTP MCP session is attached to the agent
endpoint — exactly the production scenario the relay was built for: the
agent connects only via the stdio relay, and `deliver()` must still fan
out to the broker so the relay's SSE subscription wakes the agent.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest
from mcp.shared.session import SessionMessage

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.http_host import HTTPHost
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core.endpoints.stub import StubEndpoint
from agent_core.testing import wait_until


@pytest.mark.asyncio
async def test_bus_arrival_reaches_relay_stdio_stream(tmp_path: Path) -> None:
    """Publish via the stub -> relay emits notifications/claude/channel."""
    pytest.importorskip("uvicorn")

    # 1. Bus + endpoints + broker.
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    broker = NotificationBroker()
    agent_ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent", notify_broker=broker)
    stub = StubEndpoint(name="stub")
    bus.register(EndpointSpec(endpoint=agent_ep, description="agent under test"))
    bus.register(EndpointSpec(endpoint=stub, description="probe"))

    # 2. HTTPHost with /notify/<agent> route.
    host = HTTPHost(
        bind_port=0,
        notify_broker=broker,
        notify_snapshot=bus.snapshot_for_agent,
    )
    host.mount(agent_ep)
    await host.start()
    daemon_url = f"http://127.0.0.1:{host.port}"

    try:
        await bus.start()
        try:
            # 3. Stand up the relay's two tasks against a fake stdio pair.
            from agent_core_channel.sse_client import iter_notify_events
            from agent_core_channel.stdio_server import emit_channel_notification

            # Memory stream replaces real stdout. The relay writes
            # SessionMessages onto write_from_relay; the test reads them
            # off read_from_relay.
            write_from_relay, read_from_relay = anyio.create_memory_object_stream(
                max_buffer_size=16
            )

            async def relay_pump() -> None:
                async for summary in iter_notify_events(
                    agent="agent",
                    daemon_url=daemon_url,
                    backoff_initial=0.05,
                    backoff_max=0.05,
                ):
                    await emit_channel_notification(write_from_relay, summary)

            relay_task = asyncio.create_task(relay_pump())

            try:
                # Give the relay a moment to subscribe to /notify/agent
                # before we publish, so the broker fan-out has a target.
                await wait_until(
                    lambda: broker._subs.get("agent"),
                    message="relay subscribed to /notify/agent before publish",
                )

                # 4. Publish via the stub. BusHandle.publish stamps `from_`.
                env = Envelope(
                    id="e1",
                    correlation_id="c1",
                    from_="stub",
                    to="agent",
                    kind="TextMessage",
                    payload=TextMessagePayload(text="hello"),
                    urgency="green",
                    created_at=datetime.now(UTC),
                )
                assert stub._handle is not None
                await stub._handle.publish(env)

                # 5. Assert the relay emits a notifications/claude/channel
                # within 2s. Polls every 50ms; debounce + SSE round-trip is
                # ~50-150ms in practice, so 40 iterations is generous.
                received: SessionMessage | None = None
                for _ in range(40):
                    try:
                        received = read_from_relay.receive_nowait()
                        break
                    except anyio.WouldBlock:
                        await asyncio.sleep(0.05)
                assert received is not None, "relay never emitted a notification"
                root = received.message.root
                assert root.method == "notifications/claude/channel"
                assert root.params is not None
                # Wake is a pure "go look" signal: meta carries only the
                # minimal {endpoint, fired_at}. Agents read authoritative
                # queue-state via list_pending. meta values are stringified
                # per Claude Code's channel spec (Record<string, string>).
                meta = root.params["meta"]
                assert set(meta.keys()) == {"endpoint", "fired_at"}
                assert meta["endpoint"] == "agent"
            finally:
                relay_task.cancel()
                try:
                    await relay_task
                except (asyncio.CancelledError, Exception):
                    pass
                write_from_relay.close()
                read_from_relay.close()
        finally:
            await bus.stop()
    finally:
        await host.stop()
