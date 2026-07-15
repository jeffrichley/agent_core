"""Tests for degraded boot and supervisor wiring (issue #273).

Covers:
- Bus.start() with a failing endpoint quarantines it and continues
- Healthy endpoints are not rolled back
- Zero-started-all-fail case still sets _started=True and logs CRITICAL
- Bus with no endpoints starts cleanly
- Supervisor quarantine is persisted to the supervisor_state table
- Task-failure hook feeds supervisor.record_failure()
- Supervisor tick drives restart of a restarting endpoint
- Successful restart returns to active
- Restart drains pending mail for the restarted endpoint
- Recovery clears the supervisor_state entry
- run_supervisor_tick_once() is a no-op before start()
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from agent_core.bus.core import Bus, BusConfig, EndpointSpec, SupervisorConfig
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.clock import FakeClock

_START = datetime(2026, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fake endpoints
# ---------------------------------------------------------------------------


class _OkEndpoint:
    def __init__(self, name: str) -> None:
        self.name = name
        self.started = False
        self.stopped = False
        self.delivered: list[Envelope] = []
        self.start_count = 0

    async def start(self, handle) -> None:
        self.started = True
        self.start_count += 1

    async def deliver(self, env: Envelope) -> None:
        self.delivered.append(env)

    async def stop(self) -> None:
        self.stopped = True


class _FailOnStartEndpoint:
    """Fails on first start() call; succeeds on subsequent calls (simulating a restart)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._start_count = 0
        self.started = False

    async def start(self, handle) -> None:
        self._start_count += 1
        if self._start_count == 1:
            raise RuntimeError(f"{self.name}: start failed")
        self.started = True  # succeeds on 2nd call (restart / probe)

    async def deliver(self, env: Envelope) -> None:
        pass

    async def stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Shared factory
# ---------------------------------------------------------------------------


def _make_bus(
    tmp_path: Path,
    *,
    clock: FakeClock | None = None,
    supervisor: SupervisorConfig | None = None,
) -> Bus:
    cfg = BusConfig(
        storage_path=tmp_path / "bus.sqlite",
        supervisor=supervisor
        or SupervisorConfig(
            restart_backoff_base_seconds=1,
            restart_backoff_factor=2,
            restart_backoff_cap_seconds=60,
            restart_jitter="none",
            probe_interval_seconds=300,
        ),
    )
    return Bus(cfg, clock=clock)


# ---------------------------------------------------------------------------
# TestDegradedBoot
# ---------------------------------------------------------------------------


class TestDegradedBoot:
    async def test_bus_starts_when_endpoint_fails(self, tmp_path: Path) -> None:
        fail_ep = _FailOnStartEndpoint("fail")
        ok_ep = _OkEndpoint("ok")
        bus = _make_bus(tmp_path)
        bus.register(EndpointSpec(endpoint=fail_ep))
        bus.register(EndpointSpec(endpoint=ok_ep))
        try:
            await bus.start()
            assert bus._started is True
        finally:
            await bus.stop()

    async def test_failing_endpoint_is_quarantined(self, tmp_path: Path) -> None:
        fail_ep = _FailOnStartEndpoint("fail")
        ok_ep = _OkEndpoint("ok")
        bus = _make_bus(tmp_path)
        bus.register(EndpointSpec(endpoint=fail_ep))
        bus.register(EndpointSpec(endpoint=ok_ep))
        try:
            await bus.start()
            assert bus._supervisor is not None
            state = bus._supervisor.state("fail")
            assert state is not None
            assert state.status == "quarantined"
        finally:
            await bus.stop()

    async def test_healthy_endpoint_is_active(self, tmp_path: Path) -> None:
        fail_ep = _FailOnStartEndpoint("fail")
        ok_ep = _OkEndpoint("ok")
        bus = _make_bus(tmp_path)
        bus.register(EndpointSpec(endpoint=fail_ep))
        bus.register(EndpointSpec(endpoint=ok_ep))
        try:
            await bus.start()
            assert bus._supervisor is not None
            state = bus._supervisor.state("ok")
            assert state is not None
            assert state.status == "active"
        finally:
            await bus.stop()

    async def test_good_endpoint_not_rolled_back(self, tmp_path: Path) -> None:
        fail_ep = _FailOnStartEndpoint("fail")
        ok_ep = _OkEndpoint("ok")
        bus = _make_bus(tmp_path)
        bus.register(EndpointSpec(endpoint=fail_ep))
        bus.register(EndpointSpec(endpoint=ok_ep))
        try:
            await bus.start()
            assert ok_ep.stopped is False
        finally:
            await bus.stop()

    async def test_transition_callback_fires_on_quarantine(self, tmp_path: Path) -> None:
        """Quarantine fires on_transition which persists to supervisor_state."""
        fail_ep = _FailOnStartEndpoint("fail")
        bus = _make_bus(tmp_path)
        bus.register(EndpointSpec(endpoint=fail_ep))
        try:
            await bus.start()
            assert bus._store is not None
            degraded = await bus._store.list_supervisor_degraded()
            names = [row["name"] for row in degraded]
            assert "fail" in names
        finally:
            await bus.stop()

    async def test_zero_started_logs_critical(self, tmp_path: Path, caplog) -> None:
        fail1 = _FailOnStartEndpoint("fail1")
        fail2 = _FailOnStartEndpoint("fail2")
        bus = _make_bus(tmp_path)
        bus.register(EndpointSpec(endpoint=fail1))
        bus.register(EndpointSpec(endpoint=fail2))
        try:
            with caplog.at_level(logging.DEBUG, logger="agent_core.bus.core"):
                await bus.start()
            assert bus._started is True
            assert any(r.levelno >= logging.CRITICAL for r in caplog.records)
        finally:
            await bus.stop()

    async def test_bus_with_no_endpoints_starts_cleanly(self, tmp_path: Path) -> None:
        bus = _make_bus(tmp_path)
        try:
            await bus.start()
            assert bus._started is True
        finally:
            await bus.stop()

    async def test_quarantine_persisted_to_store(self, tmp_path: Path) -> None:
        fail_ep = _FailOnStartEndpoint("fail")
        bus = _make_bus(tmp_path)
        bus.register(EndpointSpec(endpoint=fail_ep))
        try:
            await bus.start()
            assert bus._store is not None
            degraded = await bus._store.list_supervisor_degraded()
            assert len(degraded) == 1
            assert degraded[0]["name"] == "fail"
            assert degraded[0]["last_error"]  # non-empty string
        finally:
            await bus.stop()


# ---------------------------------------------------------------------------
# TestSupervisorWiring
# ---------------------------------------------------------------------------


class TestSupervisorWiring:
    async def test_task_failure_feeds_supervisor(self, tmp_path: Path) -> None:
        ok_ep = _OkEndpoint("ok")
        bus = _make_bus(tmp_path)
        bus.register(EndpointSpec(endpoint=ok_ep))
        try:
            await bus.start()
            handle = bus._handles["ok"]

            async def _fail() -> None:
                raise RuntimeError("task boom")

            handle.spawn(_fail())
            await asyncio.sleep(0)  # let task run and raise
            await asyncio.sleep(0)  # let done-callback fire (scheduled in prior turn)
            assert bus._supervisor is not None
            state = bus._supervisor.state("ok")
            assert state is not None
            assert state.status == "restarting"
        finally:
            await bus.stop()

    async def test_supervisor_tick_restarts_restarting_endpoint(self, tmp_path: Path) -> None:
        clock = FakeClock(start=_START)
        ok_ep = _OkEndpoint("ok")
        bus = _make_bus(tmp_path, clock=clock)
        bus.register(EndpointSpec(endpoint=ok_ep))
        try:
            await bus.start()
            assert ok_ep.start_count == 1
            assert bus._supervisor is not None
            bus._supervisor.record_failure("ok", "err")
            # Advance past restart_backoff_base_seconds (1s, jitter=none)
            clock.advance(1)
            await bus.run_supervisor_tick_once(now=clock.now())
            # _restart_endpoint called endpoint.start() a second time
            assert ok_ep.start_count == 2
        finally:
            await bus.stop()

    async def test_restart_success_returns_to_active(self, tmp_path: Path) -> None:
        clock = FakeClock(start=_START)
        ok_ep = _OkEndpoint("ok")
        bus = _make_bus(tmp_path, clock=clock)
        bus.register(EndpointSpec(endpoint=ok_ep))
        try:
            await bus.start()
            assert bus._supervisor is not None
            bus._supervisor.record_failure("ok", "err")
            clock.advance(1)
            await bus.run_supervisor_tick_once(now=clock.now())
            state = bus._supervisor.state("ok")
            assert state is not None
            assert state.status == "active"
        finally:
            await bus.stop()

    async def test_restart_drains_pending_mail(self, tmp_path: Path) -> None:
        clock = FakeClock(start=_START)
        ok_ep = _OkEndpoint("ok")
        bus = _make_bus(tmp_path, clock=clock)
        bus.register(EndpointSpec(endpoint=ok_ep))
        try:
            await bus.start()
            # Insert a pending envelope directly in the store (bypass normal dispatch)
            env = Envelope(
                id="test-drain-env",
                correlation_id="c1",
                from_="other",
                to="ok",
                kind="TextMessage",
                payload=TextMessagePayload(text="pending mail"),
                created_at=_START,
            )
            assert bus._store is not None
            await bus._store.insert(env)
            # Force into restarting and advance clock
            assert bus._supervisor is not None
            bus._supervisor.record_failure("ok", "err")
            clock.advance(1)
            await bus.run_supervisor_tick_once(now=clock.now())
            # drain_for should have dispatched the pending envelope
            assert len(ok_ep.delivered) == 1
            assert ok_ep.delivered[0].id == "test-drain-env"
        finally:
            await bus.stop()

    async def test_recovered_event_persisted(self, tmp_path: Path) -> None:
        clock = FakeClock(start=_START)
        fail_ep = _FailOnStartEndpoint("fail")
        bus = _make_bus(tmp_path, clock=clock)
        bus.register(EndpointSpec(endpoint=fail_ep))
        try:
            await bus.start()
            # After degraded boot: "fail" is quarantined and persisted
            assert bus._store is not None
            degraded = await bus._store.list_supervisor_degraded()
            assert len(degraded) == 1
            # Advance past probe_interval_seconds (300s) to trigger probe
            clock.advance(300)
            await bus.run_supervisor_tick_once(now=clock.now())
            # Probe succeeds (2nd start() call) → recovery → clear_supervisor_state
            degraded_after = await bus._store.list_supervisor_degraded()
            assert degraded_after == []
        finally:
            await bus.stop()


# ---------------------------------------------------------------------------
# TestRunSupervisorTickOnce
# ---------------------------------------------------------------------------


class TestRunSupervisorTickOnce:
    async def test_tick_noop_before_supervisor_created(self, tmp_path: Path) -> None:
        bus = _make_bus(tmp_path)
        # Supervisor is None before start() — tick should be a no-op, not raise
        await bus.run_supervisor_tick_once()
