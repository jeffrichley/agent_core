"""Full state-machine and unit tests for EndpointSupervisor (issue #272).

Covers:
- EndpointState defaults
- _compute_restart_backoff pure function (no jitter + full jitter)
- EndpointSupervisor.register / state / all_states
- record_failure: active→restarting, idempotent updates when already restarting/quarantined
- record_success: reset to active/closed
- tick: does not fire before next_probe_at; fires at/after next_probe_at
- _attempt_restart: success→active/closed; failure→increments counter, schedules backoff; quarantine after N
- _attempt_probe: half_open transition; success→active/closed; failure→back to quarantined
- Full state-machine cycle: closed→restarting→open→half_open→closed + half_open→open
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_core.bus.core import SupervisorConfig
from agent_core.bus.supervisor import EndpointState, EndpointSupervisor, _compute_restart_backoff
from agent_core.clock import FakeClock

_START = datetime(2026, 1, 1, tzinfo=UTC)


def _config(**overrides) -> SupervisorConfig:
    """Return a SupervisorConfig with sensible test defaults, overridable."""
    defaults = dict(
        restart_backoff_base_seconds=1,
        restart_backoff_factor=2,
        restart_backoff_cap_seconds=60,
        restart_jitter="none",
        restarts_before_quarantine=3,
        probe_interval_seconds=300,
    )
    defaults.update(overrides)
    return SupervisorConfig(**defaults)


class FakeRestart:
    def __init__(self, *, fails: int = 0) -> None:
        self.calls: list[str] = []
        self.fails = fails  # raise on first N calls

    async def __call__(self, name: str) -> None:
        self.calls.append(name)
        if self.fails > 0:
            self.fails -= 1
            raise RuntimeError(f"restart failed for {name}")


# ---------------------------------------------------------------------------
# TestEndpointStateDefaults
# ---------------------------------------------------------------------------


class TestEndpointStateDefaults:
    def test_default_state_is_active_closed(self) -> None:
        state = EndpointState(name="x")
        assert state.status == "active"
        assert state.breaker == "closed"
        assert state.consecutive_failures == 0
        assert state.last_error is None
        assert state.next_probe_at is None


# ---------------------------------------------------------------------------
# TestComputeRestartBackoff
# ---------------------------------------------------------------------------


class TestComputeRestartBackoff:
    def test_attempt_1_no_jitter(self) -> None:
        cfg = SupervisorConfig(
            restart_backoff_base_seconds=2,
            restart_backoff_factor=3,
            restart_backoff_cap_seconds=100,
            restart_jitter="none",
        )
        assert _compute_restart_backoff(1, cfg) == 2.0

    def test_attempt_2_no_jitter(self) -> None:
        cfg = SupervisorConfig(
            restart_backoff_base_seconds=2,
            restart_backoff_factor=3,
            restart_backoff_cap_seconds=100,
            restart_jitter="none",
        )
        assert _compute_restart_backoff(2, cfg) == 6.0

    def test_attempt_5_capped_no_jitter(self) -> None:
        cfg = SupervisorConfig(
            restart_backoff_base_seconds=2,
            restart_backoff_factor=3,
            restart_backoff_cap_seconds=100,
            restart_jitter="none",
        )
        # 2 * 3^4 = 162 → capped at 100
        assert _compute_restart_backoff(5, cfg) == 100.0

    def test_full_jitter_within_range(self) -> None:
        cfg = SupervisorConfig(
            restart_backoff_base_seconds=2,
            restart_backoff_factor=3,
            restart_backoff_cap_seconds=100,
            restart_jitter="full",
        )
        # attempt=3 → min(100, 2*3^2) = min(100, 18) = 18; jitter in [0, 18]
        computed_max = 18.0
        for _ in range(200):
            val = _compute_restart_backoff(3, cfg)
            assert 0.0 <= val <= computed_max


# ---------------------------------------------------------------------------
# TestRegister
# ---------------------------------------------------------------------------


class TestRegister:
    def _supervisor(self) -> EndpointSupervisor:
        return EndpointSupervisor(
            config=_config(),
            clock=FakeClock(start=_START),
            restart_fn=FakeRestart(),
        )

    def test_register_creates_active_closed_state(self) -> None:
        sup = self._supervisor()
        sup.register("ep1")
        s = sup.state("ep1")
        assert s is not None
        assert s.name == "ep1"
        assert s.status == "active"
        assert s.breaker == "closed"

    def test_register_duplicate_raises_value_error(self) -> None:
        sup = self._supervisor()
        sup.register("ep1")
        with pytest.raises(ValueError, match="ep1"):
            sup.register("ep1")

    def test_state_returns_none_for_unknown(self) -> None:
        sup = self._supervisor()
        assert sup.state("nonexistent") is None

    def test_all_states_returns_all_registered(self) -> None:
        sup = self._supervisor()
        sup.register("a")
        sup.register("b")
        names = {s.name for s in sup.all_states()}
        assert names == {"a", "b"}


# ---------------------------------------------------------------------------
# TestRecordFailure
# ---------------------------------------------------------------------------


class TestRecordFailure:
    def _supervisor(self) -> tuple[EndpointSupervisor, FakeClock, FakeRestart]:
        clock = FakeClock(start=_START)
        restart_fn = FakeRestart()
        sup = EndpointSupervisor(config=_config(), clock=clock, restart_fn=restart_fn)
        sup.register("x")
        return sup, clock, restart_fn

    def test_record_failure_from_closed_transitions_to_restarting(self) -> None:
        sup, clock, _ = self._supervisor()
        sup.record_failure("x", error="oops")
        s = sup.state("x")
        assert s is not None
        assert s.status == "restarting"
        assert s.breaker == "closed"
        assert s.last_error == "oops"

    def test_record_failure_schedules_restart_at_backoff_from_now(self) -> None:
        # jitter="none" so backoff is deterministic: base=1, factor=2, attempt=1 → 1s
        sup, clock, _ = self._supervisor()
        t0 = clock.now()
        sup.record_failure("x", error="oops")
        s = sup.state("x")
        assert s is not None
        expected = t0 + timedelta(seconds=1.0)  # base=1, attempt=1, jitter=none
        assert s.next_probe_at == expected

    def test_record_failure_when_restarting_updates_last_error_only(self) -> None:
        sup, clock, _ = self._supervisor()
        sup.record_failure("x", error="first")
        s = sup.state("x")
        assert s is not None
        original_next_probe_at = s.next_probe_at
        # Second record_failure while already restarting
        sup.record_failure("x", error="second")
        s2 = sup.state("x")
        assert s2 is not None
        assert s2.status == "restarting"
        assert s2.last_error == "second"
        assert s2.next_probe_at == original_next_probe_at  # unchanged

    def test_record_failure_when_quarantined_updates_last_error_only(self) -> None:
        # Manually set up quarantined state
        clock = FakeClock(start=_START)
        restart_fn = FakeRestart()
        sup = EndpointSupervisor(config=_config(), clock=clock, restart_fn=restart_fn)
        sup.register("x")
        # Force quarantined state directly
        s = sup.state("x")
        assert s is not None
        s.status = "quarantined"
        s.breaker = "open"
        s.next_probe_at = clock.now() + timedelta(seconds=300)
        original_next_probe_at = s.next_probe_at
        # record_failure on a quarantined endpoint
        sup.record_failure("x", error="new error while quarantined")
        s2 = sup.state("x")
        assert s2 is not None
        assert s2.status == "quarantined"  # unchanged
        assert s2.last_error == "new error while quarantined"
        assert s2.next_probe_at == original_next_probe_at  # unchanged


# ---------------------------------------------------------------------------
# TestRecordSuccess
# ---------------------------------------------------------------------------


class TestRecordSuccess:
    def test_record_success_resets_to_active_closed(self) -> None:
        clock = FakeClock(start=_START)
        restart_fn = FakeRestart()
        sup = EndpointSupervisor(config=_config(), clock=clock, restart_fn=restart_fn)
        sup.register("x")
        sup.record_failure("x", error="oops")
        sup.record_success("x")
        s = sup.state("x")
        assert s is not None
        assert s.status == "active"
        assert s.breaker == "closed"
        assert s.consecutive_failures == 0
        assert s.last_error is None
        assert s.next_probe_at is None


# ---------------------------------------------------------------------------
# TestTickRestarting
# ---------------------------------------------------------------------------


class TestTickRestarting:
    def _setup(
        self, *, restarts_before_quarantine: int = 3, fails: int = 0
    ) -> tuple[EndpointSupervisor, FakeClock, FakeRestart]:
        clock = FakeClock(start=_START)
        restart_fn = FakeRestart(fails=fails)
        cfg = _config(restarts_before_quarantine=restarts_before_quarantine)
        sup = EndpointSupervisor(config=cfg, clock=clock, restart_fn=restart_fn)
        sup.register("x")
        return sup, clock, restart_fn

    async def test_tick_before_restart_due_does_not_call_restart_fn(self) -> None:
        sup, clock, restart_fn = self._setup()
        # next_probe_at will be set to t0 + 1s (base=1 jitter=none attempt=1)
        sup.record_failure("x", error="oops")
        # tick at t=0 (before due)
        await sup.tick(clock.now())
        assert restart_fn.calls == []

    async def test_tick_at_restart_due_calls_restart_fn(self) -> None:
        sup, clock, restart_fn = self._setup()
        sup.record_failure("x", error="oops")
        # Advance to the restart time (base=1s)
        clock.advance(1)
        await sup.tick(clock.now())
        assert restart_fn.calls == ["x"]

    async def test_successful_restart_transitions_to_active_closed(self) -> None:
        sup, clock, restart_fn = self._setup(fails=0)
        sup.record_failure("x", error="oops")
        clock.advance(1)
        await sup.tick(clock.now())
        s = sup.state("x")
        assert s is not None
        assert s.status == "active"
        assert s.breaker == "closed"
        assert s.consecutive_failures == 0
        assert s.next_probe_at is None

    async def test_failed_restart_increments_consecutive_failures(self) -> None:
        sup, clock, restart_fn = self._setup(fails=1)
        sup.record_failure("x", error="oops")
        clock.advance(1)
        await sup.tick(clock.now())
        s = sup.state("x")
        assert s is not None
        assert s.consecutive_failures == 1
        assert s.status == "restarting"

    async def test_failed_restart_schedules_next_backoff(self) -> None:
        # After 1 failure, consecutive_failures=1, next attempt=2 → base*factor^1=2s
        sup, clock, restart_fn = self._setup(fails=1)
        sup.record_failure("x", error="oops")
        clock.advance(1)  # to trigger first restart
        t_after_first_tick = clock.now()
        await sup.tick(t_after_first_tick)
        s = sup.state("x")
        assert s is not None
        assert s.status == "restarting"
        # next backoff: attempt=2 → 1 * 2^1 = 2s
        expected = t_after_first_tick + timedelta(seconds=2.0)
        assert s.next_probe_at == expected

    async def test_quarantine_after_exactly_n_failed_restarts(self) -> None:
        # restarts_before_quarantine=3: 3 failed restarts → quarantine
        sup, clock, restart_fn = self._setup(restarts_before_quarantine=3, fails=10)
        sup.record_failure("x", error="start fail")

        # Tick 3 times (each time advancing past next_probe_at)
        # restart 1: next_probe_at = t0+1, advance 1s
        clock.advance(1)
        await sup.tick(clock.now())
        s = sup.state("x")
        assert s is not None
        assert s.consecutive_failures == 1
        assert s.status == "restarting"

        # restart 2: next_probe_at = t0+1+2=t0+3, need +2 more
        clock.advance(2)
        await sup.tick(clock.now())
        s = sup.state("x")
        assert s is not None
        assert s.consecutive_failures == 2
        assert s.status == "restarting"

        # restart 3: next_probe_at = t0+3+4=t0+7, need +4 more
        clock.advance(4)
        await sup.tick(clock.now())
        s = sup.state("x")
        assert s is not None
        assert s.consecutive_failures == 3
        assert s.status == "quarantined"
        assert s.breaker == "open"

    async def test_quarantine_sets_next_probe_at(self) -> None:
        sup, clock, restart_fn = self._setup(restarts_before_quarantine=1, fails=10)
        sup.record_failure("x", error="oops")
        clock.advance(1)
        t_tick = clock.now()
        await sup.tick(t_tick)
        s = sup.state("x")
        assert s is not None
        assert s.status == "quarantined"
        # probe_interval_seconds=300 from _config()
        expected = t_tick + timedelta(seconds=300)
        assert s.next_probe_at == expected

    async def test_successful_restart_after_partial_failures_resets_counter(self) -> None:
        # Fail twice, succeed on the third attempt
        sup, clock, restart_fn = self._setup(restarts_before_quarantine=5, fails=2)
        sup.record_failure("x", error="initial")

        # First restart fails
        clock.advance(1)
        await sup.tick(clock.now())
        assert sup.state("x").consecutive_failures == 1  # type: ignore[union-attr]

        # Second restart fails
        clock.advance(2)
        await sup.tick(clock.now())
        assert sup.state("x").consecutive_failures == 2  # type: ignore[union-attr]

        # Third restart succeeds (fails=0 now)
        clock.advance(4)
        await sup.tick(clock.now())
        s = sup.state("x")
        assert s is not None
        assert s.status == "active"
        assert s.breaker == "closed"
        assert s.consecutive_failures == 0


# ---------------------------------------------------------------------------
# TestTickQuarantined
# ---------------------------------------------------------------------------


class TestTickQuarantined:
    def _setup_quarantined(
        self, *, probe_fails: int = 0
    ) -> tuple[EndpointSupervisor, FakeClock, FakeRestart]:
        """Set up an endpoint in quarantined/open state directly (without async tick).

        Uses direct state mutation so this sync helper can be called from async tests
        without needing run_until_complete inside an already-running event loop.
        """
        clock = FakeClock(start=_START)
        probe_fn = FakeRestart(fails=probe_fails)
        cfg = _config(restarts_before_quarantine=1)
        sup = EndpointSupervisor(config=cfg, clock=clock, restart_fn=probe_fn)
        sup.register("x")

        # Directly set quarantined state (simulating what tick+_attempt_restart would do)
        s = sup.state("x")
        assert s is not None
        s.status = "quarantined"
        s.breaker = "open"
        s.consecutive_failures = 1
        s.last_error = "initial failure"
        s.next_probe_at = clock.now() + timedelta(seconds=300)

        return sup, clock, probe_fn

    async def test_tick_before_probe_due_does_not_probe(self) -> None:
        sup, clock, probe_fn = self._setup_quarantined(probe_fails=0)
        # Don't advance past probe_interval_seconds (300s)
        clock.advance(100)
        await sup.tick(clock.now())
        assert probe_fn.calls == []

    async def test_tick_at_probe_calls_restart_fn(self) -> None:
        sup, clock, probe_fn = self._setup_quarantined(probe_fails=0)
        clock.advance(300)  # probe_interval_seconds=300
        await sup.tick(clock.now())
        assert probe_fn.calls == ["x"]

    async def test_probe_success_transitions_to_active_closed(self) -> None:
        sup, clock, probe_fn = self._setup_quarantined(probe_fails=0)
        clock.advance(300)
        await sup.tick(clock.now())
        s = sup.state("x")
        assert s is not None
        assert s.status == "active"
        assert s.breaker == "closed"
        assert s.consecutive_failures == 0
        assert s.next_probe_at is None

    async def test_probe_failure_returns_to_open_resets_probe_timer(self) -> None:
        sup, clock, probe_fn = self._setup_quarantined(probe_fails=1)
        clock.advance(300)
        t_probe = clock.now()
        await sup.tick(t_probe)
        s = sup.state("x")
        assert s is not None
        assert s.status == "quarantined"
        assert s.breaker == "open"
        expected = t_probe + timedelta(seconds=300)
        assert s.next_probe_at == expected


# ---------------------------------------------------------------------------
# TestFullStateMachine
# ---------------------------------------------------------------------------


class TestFullStateMachine:
    async def test_full_cycle_closed_restarting_open_half_open_closed(self) -> None:
        """closed → record_failure → restarting → 2 failed restarts → quarantined/open
        → advance past probe_interval → half_open → probe succeeds → active/closed."""
        clock = FakeClock(start=_START)
        # 2 restarts to quarantine, then probe succeeds
        restart_fn = FakeRestart(fails=2)
        cfg = _config(restarts_before_quarantine=2)
        sup = EndpointSupervisor(config=cfg, clock=clock, restart_fn=restart_fn)
        sup.register("x")

        # CLOSED: record_failure → restarting
        sup.record_failure("x", error="boom")
        s = sup.state("x")
        assert s is not None
        assert s.status == "restarting"
        assert s.breaker == "closed"

        # RESTARTING → first failed restart (consecutive_failures=1)
        clock.advance(1)  # base=1, attempt=1
        await sup.tick(clock.now())
        s = sup.state("x")
        assert s is not None
        assert s.consecutive_failures == 1
        assert s.status == "restarting"

        # RESTARTING → second failed restart → OPEN/quarantined
        clock.advance(2)  # attempt=2 → 1*2^1=2s
        await sup.tick(clock.now())
        s = sup.state("x")
        assert s is not None
        assert s.status == "quarantined"
        assert s.breaker == "open"
        assert s.consecutive_failures == 2

        # Advance past probe_interval to trigger HALF_OPEN probe
        clock.advance(300)  # probe_interval_seconds=300
        await sup.tick(clock.now())

        # After successful probe → CLOSED
        s = sup.state("x")
        assert s is not None
        assert s.status == "active"
        assert s.breaker == "closed"
        assert s.consecutive_failures == 0
        assert s.next_probe_at is None

    async def test_full_cycle_closed_restarting_open_half_open_open(self) -> None:
        """Same setup but probe fails → back to quarantined/open."""
        clock = FakeClock(start=_START)
        # 2 restarts to quarantine, then probe also fails
        restart_fn = FakeRestart(fails=3)  # 2 restarts + 1 probe, all fail
        cfg = _config(restarts_before_quarantine=2)
        sup = EndpointSupervisor(config=cfg, clock=clock, restart_fn=restart_fn)
        sup.register("x")

        sup.record_failure("x", error="boom")

        # First failed restart
        clock.advance(1)
        await sup.tick(clock.now())

        # Second failed restart → quarantine
        clock.advance(2)
        await sup.tick(clock.now())
        s = sup.state("x")
        assert s is not None
        assert s.status == "quarantined"
        assert s.breaker == "open"

        # Advance past probe_interval; probe fails → back to quarantined/open
        clock.advance(300)
        t_probe = clock.now()
        await sup.tick(t_probe)

        s = sup.state("x")
        assert s is not None
        assert s.status == "quarantined"
        assert s.breaker == "open"
        # next_probe_at reset to now + probe_interval
        assert s.next_probe_at == t_probe + timedelta(seconds=300)
