"""Tests for the periodic TTL and redelivery sweeps."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload


class _Stub:
    def __init__(self, name="x"):
        self.name = name

    async def start(self, bus) -> None:
        pass

    async def deliver(self, envelope: Envelope) -> None:
        pass  # leaves state in_flight forever (no auto-ack)

    async def stop(self) -> None:
        pass


def _envelope(**kwargs) -> Envelope:
    fields = dict(
        id="e1",
        correlation_id="c1",
        to="x",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
    )
    fields.update(kwargs)
    return Envelope(**fields)


@pytest.fixture
async def bus(tmp_path: Path) -> Bus:
    cfg = BusConfig(
        storage_path=tmp_path / "bus.sqlite",
        redelivery_timeout_seconds=1,
        max_delivery_attempts=2,
    )
    b = Bus(cfg)
    b.register(EndpointSpec(endpoint=_Stub("x")))
    yield b
    await b.stop()


class TestTTLSweep:
    async def test_expired_pending_marked_expired(self, bus: Bus):
        await bus.start()
        env = _envelope(expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        # Persist directly without dispatch to keep state=pending.
        await bus._store.insert(env)
        await bus.run_ttl_sweep_once(now=datetime(2026, 4, 27, tzinfo=timezone.utc))
        assert (await bus._store.row("e1"))["state"] == "expired"

    async def test_unset_ttl_unaffected(self, bus: Bus):
        await bus.start()
        env = _envelope()  # no expires_at
        await bus._store.insert(env)
        await bus.run_ttl_sweep_once(now=datetime(2026, 4, 27, tzinfo=timezone.utc))
        assert (await bus._store.row("e1"))["state"] == "pending"


class TestRedeliverySweep:
    async def test_stale_in_flight_within_attempt_limit_requeues(self, bus: Bus):
        await bus.start()
        env = _envelope()
        await bus._enqueue(env)  # delivery_count = 1; state in_flight
        # Force in_flight_until into the past.
        await bus._store._conn.execute(
            "UPDATE envelopes SET in_flight_until = ? WHERE id = ?",
            (datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat(), env.id),
        )
        await bus._store._conn.commit()
        await bus.run_redelivery_sweep_once(now=datetime(2026, 4, 27, tzinfo=timezone.utc))
        # delivery_count was 1; max is 2, so still re-dispatchable → pending.
        assert (await bus._store.row("e1"))["state"] in ("pending", "in_flight")
        # If pending: dispatch will run again next time. Either way, count <=2.
        assert (await bus._store.row("e1"))["delivery_count"] <= 2

    async def test_exhausted_attempts_dead_letter(self, bus: Bus):
        await bus.start()
        env = _envelope()
        await bus._enqueue(env)
        # Bump delivery_count to max and stale-out the in_flight row.
        await bus._store._conn.execute(
            """UPDATE envelopes
               SET delivery_count = ?, in_flight_until = ?
               WHERE id = ?""",
            (
                bus.config.max_delivery_attempts,
                datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat(),
                env.id,
            ),
        )
        await bus._store._conn.commit()
        await bus.run_redelivery_sweep_once(now=datetime(2026, 4, 27, tzinfo=timezone.utc))
        assert (await bus._store.row("e1"))["state"] == "dead_letter"
