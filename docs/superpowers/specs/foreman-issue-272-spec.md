# Spec: `EndpointSupervisor` + circuit-breaker state machine (issue #272)

## Goal

Add `EndpointSupervisor` as a new module at `packages/core/src/agent_core/bus/supervisor.py`, implementing the per-endpoint circuit-breaker state machine described in `docs/superpowers/specs/2026-07-13-agent-core-supervision-design.md`. This is **pure logic** with no bus wiring (that's T4/issue upcoming): it reads `SupervisorConfig` (already in `core.py` from T1/issue #270), uses an injected `Clock` (`agent_core.clock.FakeClock` / `SystemClock`) and an injected async restart callable. The module is fully unit-testable with no real sleeps.

Issue: https://github.com/jeffrichley/agent_core/issues/272

## Acceptance criteria

- `EndpointState` dataclass exists with fields: `name: str`, `status: Literal["active", "restarting", "quarantined"]`, `breaker: Literal["closed", "open", "half_open"]`, `consecutive_failures: int`, `last_error: str | None`, `next_probe_at: datetime | None`; defaults to `status="active"`, `breaker="closed"`, `consecutive_failures=0`, `last_error=None`, `next_probe_at=None`.
- `_compute_restart_backoff(attempt: int, config: SupervisorConfig) -> float` module-level function returns `random.uniform(0, min(cap, base * factor**(attempt-1)))` for `restart_jitter="full"`, and `min(cap, base * factor**(attempt-1))` for `restart_jitter="none"`. `attempt` is 1-indexed (attempt=1 is the first restart).
- `EndpointSupervisor.__init__(config, clock, restart_fn)` accepts `SupervisorConfig`, `Clock`, and a `Callable[[str], Awaitable[None]]` restart callable (name → restarts the endpoint, raises on failure).
- `EndpointSupervisor.register(name)` creates an `EndpointState(name=name)` in active/closed state; raises `ValueError` if already registered.
- `EndpointSupervisor.record_failure(name, error="")`: if `status == "active"` (breaker closed), transitions to `status="restarting"`, sets `next_probe_at = clock.now() + timedelta(seconds=_compute_restart_backoff(1, config))`. If status is already `"restarting"` or `"quarantined"`, updates `last_error` only (no reschedule).
- `EndpointSupervisor.record_success(name)`: resets `consecutive_failures=0`, `last_error=None`, `status="active"`, `breaker="closed"`, `next_probe_at=None`.
- `EndpointSupervisor.tick(now: datetime)` (async): for each state where `next_probe_at is not None and now >= next_probe_at` — if `status == "restarting"`, calls `_attempt_restart`; if `status == "quarantined"`, calls `_attempt_probe`. Skips if `next_probe_at` is in the future.
- `_attempt_restart` (private async): calls `restart_fn(name)`. On success: `status="active"`, `breaker="closed"`, `consecutive_failures=0`, `last_error=None`, `next_probe_at=None`. On failure: `consecutive_failures += 1`, `last_error = str(exc)`. If `consecutive_failures >= config.restarts_before_quarantine`: `status="quarantined"`, `breaker="open"`, `next_probe_at = now + timedelta(seconds=config.probe_interval_seconds)`. Else: schedule next restart at `now + timedelta(seconds=_compute_restart_backoff(consecutive_failures + 1, config))`.
- `_attempt_probe` (private async): sets `breaker="half_open"` transiently, calls `restart_fn(name)`. On success: `status="active"`, `breaker="closed"`, `consecutive_failures=0`, `last_error=None`, `next_probe_at=None`. On failure: `last_error = str(exc)`, `breaker="open"`, `status="quarantined"`, `next_probe_at = now + timedelta(seconds=config.probe_interval_seconds)`. `consecutive_failures` is not changed on probe failure (already at quarantine threshold).
- `EndpointSupervisor.state(name)` → `EndpointState | None`; `all_states()` → `list[EndpointState]`.
- **Full state-machine test coverage** with `FakeClock`: closed→restarting→(backoff timing)→open→half_open→closed; half_open→open.
- **Backoff sequence** test: with `jitter="none"`, `base=2`, `factor=3`, `cap=100`: attempt 1 → 2s; attempt 2 → 6s; attempt 3 → 18s; attempt 4 → 54s; attempt 5 → 100s (capped).
- **Quarantine** occurs after exactly `restarts_before_quarantine` consecutive failed restarts.
- **Probe** scheduled at `now + probe_interval_seconds`; half-open success clears quarantine and resets counters.
- No `sleep`, no `asyncio`, no real I/O in tests — deterministic via `FakeClock.advance()` and a fake restart callable.
- New test file passes `just test-fast` (no `@pytest.mark.slow` needed — all logic is pure Python with no subprocess/network/sleep).

## Approach

No GoF pattern applies here — this is a straightforward state machine. The relevant principle is **DIP (Dependency Inversion)**: `EndpointSupervisor` depends on the `Clock` and `Callable` abstractions already established in the codebase, not on `asyncio` timers or concrete endpoint implementations. This is what makes it independently unit-testable before T4 wires it into the Bus.

### `EndpointState` dataclass (`supervisor.py`)

`@dataclass` (consistent with `EndpointState` conventions in `core.py`). All fields have defaults so `EndpointState(name="x")` gives an active/closed starting state. The `next_probe_at` field serves double duty:
- When `status="restarting"`: earliest time to attempt the next restart.
- When `status="quarantined"`: earliest time to enter HALF_OPEN.

This collapses the "next action time" into one field (matching the struct the issue defines) instead of introducing a second timer field.

### `_compute_restart_backoff` (module-level function, `supervisor.py`)

Mirrors `_compute_delivery_backoff` from T5's design (same formula, same parameter style). `attempt` is 1-indexed:

```python
def _compute_restart_backoff(attempt: int, config: SupervisorConfig) -> float:
    cap = config.restart_backoff_cap_seconds
    raw = config.restart_backoff_base_seconds * (config.restart_backoff_factor ** (attempt - 1))
    computed = min(raw, cap)
    if config.restart_jitter == "none":
        return computed
    # restart_jitter == "full": full jitter within [0, computed]
    return random.uniform(0, computed)
```

`attempt=1` → `base * factor^0 = base`. `attempt=2` → `base * factor^1`. Cap is applied before jitter so jitter never exceeds the cap.

### `EndpointSupervisor` class (`supervisor.py`)

Central registry. `__init__` stores `config`, `clock`, `restart_fn`, and `_states: dict[str, EndpointState]`.

**`record_failure(name, error="")`** is synchronous — only state mutation, no I/O. It transitions from active/closed to restarting, computing the first restart backoff with `attempt=1` (`consecutive_failures=0` at this point, so `attempt = consecutive_failures + 1 = 1`). Calling `record_failure` while already `restarting` or `quarantined` only updates `last_error` — the supervisor is already handling it.

**`tick(now)`** is the sole async entry point. It iterates all states, finds those with `next_probe_at <= now`, and delegates to `_attempt_restart` (for `restarting`) or `_attempt_probe` (for `quarantined`). The iteration is over `list(self._states.values())` to be safe against concurrent modification.

**`_attempt_restart(state, now)`** increments `consecutive_failures` on failure (not before the attempt — a clean first attempt is possible). The quarantine check uses `>=`: with `restarts_before_quarantine=5`, the 5th failed restart (`consecutive_failures == 5`) triggers OPEN. The backoff for the next restart (when < threshold) is `_compute_restart_backoff(state.consecutive_failures + 1, config)` after incrementing — i.e., attempt 2, 3, 4, … for subsequent failures.

**`_attempt_probe(state, now)`** sets `breaker="half_open"` immediately (observable mid-tick if `all_states()` is called from another coroutine, though no test requires this). On probe failure, `consecutive_failures` is NOT changed — the endpoint is already quarantined at the threshold and the probe is only a health check.

### Imports in `supervisor.py`

```python
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Literal

from agent_core.bus.core import SupervisorConfig
from agent_core.clock import Clock
```

No circular imports: `supervisor.py` imports from `core.py` (SupervisorConfig only) and `clock.py`. Neither `core.py` nor `clock.py` imports from `supervisor.py`. T4 will add the reverse dependency in `core.py`.

### Test file (`test_supervisor.py`)

Uses `FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))` and a simple async fake restart callable that records calls and can be configured to raise. No `tmp_path`, no `Bus`, no SQLite — pure in-memory. `asyncio_mode = "auto"` (root `conftest.py`) means `async def test_*` needs no decorator.

Fake restart callable pattern:
```python
class FakeRestart:
    def __init__(self, *, fails: int = 0) -> None:
        self.calls: list[str] = []
        self.fails = fails  # raise on first N calls
    
    async def __call__(self, name: str) -> None:
        self.calls.append(name)
        if self.fails > 0:
            self.fails -= 1
            raise RuntimeError(f"restart failed for {name}")
```

## Sub-requests (topologically sorted)

1. **Create `packages/core/src/agent_core/bus/supervisor.py`** with imports, `EndpointState` dataclass, and `_compute_restart_backoff` function.
2. **Add `EndpointSupervisor` class** with `__init__`, `register`, `record_success`, `state`, `all_states` (no async, no I/O).
3. **Add `record_failure`** to `EndpointSupervisor` — synchronous state transition from active/closed to restarting; no-op (last_error update only) for non-closed states.
4. **Add `tick`, `_attempt_restart`, `_attempt_probe`** to `EndpointSupervisor` — the async methods that call `restart_fn` and drive state transitions.
5. **Write `packages/core/tests/bus/test_supervisor.py`** — full test coverage (see File-level changes for test list).

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/bus/supervisor.py` | **New file**: `EndpointState` dataclass; `_compute_restart_backoff` module-level function; `EndpointSupervisor` class with `register`, `record_failure`, `record_success`, `tick`, `_attempt_restart`, `_attempt_probe`, `state`, `all_states` |
| `packages/core/tests/bus/test_supervisor.py` | **New file**: see test cases below |

No other files change. `supervisor.py` is a leaf module; nothing in the existing bus package imports it yet (T4 will add that import). `core.py` is read-only from this ticket's perspective.

**Test cases in `test_supervisor.py`** (organised by class):

`TestEndpointStateDefaults`:
- `test_default_state_is_active_closed` — `EndpointState(name="x")` has `status="active"`, `breaker="closed"`, `consecutive_failures=0`, `last_error=None`, `next_probe_at=None`.

`TestComputeRestartBackoff`:
- `test_attempt_1_no_jitter` — `jitter="none"`, base=2, factor=3, cap=100, attempt=1 → 2.0s.
- `test_attempt_2_no_jitter` — attempt=2 → 6.0s.
- `test_attempt_5_capped_no_jitter` — attempt=5 → min(100, 2*3^4) = min(100, 162) = 100.0s.
- `test_full_jitter_within_range` — `jitter="full"`, run 200 iterations, all values in `[0, computed]`.

`TestRegister`:
- `test_register_creates_active_closed_state`.
- `test_register_duplicate_raises_value_error`.
- `test_state_returns_none_for_unknown`.

`TestRecordFailure`:
- `test_record_failure_from_closed_transitions_to_restarting` — status becomes "restarting", breaker stays "closed", last_error set.
- `test_record_failure_schedules_restart_at_backoff_from_now` — `next_probe_at` = `clock.now() + backoff(attempt=1)` (with `jitter="none"` for determinism).
- `test_record_failure_when_restarting_updates_last_error_only` — second `record_failure` call while restarting: `next_probe_at` unchanged.
- `test_record_failure_when_quarantined_is_noop` — no state change when status="quarantined".

`TestRecordSuccess`:
- `test_record_success_resets_to_active_closed` — after record_failure, record_success restores all fields.

`TestTickRestarting`:
- `test_tick_before_restart_due_does_not_call_restart_fn` — `FakeClock` at t=0, next_probe_at at t=5; tick at t=2 → `FakeRestart.calls == []`.
- `test_tick_at_restart_due_calls_restart_fn` — tick at t=5 → `FakeRestart.calls == ["x"]`.
- `test_successful_restart_transitions_to_active_closed` — after successful restart: `status="active"`, `breaker="closed"`, `consecutive_failures=0`, `next_probe_at=None`.
- `test_failed_restart_increments_consecutive_failures` — restart fails → `consecutive_failures=1`.
- `test_failed_restart_schedules_next_backoff` — after 1 failure, `next_probe_at` = `now + backoff(attempt=2)`.
- `test_quarantine_after_exactly_n_failed_restarts` — with `restarts_before_quarantine=3` and `jitter="none"`, drive clock through 3 failed restarts: on the 3rd, `status="quarantined"`, `breaker="open"`.
- `test_quarantine_sets_next_probe_at` — `next_probe_at = now + probe_interval_seconds` after quarantine.
- `test_successful_restart_after_partial_failures_resets_counter` — fail twice, succeed on third → `consecutive_failures=0`.

`TestTickQuarantined`:
- `test_tick_before_probe_due_does_not_probe` — quarantined endpoint, tick before `next_probe_at` → `restart_fn` not called.
- `test_tick_at_probe_calls_restart_fn` — tick at `next_probe_at` → restart_fn called once.
- `test_probe_success_transitions_to_active_closed` — success → `status="active"`, `breaker="closed"`, `consecutive_failures=0`.
- `test_probe_failure_returns_to_open_resets_probe_timer` — failure → `status="quarantined"`, `breaker="open"`, `next_probe_at` = `now + probe_interval_seconds`.
- `test_half_open_breaker_set_during_probe_attempt` (optional, best-effort) — if testable: breaker is "half_open" between `_attempt_probe` entry and the restart call; otherwise omit.

`TestFullStateMachine`:
- `test_full_cycle_closed_restarting_open_half_open_closed` — uses `restarts_before_quarantine=2`, `jitter="none"` to drive: `record_failure` → tick → fail → tick → fail → quarantined → advance clock past `probe_interval` → tick → probe succeeds → active/closed.
- `test_full_cycle_closed_restarting_open_half_open_open` — same but probe fails → back to quarantined.

## Alternatives considered

1. **Per-endpoint self-supervising wrapper** — each endpoint wraps a circuit breaker object; no central registry. Ruled out: the design spec explicitly requires a central registry ("not a per-endpoint self-supervising wrapper") so `bus status` and future consumers (tray icon) read all health from one place.
2. **Fold restart timing into a second `next_restart_at` field on `EndpointState`** — separate datetime fields for restart timer (when `restarting`) and probe timer (when `quarantined`). Ruled out: the issue defines exactly six named fields on `EndpointState`; `next_probe_at` is already named and serves as "next action time" in both states without ambiguity (only one of the two states uses it at a time).
3. **Use `asyncio.sleep` + `asyncio.create_task` for backoff timers** — simpler call site, no `tick()` needed. Ruled out: an explicit acceptance criterion requires no real sleeps and determinism via injected clock; `tick(now)` is explicitly named in the issue as the bus-loop-facing API T4 will call.

## Open questions

None. The state-machine fields, transitions, backoff formula, quarantine threshold, probe interval, injected-clock seam, and restart-callable contract are all unambiguous from the issue and design spec. `SupervisorConfig` (T1) is confirmed present in `core.py` at the expected import path.

## Out of scope

- Wiring `EndpointSupervisor` into `Bus.start()`/`stop()` — T4.
- `EndpointStateChanged` event emission — T4.
- Degraded boot (`Bus.start()` surviving per-endpoint failures) — T4.
- `BusHandle.spawn()` tracked-task API — T2.
- Delivery retry backoff (`next_attempt_at` column) — T5 / issue #274.
- ack-vs-nack endpoint fixes — T6 / issue #275.
- OS-level process supervisor — issue #265.
- The `"equal"` jitter mode (recognized by `SupervisorConfig` validation but not implemented here; YAGNI until required).
- Export from `packages/core/src/agent_core/bus/__init__.py` — the module is a leaf; T4 will import it directly.
