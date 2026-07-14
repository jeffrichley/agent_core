# Spec: degraded boot + wire supervisor + state-change event + bus status (issue #273)

## Goal

Wire the `EndpointSupervisor` (T3, issue #272) into the `Bus` lifecycle so that one endpoint's start failure no longer tears down the entire bus, the supervisor drives runtime restart/probe cycles via `tick()`, task-failure signals reach the supervisor, and quarantine/recover transitions are surfaced as structured events and in `bus status`. Implements Supervision T4 as described in `docs/superpowers/specs/2026-07-13-agent-core-supervision-design.md`.

Issue: https://github.com/jeffrichley/agent_core/issues/273

## Acceptance criteria

- `Bus.start()` with N endpoints where one raises during `start()` → bus sets `_started = True`, the failing endpoint has `supervisor.state(name).status == "quarantined"`, all other endpoints have `status == "active"`, and an `on_transition` callback fires with `("quarantined", last_error)`.
- When zero of N endpoints start successfully, bus still sets `_started = True` and logs at CRITICAL.
- On a subsequent successful probe or restart, `supervisor.state(name).status == "active"`, pending mail is drained via `drain_for()`, and the `on_transition` callback fires with `("recovered", None)`.
- `Bus.run_supervisor_tick_once()` method exists; the `_run_bus` CLI loop calls it alongside the redelivery sweep.
- `Bus._make_task_failure_hook()` now calls `self._supervisor.record_failure(endpoint_name, str(exc))` (in addition to logging) so spawned-task failures feed the circuit breaker.
- `bus status` shows a "Degraded Endpoints" Rich table (name + last_error + since) for quarantined endpoints when any exist; the table is absent when all endpoints are healthy.
- `bus status` stops showing a degraded endpoint after recovery (the row is removed from `supervisor_state`).
- `EndpointSupervisor` gains a `quarantine(name, error="")` async method that directly enters the quarantined/open state (bypassing the restarting phase) and fires `on_transition`.
- `EndpointSupervisor.__init__` gains an optional `on_transition: Callable[[str, str, str | None], Awaitable[None]] | None = None` parameter; callbacks fire on quarantine-entry and recovery.
- Existing tests in `test_core_lifecycle.py` still pass, except `test_start_partial_failure_rolls_back` which is renamed and revised to reflect degraded-boot semantics.
- No `asyncio.sleep`, no notification/Discord code, no new YAML config knobs introduced.

## Approach

No GoF pattern applies end-to-end. The relevant principles are **DIP** (dependency-inversion for the restart callable and transition callback, already established in T3) and **SRP**: each piece has one job — the supervisor owns the state machine; the bus owns the lifecycle wiring; the persistence layer owns the SQLite record.

### `EndpointSupervisor` additions (`supervisor.py`)

Two additions needed in T4:

**`on_transition` injection** — add `on_transition: Callable[[str, str, str | None], Awaitable[None]] | None = None` to `EndpointSupervisor.__init__`. Store as `self._on_transition`. Call `await self._on_transition(name, "quarantined", last_error)` at two sites: inside the new `quarantine()` method and inside `_attempt_restart` when the consecutive-failure threshold is reached. Call `await self._on_transition(name, "recovered", None)` at two sites: inside `_attempt_restart` on success and inside `_attempt_probe` on success. Do NOT fire on probe failure (status stays quarantined — no boundary crossing).

**`quarantine(name, error="")` async method** — directly transitions an endpoint to `status="quarantined"`, `breaker="open"`, `last_error=error`, `next_probe_at = clock.now() + timedelta(seconds=config.probe_interval_seconds)`. Calls `on_transition` if set. This bypasses the restarting phase and is used by the bus for boot-time failures where the endpoint is already known-bad.

These additions are purely additive; all T3 tests pass unchanged because `on_transition` defaults to `None` and the new method is unused by existing tests.

### `Persistence` additions (`persistence.py`)

Add a second table `supervisor_state` to `_SCHEMA` (after the existing `envelopes` block):

```sql
CREATE TABLE IF NOT EXISTS supervisor_state (
    name        TEXT PRIMARY KEY,
    last_error  TEXT,
    updated_at  TEXT NOT NULL
);
```

Add three async methods:
- `upsert_supervisor_state(name: str, last_error: str | None) -> None` — `INSERT OR REPLACE INTO supervisor_state` with `updated_at = clock.now().isoformat()`.
- `clear_supervisor_state(name: str) -> None` — `DELETE FROM supervisor_state WHERE name = ?`.
- `list_supervisor_degraded() -> list[dict[str, Any]]` — `SELECT * FROM supervisor_state ORDER BY updated_at ASC`; returns list of dicts with keys `name`, `last_error`, `updated_at`.

Only quarantined endpoints have rows; rows are deleted on recovery. No `status` column needed.

### `Bus` core wiring (`core.py`)

**`Bus.__init__`**: add `self._supervisor: EndpointSupervisor | None = None`.

**`Bus.start()` — degraded boot**: replace the current single `try/except` block that wraps the entire endpoint loop with per-endpoint isolation:

```python
self._supervisor = EndpointSupervisor(
    config=self.config.supervisor,
    clock=self._clock,
    restart_fn=self._restart_endpoint,
    on_transition=self._on_supervisor_transition,
)
started_count = 0
for spec in self._endpoints_by_name.values():
    self._supervisor.register(spec.name)
    handle = BusHandle(
        self,
        spec.name,
        on_task_failure=self._make_task_failure_hook(spec.name),
    )
    self._handles[spec.name] = handle
    try:
        await spec.endpoint.start(handle)
        await self.drain_for(spec.name)
        started_count += 1
    except Exception as exc:
        log.error("endpoint %r failed to start; quarantining: %s", spec.name, exc)
        await self._supervisor.quarantine(spec.name, str(exc))

if started_count == 0 and self._endpoints_by_name:
    log.critical(
        "all %d endpoint(s) failed to start; bus is running degraded-empty",
        len(self._endpoints_by_name),
    )
self._started = True
```

Remove the old `started_specs: list[EndpointSpec]` variable and its rollback logic entirely. The handle is stored in `self._handles[spec.name]` before the start attempt so `_restart_endpoint` can find it later.

**`Bus.stop()`**: add `self._supervisor = None` at the end (after `self._started = False`).

**`Bus._make_task_failure_hook()`**: update the closure body to also call the supervisor:

```python
def _hook(exc: BaseException) -> None:
    log.error(
        "endpoint %r: background task raised (endpoint may need restart)",
        endpoint_name,
        exc_info=exc,
    )
    if self._supervisor is not None:
        self._supervisor.record_failure(endpoint_name, str(exc))
```

**`Bus._restart_endpoint(name: str) -> None`** (new async method): the restart callable injected into `EndpointSupervisor`:

```python
async def _restart_endpoint(self, name: str) -> None:
    spec = self._endpoints_by_name[name]
    handle = self._handles.get(name)
    try:
        await spec.endpoint.stop()
    except Exception:
        log.exception("error stopping endpoint %s during supervisor restart", name)
    if handle is not None:
        await handle._drain_tasks()
    await spec.endpoint.start(handle)   # raises on failure → supervisor catches
    await self.drain_for(name)
```

**`Bus._on_supervisor_transition(name, transition, last_error)` (new async method)**:

```python
async def _on_supervisor_transition(
    self, name: str, transition: str, last_error: str | None
) -> None:
    if transition == "quarantined":
        log.warning("endpoint %r quarantined; last error: %s", name, last_error or "(none)")
        if self._store is not None:
            await self._store.upsert_supervisor_state(name, last_error)
    elif transition == "recovered":
        log.info("endpoint %r recovered", name)
        if self._store is not None:
            await self._store.clear_supervisor_state(name)
```

**`Bus.run_supervisor_tick_once(*, now: datetime | None = None) -> None`** (new async method):

```python
async def run_supervisor_tick_once(self, *, now: datetime | None = None) -> None:
    if self._supervisor is None:
        return
    now = now or self._clock.now()
    await self._supervisor.tick(now)
```

Add a **local import** of `EndpointSupervisor` inside `Bus.start()` (not at the module level), immediately before constructing the supervisor:

```python
from agent_core.bus.supervisor import EndpointSupervisor
```

A top-level import would create a circular dependency: `supervisor.py` already imports `SupervisorConfig` from `core.py` at line 16, so a module-level `from agent_core.bus.supervisor import EndpointSupervisor` in `core.py` would cause `ImportError: cannot import name 'SupervisorConfig' from partially initialized module 'agent_core.bus.core'` at import time. With `from __future__ import annotations` already present in `core.py`, the annotation `self._supervisor: EndpointSupervisor | None = None` is a lazy string at runtime and requires no module-level import.

### `cli.py` changes

**`_run_bus`**: add supervisor tick inside the redelivery loop (same cadence, no new sweep task):

```python
async def _redelivery_loop():
    while not stop_event.is_set():
        try:
            await bus.run_redelivery_sweep_once()
        except Exception:
            log.exception("redelivery sweep failed")
        try:
            await bus.run_supervisor_tick_once()
        except Exception:
            log.exception("supervisor tick failed")
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=bus.config.redelivery_sweep_seconds
            )
        except TimeoutError:
            pass
```

**`_status`**: after the existing aggregate-counts table, query `supervisor_state` and print a "Degraded Endpoints" table only if non-empty:

```python
degraded = await store.list_supervisor_degraded()
if degraded:
    deg_table = Table(title="Degraded Endpoints")
    deg_table.add_column("name")
    deg_table.add_column("last_error")
    deg_table.add_column("since")
    for row in degraded:
        deg_table.add_row(row["name"], row["last_error"] or "", row["updated_at"])
    console.print(deg_table)
```

### Test changes

**`test_core_lifecycle.py`**: rename `test_start_partial_failure_rolls_back` to `test_start_partial_failure_quarantines_and_continues` and revise its assertions — no `pytest.raises`, `bus._started` is `True`, `good.stopped` is `False`, `bus._supervisor.state("boom").status == "quarantined"`, `bus._supervisor.state("good").status == "active"`.

**New `test_core_degraded_boot.py`**: see Sub-requests section for full test list.

**New `test_cli_status_degraded.py`**: see Sub-requests section.

## Sub-requests (topologically sorted)

1. **Add `on_transition` parameter and `quarantine()` method to `EndpointSupervisor`** in `packages/core/src/agent_core/bus/supervisor.py`. Add `Awaitable` to the `collections.abc` import (already present). Store `self._on_transition = on_transition` in `__init__`. Add async `quarantine(self, name: str, error: str = "") -> None`. Add `await self._on_transition(...)` calls at: `quarantine()`, `_attempt_restart` success, `_attempt_restart` threshold hit, `_attempt_probe` success.

2. **Add `supervisor_state` table and three methods to `Persistence`** in `packages/core/src/agent_core/bus/persistence.py`. Add the DDL to `_SCHEMA`. Add `upsert_supervisor_state`, `clear_supervisor_state`, `list_supervisor_degraded`.

3. **Degrade-proof `Bus.start()`** in `packages/core/src/agent_core/bus/core.py`. Add `self._supervisor: EndpointSupervisor | None = None` to `__init__`. Replace the rollback `try/except` block in `start()` with per-endpoint try/except + `await supervisor.quarantine()` on failure. Always set `_started = True`. Log CRITICAL on zero-started.

4. **Add `_restart_endpoint`, `_on_supervisor_transition`, `run_supervisor_tick_once` to `Bus`** in `core.py`. Update `_make_task_failure_hook` to call `self._supervisor.record_failure(...)`. Add `self._supervisor = None` in `stop()`. Do **not** add a top-level `EndpointSupervisor` import — the local import inside `Bus.start()` (SR3) is sufficient; a module-level import would create a circular dependency with `supervisor.py`.

5. **Wire supervisor tick into CLI** in `packages/core/src/agent_core/bus/cli.py`. Add `await bus.run_supervisor_tick_once()` inside `_redelivery_loop`. Add degraded table rendering to `_status`.

6. **Update `test_start_partial_failure_rolls_back`** in `packages/core/tests/bus/test_core_lifecycle.py`. Rename and revise assertions per above.

7. **Write `packages/core/tests/bus/test_core_degraded_boot.py`** — full test coverage for degraded boot and supervisor wiring (see below).

8. **Write `packages/core/tests/bus/test_cli_status_degraded.py`** — `bus status` degraded display.

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/bus/supervisor.py` | Add `on_transition` optional param to `__init__`; add async `quarantine()` method; add `await self._on_transition(...)` calls in `_attempt_restart` (success + threshold) and `_attempt_probe` (success) |
| `packages/core/src/agent_core/bus/persistence.py` | Add `supervisor_state` DDL to `_SCHEMA`; add `upsert_supervisor_state`, `clear_supervisor_state`, `list_supervisor_degraded` async methods |
| `packages/core/src/agent_core/bus/core.py` | `Bus.__init__` adds `_supervisor`; `Bus.start()` replaced rollback logic with per-endpoint quarantine-and-continue (with local `EndpointSupervisor` import at the top of `Bus.start()` — not module-level, to avoid circular import with `supervisor.py`); add `_restart_endpoint`, `_on_supervisor_transition`, `run_supervisor_tick_once`; update `_make_task_failure_hook`; update `stop()` to null out supervisor |
| `packages/core/src/agent_core/bus/cli.py` | Add `run_supervisor_tick_once()` call inside `_redelivery_loop`; add degraded table in `_status` |
| `packages/core/tests/bus/test_core_lifecycle.py` | Rename + revise `test_start_partial_failure_rolls_back` → `test_start_partial_failure_quarantines_and_continues` |
| `packages/core/tests/bus/test_core_degraded_boot.py` | **New file** (see test list below) |
| `packages/core/tests/bus/test_cli_status_degraded.py` | **New file** |

No other files change. `supervisor.py` already imports `Awaitable` from `collections.abc` — check before adding.

### Test cases in `test_core_degraded_boot.py`

Fake endpoint helpers:

```python
class _OkEndpoint:
    def __init__(self, name): self.name = name; self.started = False; self.stopped = False; self.delivered = []
    async def start(self, handle): self.started = True
    async def deliver(self, env): self.delivered.append(env)
    async def stop(self): self.stopped = True

class _FailOnStartEndpoint:
    def __init__(self, name): self.name = name; self._start_count = 0; self.started = False
    async def start(self, handle):
        self._start_count += 1
        if self._start_count == 1:
            raise RuntimeError(f"{self.name}: start failed")
        self.started = True  # succeeds on 2nd call (restart)
    async def deliver(self, env): pass
    async def stop(self): pass
```

`TestDegradedBoot`:
- `test_bus_starts_when_endpoint_fails` — register one `_FailOnStartEndpoint` and one `_OkEndpoint`; `await bus.start()` completes without exception; `bus._started is True`.
- `test_failing_endpoint_is_quarantined` — same setup; `bus._supervisor.state("fail").status == "quarantined"`.
- `test_healthy_endpoint_is_active` — `bus._supervisor.state("ok").status == "active"`.
- `test_good_endpoint_not_rolled_back` — `ok_ep.stopped is False` after start.
- `test_transition_callback_fires_on_quarantine` — inject spy async callback via `on_transition` on supervisor (assert not possible via Bus API; instead: read `supervisor_state` from store after start via `bus._store.list_supervisor_degraded()`).
- `test_zero_started_logs_critical` — all endpoints fail; start completes; `bus._started is True`; `caplog` contains a CRITICAL message.
- `test_bus_with_no_endpoints_starts_cleanly` — no endpoints registered, `bus.start()` sets `_started = True`.

`TestSupervisorWiring`:
- `test_task_failure_feeds_supervisor` — start bus with `_OkEndpoint`; endpoint's handle spawns a task that raises; `await asyncio.sleep(0)` to let callback fire; `bus._supervisor.state("ok").status == "restarting"`.
- `test_supervisor_tick_restarts_restarting_endpoint` — drive a `_FailOnStartEndpoint` to restarting via task failure hook (or direct `record_failure`); advance `FakeClock` past restart backoff; call `await bus.run_supervisor_tick_once(now=clock.now())`; endpoint's `_start_count == 2` (restart attempted).
- `test_restart_success_returns_to_active` — same but endpoint succeeds on restart; `supervisor.state("ok").status == "active"`.
- `test_restart_drains_pending_mail` — insert a pending envelope for the endpoint before restart; after successful restart, envelope is dispatched (check `_OkEndpoint.delivered`).
- `test_recovered_event_persisted` — after recovery, `bus._store.list_supervisor_degraded()` returns empty.
- `test_quarantine_persisted_to_store` — after degraded boot, `bus._store.list_supervisor_degraded()` returns entry with `name == "fail"` and non-empty `last_error`.

`TestRunSupervisorTickOnce`:
- `test_tick_noop_before_supervisor_created` — call `await bus.run_supervisor_tick_once()` on an unstarted bus (no exception raised; supervisor is None).

### Test cases in `test_cli_status_degraded.py`

Uses same `_write_config` and `CliRunner` pattern as `test_cli_status.py`. Seed the `supervisor_state` table directly via `Persistence` before invoking `bus status`.

- `test_status_shows_degraded_table_when_quarantined` — seed `supervisor_state` with one quarantined entry; invoke `bus status`; assert `"Degraded"` and the endpoint name appear in output.
- `test_status_no_degraded_table_when_healthy` — no entries in `supervisor_state`; invoke `bus status`; assert `"Degraded"` does NOT appear in output.

## Alternatives considered

1. **All-or-nothing boot (current behavior)**: keep the existing outer `try/except` that tears down all endpoints and raises. Ruled out: directly contradicts the issue's central requirement; the 2026-07-09 outage class originated here.
2. **Call `record_failure()` at boot (→ "restarting") instead of `quarantine()` directly**: at boot time, call `supervisor.record_failure(name, exc)` which transitions to "restarting" rather than immediately quarantining. Ruled out: the acceptance criterion requires `status == "quarantined"` immediately after `bus.start()` returns for a failing endpoint. A "restarting" state would require additional tick cycles to reach quarantine, leaving the endpoint in an ambiguous state during the start-up window.
3. **Separate supervisor sweep loop with its own config interval**: add a `supervisor_sweep_seconds` knob to `BusConfig` / `SupervisorConfig` and a third sweep task in `_run_bus`. Ruled out: T1 (issue #270) closed the config-knob set; adding a new field is out of T4's scope. The redelivery sweep interval (10s default) is adequate for supervisor ticks; piggy-backing costs zero new state.
4. **Publish `EndpointStateChanged` as a bus `Envelope` to a registered sink endpoint**: route transition events through the bus envelope pipeline to a dedicated system endpoint (e.g., `"__bus_events__"`). Ruled out: no subscriber exists yet (the tray-icon endpoint is a separate future ticket); the bus raises `ValueError` for unknown recipients, so we'd need conditional routing. The `supervisor_state` SQLite table provides the same durable, queryable record without coupling to any endpoint registration order.

## Open questions

None. The degraded-boot flow, the supervisor callback signature, the persistence schema, and the CLI changes are all fully determined by the issue requirements, the existing codebase, and the T1–T3 precedents.

## Out of scope

- Delivery retry backoff (`next_attempt_at` column, `deliver_failures_before_breaker`) — T5 / issue #274.
- ack-vs-nack fixes in inbound and discord endpoints — T6 / issue #275.
- OS-level process supervisor ("restart whole process on death") — issue #265.
- Tray-icon / human-visible surfacing — issue #265 and consumers of `supervisor_state`.
- Heartbeat / liveness detection of silently-wedged endpoints — Theme E / issue #268.
- Adding `supervisor:` YAML examples to any documentation files.
- Any new YAML config knobs beyond what T1 already defined.
- Exporting `EndpointSupervisor` from `packages/core/src/agent_core/bus/__init__.py` (not needed — imported directly by `core.py`).
