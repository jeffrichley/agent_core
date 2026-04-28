"""Tests for the StubEndpoint — a simple echo/inbox adapter for testing."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.stub import StubEndpoint


def _envelope(id_="e1", to="stub") -> Envelope:
    return Envelope(
        id=id_,
        correlation_id="c1",
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text="hello"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
async def bus(tmp_path: Path) -> Bus:
    b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    yield b
    await b.stop()


class TestStubEndpoint:
    async def test_inbox_collects_delivered_envelopes(self, bus: Bus):
        stub = StubEndpoint(name="stub")
        bus.register(EndpointSpec(endpoint=stub))
        await bus.start()
        await bus._enqueue(_envelope())
        assert len(stub.inbox) == 1
        assert stub.inbox[0].id == "e1"

    async def test_auto_acks_by_default(self, bus: Bus):
        stub = StubEndpoint(name="stub")
        bus.register(EndpointSpec(endpoint=stub))
        await bus.start()
        await bus._enqueue(_envelope())
        assert (await bus._store.row("e1"))["state"] == "acked"

    async def test_auto_ack_disabled(self, bus: Bus):
        stub = StubEndpoint(name="stub", auto_ack=False)
        bus.register(EndpointSpec(endpoint=stub))
        await bus.start()
        await bus._enqueue(_envelope())
        assert (await bus._store.row("e1"))["state"] == "in_flight"

    async def test_publish_helper_fans_out(self, bus: Bus):
        sender = StubEndpoint(name="sender")
        receiver = StubEndpoint(name="receiver")
        bus.register(EndpointSpec(endpoint=sender))
        bus.register(EndpointSpec(endpoint=receiver))
        await bus.start()
        await sender.send(
            to="receiver",
            kind="TextMessage",
            payload=TextMessagePayload(text="from sender"),
        )
        assert len(receiver.inbox) == 1
        assert receiver.inbox[0].from_ == "sender"  # bus-stamped
        assert receiver.inbox[0].payload.text == "from sender"
