"""Tests for BusHandle — the per-endpoint surface to the bus."""

from datetime import datetime, timezone

from agent_core.bus.envelope import EndpointInfo, Envelope, TextMessagePayload
from agent_core.bus.handle import BusHandle


class _RecordingBus:
    def __init__(self):
        self.published: list[tuple[Envelope, str | list[str] | None]] = []
        self.acks: list[str] = []
        self.nacks: list[tuple[str, bool]] = []
        self.directory = [
            EndpointInfo(name="agent-pepper", description="P"),
            EndpointInfo(name="discord", description="D"),
        ]

    async def _enqueue(self, envelope: Envelope, to=None) -> None:
        self.published.append((envelope, to))

    async def _ack(self, envelope_id: str) -> None:
        self.acks.append(envelope_id)

    async def _nack(self, envelope_id: str, requeue: bool) -> None:
        self.nacks.append((envelope_id, requeue))

    def _endpoints(self) -> list[EndpointInfo]:
        return list(self.directory)


def _envelope(**overrides) -> Envelope:
    fields = dict(
        id="e1",
        correlation_id="c1",
        to="agent-pepper",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
    )
    fields.update(overrides)
    return Envelope(**fields)


class TestBusHandlePublish:
    async def test_stamps_from(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "agent-pepper")
        env = _envelope(from_="not-pepper")  # caller tries to spoof
        await handle.publish(env)
        published, _ = bus.published[0]
        assert published.from_ == "agent-pepper"  # bus overwrote

    async def test_stamps_when_unset(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "discord")
        env = _envelope()  # from_ defaults to ""
        await handle.publish(env)
        assert bus.published[0][0].from_ == "discord"

    async def test_passes_to_override(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "discord")
        env = _envelope()
        await handle.publish(env, to=["a", "b"])
        assert bus.published[0][1] == ["a", "b"]


class TestBusHandleAckNack:
    async def test_ack_delegates(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        await handle.ack("e1")
        assert bus.acks == ["e1"]

    async def test_nack_delegates_with_requeue_default(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        await handle.nack("e1")
        assert bus.nacks == [("e1", True)]

    async def test_nack_with_no_requeue(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        await handle.nack("e1", requeue=False)
        assert bus.nacks == [("e1", False)]


class TestBusHandleEndpoints:
    def test_endpoints_returns_directory_snapshot(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        infos = handle.endpoints()
        assert {i.name for i in infos} == {"agent-pepper", "discord"}

    def test_endpoints_is_independent_copy(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        infos = handle.endpoints()
        infos.clear()
        # Mutating the returned list must not affect later calls.
        assert len(handle.endpoints()) == 2
