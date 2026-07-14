"""Tests for InboundEndpoint.deliver() ack-vs-nack behaviour.

Two paths:
- _handle is None  → EndpointUnavailable (already correct, test documents it)
- _handle is not None → RuntimeError so the bus dead-letters the envelope
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import Envelope, EventPayload
from agent_core.bus.protocol import EndpointUnavailable
from agent_core_inbound.endpoint import InboundEndpoint


class _FakeHandle:
    """Strict recording fake for BusHandle — tracks ack calls."""

    def __init__(self) -> None:
        self.acked: list[str] = []

    async def ack(self, envelope_id: str) -> None:
        self.acked.append(envelope_id)

    async def nack(self, envelope_id: str, requeue: bool = True) -> None:
        pass

    async def publish(self, envelope: Envelope, to=None) -> None:
        pass


def _make_envelope() -> Envelope:
    return Envelope(
        id=uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        to="inbound",
        kind="Event",
        payload=EventPayload(kind="Event", type="test.event", data={}),
        created_at=datetime.now(UTC),
    )


def _make_endpoint(monkeypatch, tmp_path) -> InboundEndpoint:
    monkeypatch.setenv("TEST_DELIVER_SECRET", "test-secret")
    return InboundEndpoint(
        name="inbound",
        target_being="wren",
        listen_host="127.0.0.1",
        listen_port=18765,
        webhook_secret_env="TEST_DELIVER_SECRET",
        github_allowance_path=str(tmp_path / "g.toml"),
        audit_log_path=str(tmp_path / "audit.jsonl"),
        rate_limit_per_minute=30,
    )


@pytest.mark.asyncio
async def test_deliver_before_start_raises_endpoint_unavailable(monkeypatch, tmp_path):
    """When _handle is None (endpoint not started), deliver() raises EndpointUnavailable.

    EndpointUnavailable tells the bus to requeue (not dead-letter).
    """
    ep = _make_endpoint(monkeypatch, tmp_path)
    assert ep._handle is None

    with pytest.raises(EndpointUnavailable):
        await ep.deliver(_make_envelope())


@pytest.mark.asyncio
async def test_deliver_after_start_unexpected_envelope_dead_letters(monkeypatch, tmp_path):
    """When _handle is set, deliver() raises RuntimeError (not EndpointUnavailable).

    RuntimeError causes the bus to dead-letter the envelope — correct behaviour
    for a permanently misrouted envelope that no amount of retry will fix.
    The endpoint must NOT call ack() before raising.
    """
    ep = _make_endpoint(monkeypatch, tmp_path)
    fake_handle = _FakeHandle()
    # Inject handle directly to avoid spinning up uvicorn in a unit test.
    ep._handle = fake_handle

    with pytest.raises(RuntimeError):
        await ep.deliver(_make_envelope())

    # No ack must have been issued before the raise.
    assert fake_handle.acked == [], "deliver() must not ack before raising RuntimeError"
