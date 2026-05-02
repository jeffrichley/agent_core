"""Issue #12: auto-handle routine green Acknowledgments without channel wake.

Time-dependent waits use ``@pytest.mark.looptime`` (``looptime`` PyPI package) so
``asyncio.sleep`` advances *loop* time without wall-clock delay. One test uses
the real in-process FastMCP ``Client`` and is left **un**-marked so its I/O is
not subject to virtual loop compaction.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from fastmcp import Client
from mcp.shared.session import SessionMessage

from agent_core.bus.envelope import AcknowledgmentPayload, Envelope
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _RecordingSession:
    def __init__(self, fail_with: Exception | None = None) -> None:
        self.sent: list[Any] = []
        self._fail_with = fail_with

    async def send_message(self, message) -> None:
        assert isinstance(message, SessionMessage)
        if self._fail_with is not None:
            raise self._fail_with
        self.sent.append(message)


class _StubHandle:
    def __init__(self) -> None:
        self.acked: list[str] = []

    async def ack(self, envelope_id: str) -> None:
        self.acked.append(envelope_id)

    async def publish(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        raise RuntimeError("not used")

    def endpoints(self) -> list:
        return []


class _FailingAckHandle(_StubHandle):
    async def ack(self, envelope_id: str) -> None:
        raise RuntimeError("persist failed")


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


def _speed_up_debounce(ep: ClaudeCodeMCPEndpoint) -> None:
    ep._notify_debounce_seconds_by_urgency = {"red": 0.01, "yellow": 0.03, "green": 0.05}


async def _flush_debounce() -> None:
    """Advance past the slowest debounce tier used via _speed_up_debounce (green=0.05s)."""
    await asyncio.sleep(0.2)


def _green_ack(aid: str, in_reply_to: str, *, note: str | None = None, of: str | None = None) -> Envelope:
    of = of if of is not None else in_reply_to
    return Envelope(
        id=aid,
        correlation_id=f"c-{aid}",
        in_reply_to=in_reply_to,
        from_="discord",
        to="agent",
        kind="Acknowledgment",
        payload=AcknowledgmentPayload(of=of, note=note),
        urgency="green",
        created_at=datetime.now(UTC),
    )


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_green_ack_matching_recent_outbound_skips_channel_push() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    _speed_up_debounce(ep)
    stub = _StubHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    outbound_id = "outbound-1"
    ep._recent_outbound_ids[outbound_id] = asyncio.get_running_loop().time()

    session = _RecordingSession()
    ep._register_session(session)

    await ep.deliver(_green_ack("ack-1", outbound_id))
    await _flush_debounce()

    assert session.sent == []
    assert "ack-1" in stub.acked
    assert all(e.id != "ack-1" for e in ep._pending)


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_auto_ack_without_session_does_not_raise_endpoint_unavailable() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    stub = _StubHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    outbound_id = "outbound-x"
    ep._recent_outbound_ids[outbound_id] = asyncio.get_running_loop().time()
    assert not ep._sessions

    await ep.deliver(_green_ack("ack-x", outbound_id))
    assert "ack-x" in stub.acked


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_green_ack_without_registered_outbound_still_pushes() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    _speed_up_debounce(ep)
    stub = _StubHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    session = _RecordingSession()
    ep._register_session(session)

    await ep.deliver(_green_ack("ack-2", "unknown-outbound"))
    await _flush_debounce()
    assert len(session.sent) == 1


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_error_note_ack_always_wakes() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    _speed_up_debounce(ep)
    stub = _StubHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    session = _RecordingSession()
    ep._register_session(session)
    oid = "out-err"
    ep._recent_outbound_ids[oid] = asyncio.get_running_loop().time()

    await ep.deliver(_green_ack("ack-e", oid, note="error: boom"))
    await _flush_debounce()
    assert len(session.sent) == 1


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_yellow_ack_wakes() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    _speed_up_debounce(ep)
    stub = _StubHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    session = _RecordingSession()
    ep._register_session(session)
    oid = "out-y"
    ep._recent_outbound_ids[oid] = asyncio.get_running_loop().time()

    env = _green_ack("ack-y", oid).model_copy(update={"urgency": "yellow"})
    await ep.deliver(env)
    await _flush_debounce()
    assert len(session.sent) == 1


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_malformed_of_mismatch_wakes() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    _speed_up_debounce(ep)
    stub = _StubHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    session = _RecordingSession()
    ep._register_session(session)
    oid = "out-m"
    ep._recent_outbound_ids[oid] = asyncio.get_running_loop().time()

    await ep.deliver(_green_ack("ack-m", oid, of="other-id"))
    await _flush_debounce()
    assert len(session.sent) == 1


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_missing_ack_fires_wake() -> None:
    ep = ClaudeCodeMCPEndpoint(
        name="agent",
        mount="/mcp/agent",
        missing_ack_default_seconds=0.08,
    )
    _speed_up_debounce(ep)
    stub = _StubHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    session = _RecordingSession()
    ep._register_session(session)

    ep._register_outbound_sent("pend-1", {})
    await asyncio.sleep(0.25)
    assert len(session.sent) >= 1
    assert "pend-1" not in ep._recent_outbound_ids


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_missing_ack_metadata_override_seconds() -> None:
    ep = ClaudeCodeMCPEndpoint(
        name="agent",
        mount="/mcp/agent",
        missing_ack_default_seconds=30.0,
    )
    _speed_up_debounce(ep)
    stub = _StubHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    session = _RecordingSession()
    ep._register_session(session)

    ep._register_outbound_sent("pend-meta", {"agent_core": {"ack_timeout_seconds": 0.1}})
    await asyncio.sleep(0.35)
    assert len(session.sent) >= 1


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_missing_ack_metadata_timeout_clamped_to_cap() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent", missing_ack_default_seconds=30.0)
    _speed_up_debounce(ep)
    stub = _StubHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    session = _RecordingSession()
    ep._register_session(session)

    ep._register_outbound_sent(
        "pend-cap",
        {"agent_core": {"ack_timeout_seconds": 9e12}},
    )
    await asyncio.sleep(86400.5)
    assert len(session.sent) >= 1


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_wake_on_all_acknowledgments_forces_wake() -> None:
    ep = ClaudeCodeMCPEndpoint(
        name="agent",
        mount="/mcp/agent",
        wake_on_all_acknowledgments=True,
    )
    _speed_up_debounce(ep)
    stub = _StubHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    session = _RecordingSession()
    ep._register_session(session)
    oid = "out-wakeall"
    ep._recent_outbound_ids[oid] = asyncio.get_running_loop().time()

    await ep.deliver(_green_ack("ack-w", oid))
    await _flush_debounce()
    assert len(session.sent) == 1


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_auto_ack_green_routine_does_not_publish_to_broker() -> None:
    broker = NotificationBroker()
    q = await broker.subscribe("agent-b")
    ep = ClaudeCodeMCPEndpoint(name="agent-b", mount="/mcp/b", notify_broker=broker)
    _speed_up_debounce(ep)
    stub = _StubHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    ep._register_session(_RecordingSession())
    oid = "out-br"
    ep._recent_outbound_ids[oid] = asyncio.get_running_loop().time()

    await ep.deliver(_green_ack("ack-br", oid))
    await _flush_debounce()
    with pytest.raises(asyncio.QueueEmpty):
        q.get_nowait()


@pytest.mark.asyncio
async def test_send_registers_outbound_for_auto_ack() -> None:
    """Uses FastMCP ``Client`` in-process; not run under ``looptime`` (see module docstring)."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        async with Client(ep._mcp) as client:
            await client.call_tool(
                "send",
                {
                    "to": "stub",
                    "kind": "TextMessage",
                    "payload": {"kind": "TextMessage", "text": "hi"},
                },
            )
        oid = handle.published[0].id
        assert oid in ep._recent_outbound_ids

        _speed_up_debounce(ep)
        session = _RecordingSession()
        ep._register_session(session)
        await ep.deliver(_green_ack("ack-send", oid))
        await asyncio.sleep(0.2)
        assert session.sent == []
    finally:
        await ep.stop()


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_stop_cancels_missing_ack_tasks() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent", missing_ack_default_seconds=60.0)
    stub = _StubHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    ep._register_outbound_sent("long-wait", {})
    assert ep._missing_ack_tasks
    await ep.stop()
    assert not ep._missing_ack_tasks
    assert not ep._recent_outbound_ids


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_constructor_clamps_missing_ack_default() -> None:
    ep = ClaudeCodeMCPEndpoint(
        name="agent",
        mount="/mcp/agent",
        missing_ack_default_seconds=9e12,
    )
    assert ep._missing_ack_default_seconds == 86400.0


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_constructor_clamps_outbound_registry_ttl_min() -> None:
    ep = ClaudeCodeMCPEndpoint(
        name="agent",
        mount="/mcp/agent",
        outbound_registry_ttl_seconds=0.01,
    )
    assert ep._outbound_registry_ttl_seconds == 1.0


@pytest.mark.looptime
@pytest.mark.asyncio
async def test_auto_ack_bus_ack_failure_leaves_outbound_registered() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    stub = _FailingAckHandle()
    await ep.start(stub)  # type: ignore[arg-type]
    oid = "out-fail"
    ep._recent_outbound_ids[oid] = asyncio.get_running_loop().time()

    with pytest.raises(RuntimeError, match="persist failed"):
        await ep.deliver(_green_ack("ack-fail", oid))

    assert oid in ep._recent_outbound_ids
    assert stub.acked == []
