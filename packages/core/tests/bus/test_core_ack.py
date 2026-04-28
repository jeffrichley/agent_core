"""Tests for Bus.ack and Bus.nack."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload


class _Inert:
    """Endpoint that records but does not auto-ack."""

    def __init__(self, name: str):
        self.name = name
        self.delivered: list[Envelope] = []

    async def start(self, bus) -> None:
        pass

    async def deliver(self, envelope: Envelope) -> None:
        self.delivered.append(envelope)

    async def stop(self) -> None:
        pass


def _envelope(id_="e1", to="x") -> Envelope:
    return Envelope(
        id=id_,
        correlation_id="c1",
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
async def bus(tmp_path: Path) -> Bus:
    b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    yield b
    await b.stop()


class TestAck:
    async def test_ack_marks_acked(self, bus: Bus):
        ep = _Inert("x")
        bus.register(EndpointSpec(endpoint=ep))
        await bus.start()
        await bus._enqueue(_envelope())
        # State should be in_flight after dispatch.
        assert (await bus._store.row("e1"))["state"] == "in_flight"
        await bus._ack("e1")
        assert (await bus._store.row("e1"))["state"] == "acked"

    async def test_ack_idempotent(self, bus: Bus):
        ep = _Inert("x")
        bus.register(EndpointSpec(endpoint=ep))
        await bus.start()
        await bus._enqueue(_envelope())
        await bus._ack("e1")
        # Double ack must not raise.
        await bus._ack("e1")
        assert (await bus._store.row("e1"))["state"] == "acked"

    async def test_ack_unknown_id_silent(self, bus: Bus):
        await bus.start()
        # Acking a non-existent id is a no-op (doesn't raise).
        await bus._ack("never-existed")


class TestNack:
    async def test_nack_with_requeue_returns_to_pending(self, bus: Bus):
        ep = _Inert("x")
        bus.register(EndpointSpec(endpoint=ep))
        await bus.start()
        await bus._enqueue(_envelope())
        await bus._nack("e1", requeue=True)
        assert (await bus._store.row("e1"))["state"] == "pending"

    async def test_nack_no_requeue_dead_letters(self, bus: Bus):
        ep = _Inert("x")
        bus.register(EndpointSpec(endpoint=ep))
        await bus.start()
        await bus._enqueue(_envelope())
        await bus._nack("e1", requeue=False)
        assert (await bus._store.row("e1"))["state"] == "dead_letter"
