"""Unit tests for delivery retry backoff (issue #274).

Covers:
- _compute_delivery_backoff pure function
- Bus._dispatch sets next_attempt_at on EndpointUnavailable
- Bus._dispatch dead-letters at max_delivery_attempts (transient)
- Bus.run_redelivery_sweep_once Part 1 uses requeue_with_backoff (not immediate re-dispatch)
- Bus.run_redelivery_sweep_once Part 2 dispatches due-pending envelopes
- Bus.run_redelivery_sweep_once Part 2 skips future-backoff pending envelopes
- Terminal (non-EndpointUnavailable) failure still dead-letters immediately
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_core.bus.core import (
    Bus,
    BusConfig,
    EndpointSpec,
    SupervisorConfig,
    _compute_delivery_backoff,
)
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.protocol import EndpointUnavailable
from agent_core.clock import FakeClock


def _now() -> datetime:
    return datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)


def _envelope(id_="e1", to="x", **overrides) -> Envelope:
    fields = dict(
        id=id_,
        correlation_id="c1",
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=_now(),
    )
    fields.update(overrides)
    return Envelope(**fields)


class _TransientEndpoint:
    """Always raises EndpointUnavailable."""

    def __init__(self, name: str = "x"):
        self.name = name
        self.deliver_calls: int = 0

    async def start(self, bus) -> None:
        pass

    async def deliver(self, envelope: Envelope) -> None:
        self.deliver_calls += 1
        raise EndpointUnavailable("offline")

    async def stop(self) -> None:
        pass


class _TerminalEndpoint:
    """Always raises a non-EndpointUnavailable exception."""

    def __init__(self, name: str = "x"):
        self.name = name

    async def start(self, bus) -> None:
        pass

    async def deliver(self, envelope: Envelope) -> None:
        raise RuntimeError("boom")

    async def stop(self) -> None:
        pass


class _SuccessEndpoint:
    """Records deliveries and succeeds."""

    def __init__(self, name: str = "x"):
        self.name = name
        self.delivered: list[Envelope] = []

    async def start(self, bus) -> None:
        pass

    async def deliver(self, envelope: Envelope) -> None:
        self.delivered.append(envelope)

    async def stop(self) -> None:
        pass


@pytest.fixture
async def clock() -> FakeClock:
    return FakeClock(start=_now())


@pytest.fixture
async def bus(tmp_path: Path, clock: FakeClock) -> Bus:
    cfg = BusConfig(
        storage_path=tmp_path / "bus.sqlite",
        max_delivery_attempts=3,
    )
    b = Bus(cfg, clock=clock)
    b.register(EndpointSpec(endpoint=_TransientEndpoint("x")))
    yield b
    await b.stop()


class TestComputeDeliveryBackoff:
    def test_attempt_1_within_range(self):
        cfg = SupervisorConfig(
            delivery_backoff_base_seconds=2,
            delivery_backoff_factor=2,
            delivery_backoff_cap_seconds=60,
        )
        # attempt 1: uniform(0, min(2 * 2^0, 60)) = uniform(0, 2)
        for _ in range(20):
            result = _compute_delivery_backoff(1, cfg)
            assert 0.0 <= result <= 2.0

    def test_attempt_2_within_range(self):
        cfg = SupervisorConfig(
            delivery_backoff_base_seconds=2,
            delivery_backoff_factor=2,
            delivery_backoff_cap_seconds=60,
        )
        # attempt 2: uniform(0, min(2 * 2^1, 60)) = uniform(0, 4)
        for _ in range(20):
            result = _compute_delivery_backoff(2, cfg)
            assert 0.0 <= result <= 4.0

    def test_capped_at_cap(self):
        cfg = SupervisorConfig(
            delivery_backoff_base_seconds=2,
            delivery_backoff_factor=2,
            delivery_backoff_cap_seconds=60,
        )
        # attempt 10: 2 * 2^9 = 1024, capped at 60
        for _ in range(20):
            result = _compute_delivery_backoff(10, cfg)
            assert 0.0 <= result <= 60.0

    def test_non_negative(self):
        cfg = SupervisorConfig(
            delivery_backoff_base_seconds=2,
            delivery_backoff_factor=2,
            delivery_backoff_cap_seconds=60,
        )
        for attempt in range(1, 6):
            assert _compute_delivery_backoff(attempt, cfg) >= 0.0


class TestDispatchBackoff:
    async def test_dispatch_sets_next_attempt_at_on_transient_failure(
        self, bus: Bus, clock: FakeClock
    ):
        """EndpointUnavailable → envelope pending with next_attempt_at set."""
        await bus.start()
        env = _envelope(to="x")
        await bus._store.insert(env)
        await bus._dispatch(env)
        row = await bus._store.row(env.id)
        assert row["state"] == "pending"
        # next_attempt_at must be set (>= now because backoff >= 0)
        assert row["next_attempt_at"] is not None

    async def test_dispatch_dead_letters_at_max_attempts_transient(
        self, tmp_path: Path, clock: FakeClock
    ):
        """Transient failure when delivery_count >= max_delivery_attempts → dead_letter."""
        cfg = BusConfig(storage_path=tmp_path / "bus2.sqlite", max_delivery_attempts=2)
        b = Bus(cfg, clock=clock)
        ep = _TransientEndpoint("x")
        b.register(EndpointSpec(endpoint=ep))
        await b.start()
        try:
            env = _envelope(to="x")
            await b._store.insert(env)
            # Simulate delivery_count already at max (mark_in_flight increments to max+1 on dispatch,
            # but we set it beforehand so after mark_in_flight it equals max)
            await b._store._conn.execute(
                "UPDATE envelopes SET delivery_count = ? WHERE id = ?",
                (cfg.max_delivery_attempts - 1, env.id),
            )
            await b._store._conn.commit()
            await b._dispatch(env)
            row = await b._store.row(env.id)
            assert row["state"] == "dead_letter"
        finally:
            await b.stop()

    async def test_terminal_failure_dead_letters_immediately(
        self, tmp_path: Path, clock: FakeClock
    ):
        """Non-EndpointUnavailable exception → dead_letter with no backoff."""
        cfg = BusConfig(storage_path=tmp_path / "bus3.sqlite", max_delivery_attempts=5)
        b = Bus(cfg, clock=clock)
        ep = _TerminalEndpoint("x")
        b.register(EndpointSpec(endpoint=ep))
        await b.start()
        try:
            env = _envelope(to="x")
            await b._store.insert(env)
            await b._dispatch(env)
            row = await b._store.row(env.id)
            assert row["state"] == "dead_letter"
            assert "boom" in (row["nack_reason"] or "")
            # No next_attempt_at — it went straight to dead_letter.
            assert row["next_attempt_at"] is None
        finally:
            await b.stop()


class TestSweepBackoff:
    async def test_sweep_part1_requeues_with_backoff_no_immediate_redispatch(
        self, tmp_path: Path, clock: FakeClock
    ):
        """Stale in-flight within attempt limit → pending with next_attempt_at set; not re-dispatched."""
        cfg = BusConfig(
            storage_path=tmp_path / "bus4.sqlite",
            max_delivery_attempts=5,
            redelivery_timeout_seconds=1,
        )
        ep = _SuccessEndpoint("x")
        b = Bus(cfg, clock=clock)
        b.register(EndpointSpec(endpoint=ep))
        await b.start()
        try:
            env = _envelope(to="x")
            # Insert directly (not via _enqueue) to control state.
            await b._store.insert(env)
            # Mark in-flight with a past timeout (delivery_count=1).
            past_timeout = _now() - timedelta(minutes=5)
            await b._store.mark_in_flight(env.id, past_timeout)
            # Force in_flight_until to be in the past so the sweep picks it up.
            await b._store._conn.execute(
                "UPDATE envelopes SET in_flight_until = ? WHERE id = ?",
                (past_timeout.isoformat(), env.id),
            )
            await b._store._conn.commit()

            initial_deliver_count = ep.deliver_calls if hasattr(ep, "deliver_calls") else len(ep.delivered)
            await b.run_redelivery_sweep_once(now=_now())

            row = await b._store.row(env.id)
            # Part 1 must NOT immediately re-dispatch (stays pending with backoff).
            assert row["state"] == "pending"
            assert row["next_attempt_at"] is not None
            # Part 2 will skip this because next_attempt_at >= now.
            assert len(ep.delivered) == initial_deliver_count
        finally:
            await b.stop()

    async def test_sweep_part2_dispatches_due_pending(
        self, tmp_path: Path, clock: FakeClock
    ):
        """Pending with past next_attempt_at → dispatched by sweep Part 2."""
        cfg = BusConfig(storage_path=tmp_path / "bus5.sqlite", max_delivery_attempts=5)
        ep = _SuccessEndpoint("x")
        b = Bus(cfg, clock=clock)
        b.register(EndpointSpec(endpoint=ep))
        await b.start()
        try:
            env = _envelope(to="x")
            await b._store.insert(env)
            # Set next_attempt_at in the past so it's due.
            past = _now() - timedelta(seconds=5)
            await b._store.requeue_with_backoff(env.id, past)

            await b.run_redelivery_sweep_once(now=_now())

            # Part 2 must have dispatched it.
            assert len(ep.delivered) == 1
            assert ep.delivered[0].id == env.id
        finally:
            await b.stop()

    async def test_sweep_part2_skips_future_next_attempt_at(
        self, tmp_path: Path, clock: FakeClock
    ):
        """Pending with future next_attempt_at → not dispatched by sweep Part 2."""
        cfg = BusConfig(storage_path=tmp_path / "bus6.sqlite", max_delivery_attempts=5)
        ep = _SuccessEndpoint("x")
        b = Bus(cfg, clock=clock)
        b.register(EndpointSpec(endpoint=ep))
        await b.start()
        try:
            env = _envelope(to="x")
            await b._store.insert(env)
            # Set next_attempt_at in the future.
            future = _now() + timedelta(seconds=30)
            await b._store.requeue_with_backoff(env.id, future)

            await b.run_redelivery_sweep_once(now=_now())

            # Part 2 must NOT have dispatched it.
            assert len(ep.delivered) == 0
        finally:
            await b.stop()
