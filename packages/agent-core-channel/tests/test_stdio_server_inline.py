"""Issue #70: integration tests for the relay's wire-up.

The relay receives a wake on its SSE stream, fetches the queue from the
bus via BusClient, applies rendering, emits the rich notification."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core_channel.bus_client import BusClient
from agent_core_channel.stdio_server import process_wake_event
from agent_core_channel.wake_audit import WakeAuditWriter


def _inbound(env_id: str, *, text: str = "hi") -> Envelope:
    return Envelope(
        id=env_id,
        correlation_id=f"corr-{env_id}",
        in_reply_to=None,
        from_="discord",
        to="pepper",
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        urgency="green",
        created_at=datetime.now(UTC),
    )


class _StubHandle:
    async def ack(self, envelope_id: str) -> None: ...
    async def publish(self, envelope, to=None) -> None: ...
    async def nack(self, envelope_id, requeue=True) -> None: ...
    def endpoints(self) -> list:
        return []


class _CapturingWriteStream:
    """Captures everything sent to it for assertion."""
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, msg) -> None:
        self.sent.append(msg)


@pytest.mark.asyncio
async def test_process_wake_event_inlines_single_envelope(tmp_path: Path) -> None:
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound("e-1", text="hello world"))

        wake_summary = {
            "content": "INBOX: pending (pepper)",
            "meta": {"endpoint": "pepper", "fired_at": "2026-05-09T03:29:22Z"},
        }
        write = _CapturingWriteStream()
        from agent_core_channel.config import RelayConfig
        config = RelayConfig()  # full mode, defaults

        async with BusClient.from_in_process(ep._mcp) as bus:
            audit = WakeAuditWriter(tmp_path / "pepper.jsonl")
            from agent_core_channel.rendering import RedeliveryTracker
            tracker = RedeliveryTracker()
            await process_wake_event(
                wake_summary=wake_summary, write_stream=write, bus=bus,
                audit=audit, agent="pepper", config=config, redelivery=tracker,
            )

        assert len(write.sent) == 1
        msg = write.sent[0]
        # The notification's content carries the rendered <inbox> block.
        params = msg.message.root.params
        assert "<inbox" in params["content"]
        assert "envelope_id='e-1'" in params["content"]
        assert "hello world" in params["content"]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_process_wake_event_phantom_wake_suppresses_emission(tmp_path: Path) -> None:
    """Empty queue → no notification emitted; audit row written."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        wake_summary = {
            "content": "INBOX: pending (pepper)",
            "meta": {"endpoint": "pepper", "fired_at": "2026-05-09T03:29:22Z"},
        }
        write = _CapturingWriteStream()
        from agent_core_channel.config import RelayConfig
        config = RelayConfig()
        from agent_core_channel.rendering import RedeliveryTracker
        tracker = RedeliveryTracker()

        audit_path = tmp_path / "pepper.jsonl"
        async with BusClient.from_in_process(ep._mcp) as bus:
            audit = WakeAuditWriter(audit_path)
            await process_wake_event(
                wake_summary=wake_summary, write_stream=write, bus=bus,
                audit=audit, agent="pepper", config=config, redelivery=tracker,
            )

        # No notification emitted (phantom wake).
        assert write.sent == []
        # But the audit log records it as fallback="empty_queue".
        import json
        line = json.loads(audit_path.read_text().strip())
        assert line["fallback"] == "empty_queue"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_process_wake_event_circuit_breaker_emits_bare_wake(tmp_path: Path) -> None:
    """6 envelopes (over the default cap of 5) → bare wake fallback."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        for i in range(6):
            ep.queue_for_pickup(_inbound(f"e-{i}", text=f"msg {i}"))

        wake_summary = {
            "content": "INBOX: pending (pepper)",
            "meta": {"endpoint": "pepper", "fired_at": "2026-05-09T03:29:22Z"},
        }
        write = _CapturingWriteStream()
        from agent_core_channel.config import RelayConfig
        config = RelayConfig()
        from agent_core_channel.rendering import RedeliveryTracker
        tracker = RedeliveryTracker()

        async with BusClient.from_in_process(ep._mcp) as bus:
            audit = WakeAuditWriter(tmp_path / "pepper.jsonl")
            await process_wake_event(
                wake_summary=wake_summary, write_stream=write, bus=bus,
                audit=audit, agent="pepper", config=config, redelivery=tracker,
            )

        # Bare wake emitted (circuit breaker tripped).
        assert len(write.sent) == 1
        params = write.sent[0].message.root.params
        assert params["content"] == "INBOX: pending (pepper)"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_process_wake_event_disabled_mode_passes_through(tmp_path: Path) -> None:
    """inline_mode=disabled → identity passthrough of the bare wake."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound("e-d"))

        wake_summary = {
            "content": "INBOX: pending (pepper)",
            "meta": {"endpoint": "pepper", "fired_at": "2026-05-09T03:29:22Z"},
        }
        write = _CapturingWriteStream()
        from agent_core_channel.config import RelayConfig
        config = RelayConfig(inline_mode="disabled")
        from agent_core_channel.rendering import RedeliveryTracker
        tracker = RedeliveryTracker()

        async with BusClient.from_in_process(ep._mcp) as bus:
            audit = WakeAuditWriter(tmp_path / "pepper.jsonl")
            await process_wake_event(
                wake_summary=wake_summary, write_stream=write, bus=bus,
                audit=audit, agent="pepper", config=config, redelivery=tracker,
            )

        assert len(write.sent) == 1
        params = write.sent[0].message.root.params
        assert params["content"] == "INBOX: pending (pepper)"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_process_wake_event_render_error_falls_back_to_bare(tmp_path: Path) -> None:
    """Bus client raises → fall back to bare wake; audit logs the error."""
    class _BrokenBus:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def consume_no_ack(self, *, batch_window_seconds: int = 30) -> dict:
            raise RuntimeError("simulated bus failure")

    wake_summary = {
        "content": "INBOX: pending (pepper)",
        "meta": {"endpoint": "pepper", "fired_at": "2026-05-09T03:29:22Z"},
    }
    write = _CapturingWriteStream()
    from agent_core_channel.config import RelayConfig
    config = RelayConfig()
    from agent_core_channel.rendering import RedeliveryTracker
    tracker = RedeliveryTracker()
    audit_path = tmp_path / "pepper.jsonl"
    audit = WakeAuditWriter(audit_path)

    async with _BrokenBus() as bus:
        await process_wake_event(
            wake_summary=wake_summary, write_stream=write, bus=bus,
            audit=audit, agent="pepper", config=config, redelivery=tracker,
        )

    # Bare wake emitted (render error fallback).
    assert len(write.sent) == 1
    params = write.sent[0].message.root.params
    assert params["content"] == "INBOX: pending (pepper)"
    # Audit logs the error.
    import json
    line = json.loads(audit_path.read_text().strip())
    assert line["fallback"] == "render_error"
    assert "simulated bus failure" in line["error"]
