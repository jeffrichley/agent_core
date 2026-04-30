"""Tests for the pre_publish and pre_deliver hook pipeline."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, BusHookSpec, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload


class _Echo:
    def __init__(self, name="x"):
        self.name = name
        self.delivered: list[Envelope] = []
        self._handle = None

    async def start(self, bus) -> None:
        self._handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        self.delivered.append(envelope)
        await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        pass


class _RecordingHook:
    def __init__(self, name: str):
        self.name = name
        self.calls: list[tuple[str, str]] = []  # (stage, envelope.id)

    async def execute(self, stage, envelope, params):
        self.calls.append((stage, envelope.id))
        return envelope


class _DropHook:
    async def execute(self, stage, envelope, params):
        return None  # drop


class _MutatingHook:
    async def execute(self, stage, envelope, params):
        return envelope.model_copy(update={"metadata": {**envelope.metadata, "tagged": True}})


def _envelope(id_="e1", to="x") -> Envelope:
    return Envelope(
        id=id_,
        correlation_id="c1",
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
async def make_bus(tmp_path: Path):
    async def _make(*, hooks_pre_publish=(), hooks_pre_deliver=()):
        b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
        b.register(EndpointSpec(endpoint=_Echo("x")))
        for h in hooks_pre_publish:
            b.register_hook("pre_publish", BusHookSpec(hook=h, params={}))
        for h in hooks_pre_deliver:
            b.register_hook("pre_deliver", BusHookSpec(hook=h, params={}))
        await b.start()
        return b

    yield _make


class TestHookPipeline:
    async def test_pre_publish_hook_fires(self, make_bus):
        rec = _RecordingHook("rec")
        bus = await make_bus(hooks_pre_publish=[rec])
        await bus._enqueue(_envelope())
        assert ("pre_publish", "e1") in rec.calls
        await bus.stop()

    async def test_pre_deliver_hook_fires(self, make_bus):
        rec = _RecordingHook("rec")
        bus = await make_bus(hooks_pre_deliver=[rec])
        await bus._enqueue(_envelope())
        assert ("pre_deliver", "e1") in rec.calls
        await bus.stop()

    async def test_drop_hook_skips_persist(self, make_bus):
        bus = await make_bus(hooks_pre_publish=[_DropHook()])
        await bus._enqueue(_envelope())
        # Dropped before persist — no row.
        assert await bus._store.row("e1") is None
        await bus.stop()

    async def test_mutating_hook_changes_envelope(self, make_bus):
        bus = await make_bus(hooks_pre_publish=[_MutatingHook()])
        await bus._enqueue(_envelope())
        env = await bus._store.get("e1")
        assert env.metadata == {"tagged": True}
        await bus.stop()

    async def test_from_stamping_runs_before_pre_publish(self, make_bus):
        # Verify that hooks see authenticated `from_`. We use a hook that
        # records the `from_` it sees.
        seen: list[str] = []

        class _SeeFrom:
            async def execute(self, stage, envelope, params):
                if stage == "pre_publish":
                    seen.append(envelope.from_)
                return envelope

        bus = await make_bus(hooks_pre_publish=[_SeeFrom()])
        # Publish via a BusHandle (the only legitimate path) using the registered name.
        # The endpoint's `start()` got a handle — reuse it.
        endpoint = bus._endpoints_by_name["x"].endpoint
        env = _envelope()
        env.from_ = "spoofed"  # try to spoof
        await endpoint._handle.publish(env)
        assert seen == ["x"]  # bus stamped "x" before hooks saw it
        await bus.stop()
