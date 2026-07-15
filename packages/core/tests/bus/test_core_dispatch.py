"""Tests for Bus.publish, dispatch flow, and at-least-once delivery."""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.protocol import EndpointUnavailable


class _Echo:
    """Endpoint that records deliveries and (optionally) auto-acks them."""

    def __init__(
        self,
        name: str,
        *,
        raise_unavailable: bool = False,
        raise_terminal: bool = False,
        auto_ack: bool = True,
    ):
        self.name = name
        self.delivered: list[Envelope] = []
        self.handle = None
        self.raise_unavailable = raise_unavailable
        self.raise_terminal = raise_terminal
        self.auto_ack = auto_ack

    async def start(self, bus) -> None:
        self.handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        if self.raise_unavailable:
            raise EndpointUnavailable("offline")
        if self.raise_terminal:
            raise RuntimeError("boom")
        self.delivered.append(envelope)
        if self.auto_ack:
            await self.handle.ack(envelope.id)

    async def stop(self) -> None:
        pass


def _envelope(id_="e1", to="x", **overrides) -> Envelope:
    fields = dict(
        id=id_,
        correlation_id="c1",
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC),
    )
    fields.update(overrides)
    return Envelope(**fields)


@pytest.fixture
async def bus(tmp_path: Path) -> Bus:
    b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    yield b
    await b.stop()


class TestPublish:
    async def test_round_trip_via_stub_endpoint(self, bus: Bus):
        echo = _Echo("agent")
        bus.register(EndpointSpec(endpoint=echo))
        await bus.start()
        env = _envelope(to="agent")
        await bus._enqueue(env)
        # Allow the dispatch task to run.
        await asyncio.sleep(0)
        assert len(echo.delivered) == 1
        assert echo.delivered[0].id == "e1"

    async def test_publish_to_unregistered_recipient_rejected(self, bus: Bus):
        await bus.start()
        with pytest.raises(ValueError, match="unregistered endpoint"):
            await bus._enqueue(_envelope(to="nobody"))

    async def test_publish_when_endpoint_offline_queues(self, bus: Bus):
        offline = _Echo("offline", raise_unavailable=True)
        bus.register(EndpointSpec(endpoint=offline))
        await bus.start()
        await bus._enqueue(_envelope(to="offline"))
        await asyncio.sleep(0)
        # Endpoint refused; envelope must be back in pending state.
        row = await bus._store.row("e1")
        assert row["state"] == "pending"
        assert offline.delivered == []

    async def test_terminal_exception_dead_letters(self, bus: Bus):
        bad = _Echo("bad", raise_terminal=True)
        bus.register(EndpointSpec(endpoint=bad))
        await bus.start()
        await bus._enqueue(_envelope(to="bad"))
        await asyncio.sleep(0)
        row = await bus._store.row("e1")
        assert row["state"] == "dead_letter"
        assert "boom" in (row["nack_reason"] or "")

    async def test_mailbox_cap_blocks_overflow(self, tmp_path: Path):
        cfg = BusConfig(storage_path=tmp_path / "bus.sqlite", max_pending_per_endpoint=2)
        b = Bus(cfg)
        # Register a non-running endpoint via spec (we never start the bus,
        # so deliveries pile up as 'pending').
        b.register(EndpointSpec(endpoint=_Echo("offline", raise_unavailable=True)))
        await b.start()
        await b._enqueue(_envelope("e1", to="offline"))
        await asyncio.sleep(0)
        await b._enqueue(_envelope("e2", to="offline"))
        await asyncio.sleep(0)
        from agent_core.bus.core import MailboxFull

        with pytest.raises(MailboxFull):
            await b._enqueue(_envelope("e3", to="offline"))
        await b.stop()

    async def test_drain_for_redelivers_pending(self, bus: Bus):
        # Endpoint refuses delivery initially; envelope queues as pending.
        # Then we flip it to accept and call drain_for() — must re-dispatch.
        flaky = _Echo("flaky", raise_unavailable=True)
        bus.register(EndpointSpec(endpoint=flaky))
        await bus.start()
        await bus._enqueue(_envelope("e1", to="flaky"))
        await asyncio.sleep(0)
        assert (await bus._store.row("e1"))["state"] == "pending"
        assert flaky.delivered == []

        # Endpoint comes online; drain pending mail.
        flaky.raise_unavailable = False
        await bus.drain_for("flaky")
        await asyncio.sleep(0)
        assert len(flaky.delivered) == 1
        assert flaky.delivered[0].id == "e1"


class TestPublishWithListTo:
    async def test_list_recipients_fans_out(self, bus: Bus):
        a = _Echo("a")
        b_ep = _Echo("b")
        bus.register(EndpointSpec(endpoint=a))
        bus.register(EndpointSpec(endpoint=b_ep))
        await bus.start()
        await bus._enqueue(_envelope("e1", to="a"), to=["a", "b"])
        await asyncio.sleep(0)
        assert len(a.delivered) == 1
        assert len(b_ep.delivered) == 1
        # Each got a distinct envelope id (bus minted N-1 fresh ids).
        assert a.delivered[0].id != b_ep.delivered[0].id


class TestSlowDeliverWatchdog:
    """Bus._dispatch emits SlowDeliverWarning when deliver() exceeds threshold."""

    @pytest.mark.asyncio
    async def test_slow_deliver_emits_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A deliver() that sleeps 100 ms with a 1 ms threshold emits SlowDeliverWarning."""

        class _SlowEndpoint:
            name = "slow"

            async def start(self, bus) -> None:
                self._handle = bus

            async def deliver(self, envelope: Envelope) -> None:
                await asyncio.sleep(0.1)
                await self._handle.ack(envelope.id)

            async def stop(self) -> None:
                pass

        cfg = BusConfig(storage_path=tmp_path / "bus.sqlite", slow_deliver_warn_seconds=0.001)
        b = Bus(cfg)
        b.register(EndpointSpec(endpoint=_SlowEndpoint()))
        await b.start()
        with caplog.at_level(logging.WARNING, logger="agent_core.bus.core"):
            await b._enqueue(_envelope(to="slow"))
        await b.stop()

        warning_records = [r for r in caplog.records if "SlowDeliverWarning" in r.message]
        assert warning_records, "Expected a SlowDeliverWarning log record, got none"

    @pytest.mark.asyncio
    async def test_fast_deliver_no_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A deliver() that completes immediately does not emit SlowDeliverWarning."""

        class _FastEndpoint:
            name = "fast"

            async def start(self, bus) -> None:
                self._handle = bus

            async def deliver(self, envelope: Envelope) -> None:
                await self._handle.ack(envelope.id)

            async def stop(self) -> None:
                pass

        cfg = BusConfig(storage_path=tmp_path / "bus.sqlite", slow_deliver_warn_seconds=10.0)
        b = Bus(cfg)
        b.register(EndpointSpec(endpoint=_FastEndpoint()))
        await b.start()
        with caplog.at_level(logging.WARNING, logger="agent_core.bus.core"):
            await b._enqueue(_envelope(to="fast"))
        await b.stop()

        warning_records = [r for r in caplog.records if "SlowDeliverWarning" in r.message]
        assert not warning_records, f"Unexpected SlowDeliverWarning: {warning_records}"

    @pytest.mark.asyncio
    async def test_disabled_watchdog_no_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """slow_deliver_warn_seconds <= 0 disables the watchdog entirely."""

        class _SlowEndpoint2:
            name = "slow2"

            async def start(self, bus) -> None:
                self._handle = bus

            async def deliver(self, envelope: Envelope) -> None:
                await asyncio.sleep(0.1)
                await self._handle.ack(envelope.id)

            async def stop(self) -> None:
                pass

        cfg = BusConfig(storage_path=tmp_path / "bus.sqlite", slow_deliver_warn_seconds=0.0)
        b = Bus(cfg)
        b.register(EndpointSpec(endpoint=_SlowEndpoint2()))
        await b.start()
        with caplog.at_level(logging.WARNING, logger="agent_core.bus.core"):
            await b._enqueue(_envelope(to="slow2"))
        await b.stop()

        warning_records = [r for r in caplog.records if "SlowDeliverWarning" in r.message]
        assert not warning_records, f"Watchdog should be disabled: {warning_records}"
