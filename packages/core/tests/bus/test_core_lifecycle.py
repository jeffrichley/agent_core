"""Tests for Bus registration, start/stop, and endpoint discovery."""

from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope


class _LifecycleSpy:
    def __init__(self, name: str):
        self.name = name
        self.started = False
        self.stopped = False
        self.delivered: list[Envelope] = []
        self._handle = None

    async def start(self, bus) -> None:
        self.started = True
        self._handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        self.delivered.append(envelope)

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
async def bus(tmp_path: Path) -> Bus:
    cfg = BusConfig(storage_path=tmp_path / "bus.sqlite")
    b = Bus(cfg)
    yield b
    await b.stop()


class TestRegistration:
    async def test_register_and_start(self, bus: Bus):
        ep = _LifecycleSpy("agent-pepper")
        bus.register(EndpointSpec(endpoint=ep, description="A test agent."))
        await bus.start()
        assert ep.started is True
        assert ep._handle is not None  # received its BusHandle

    async def test_endpoints_directory(self, bus: Bus):
        a = _LifecycleSpy("a")
        b_ep = _LifecycleSpy("b")
        bus.register(EndpointSpec(endpoint=a, description="A!"))
        bus.register(EndpointSpec(endpoint=b_ep, description="B!"))
        await bus.start()
        infos = bus._endpoints()
        assert {(i.name, i.description) for i in infos} == {("a", "A!"), ("b", "B!")}

    async def test_stop_calls_endpoints(self, bus: Bus):
        ep = _LifecycleSpy("x")
        bus.register(EndpointSpec(endpoint=ep))
        await bus.start()
        await bus.stop()
        assert ep.stopped is True

    async def test_duplicate_registration_rejected(self, bus: Bus):
        bus.register(EndpointSpec(endpoint=_LifecycleSpy("x")))
        with pytest.raises(ValueError, match="already registered"):
            bus.register(EndpointSpec(endpoint=_LifecycleSpy("x")))

    async def test_start_partial_failure_quarantines_and_continues(self, bus: Bus):
        class _ExplodingStart:
            name = "boom"

            async def start(self, bus) -> None:
                raise RuntimeError("kaboom")

            async def deliver(self, envelope: Envelope) -> None:
                pass

            async def stop(self) -> None:
                pass

        good = _LifecycleSpy("good")
        bad = _ExplodingStart()
        bus.register(EndpointSpec(endpoint=good))
        bus.register(EndpointSpec(endpoint=bad))

        # Degraded boot: no exception raised; bus starts with what it can
        await bus.start()

        assert bus._started is True
        assert good.stopped is False  # good endpoint was NOT rolled back
        assert bus._supervisor is not None
        assert bus._supervisor.state("boom") is not None
        assert bus._supervisor.state("boom").status == "quarantined"  # type: ignore[union-attr]
        assert bus._supervisor.state("good") is not None
        assert bus._supervisor.state("good").status == "active"  # type: ignore[union-attr]
