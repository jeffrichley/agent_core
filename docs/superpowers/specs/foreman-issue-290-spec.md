# Spec: `BusHandle.spawn()` tracked-task API (issue #290)

## Goal

Add a `spawn()` helper to `BusHandle` that creates, tracks, and monitors background asyncio tasks on a per-endpoint basis. The bus cancels and awaits all tracked tasks when it stops an endpoint, eliminating the task-leak class. An unhandled exception in a spawned task invokes a configurable failure hook (a logging closure for this ticket; T3/T4 wires it into `EndpointSupervisor`). This is the **API half** only — call-site migration of existing leaky `create_task` calls is #291.

Issue: https://github.com/jeffrichley/agent_core/issues/290  
Design spec: `docs/superpowers/specs/2026-07-13-agent-core-supervision-design.md`  
Prior combined-API spec: `docs/superpowers/specs/foreman-issue-271-spec.md`

## Acceptance criteria

- `BusHandle.spawn(coro, *, name=None) -> asyncio.Task` exists; tasks registered in a per-endpoint set are cancelled and awaited on endpoint `stop()` — no leak past `stop()`.
- An unhandled exception in a spawned task invokes the failure hook exactly once and does not crash the event loop.
- `CancelledError` on shutdown is not routed to the failure hook.
- Unit tests cover:
  - spawn + complete: task is removed from the internal set after normal completion.
  - spawn + raise: failure hook is fired exactly once; event loop is unaffected.
  - spawn + stop (via `_drain_tasks()`): task is cancelled and hook is **not** fired.
- All new tests pass `just test-fast` (no subprocess / network / sleep → no `@pytest.mark.slow`).

## Approach

No GoF pattern fits cleanly. The engineering principle is **DIP (Dependency Inversion)**: the failure callback is injected into `BusHandle` at construction time by the bus so T3's `EndpointSupervisor` can substitute its own hook later without changing `BusHandle`. For this ticket the bus provides a logging closure.

The prior combined spec (`foreman-issue-271-spec.md`) covered API + migration together; this ticket implements that spec's sub-requests 1 and 2 (the API additions to `handle.py` and `core.py`). Sub-requests 3–5 (leaky site migrations) belong to #291.

### `BusHandle` changes (`packages/core/src/agent_core/bus/handle.py`)

**New imports** (add to the top of the file, below `from __future__ import annotations`):

```python
import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any
```

Add module-level logger (after the imports):

```python
log = logging.getLogger(__name__)
```

**Updated `__init__`** — add optional `on_task_failure` parameter (defaults to `None`, which selects `_default_failure`; all existing call sites that omit it compile unchanged):

```python
def __init__(
    self,
    bus: Bus,
    endpoint_name: str,
    on_task_failure: Callable[[BaseException], None] | None = None,
) -> None:
    self._bus = bus
    self._endpoint_name = endpoint_name
    self._tasks: set[asyncio.Task] = set()
    self._on_task_failure = on_task_failure if on_task_failure is not None else self._default_failure
```

**`spawn()`** — creates the task, registers it, attaches the done callback:

```python
def spawn(
    self,
    coro: Coroutine[Any, Any, None],
    *,
    name: str | None = None,
) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    self._tasks.add(task)
    task.add_done_callback(self._task_done)
    return task
```

**`_task_done()`** — synchronous done callback, removes task, routes non-cancellation exceptions to hook:

```python
def _task_done(self, task: asyncio.Task) -> None:
    self._tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        self._on_task_failure(exc)
```

Note: `task.cancelled()` is checked before `task.exception()` because calling `.exception()` on a cancelled task raises `CancelledError`. This is the correct guard order.

**`_default_failure()`** — T2 logging hook (T3 replaces by injecting a supervisor callback):

```python
def _default_failure(self, exc: BaseException) -> None:
    log.error(
        "endpoint %r: background task raised (endpoint may need restart)",
        self._endpoint_name,
        exc_info=exc,
    )
```

**`_drain_tasks()`** — bus-facing method called from `Bus.stop()` (and the start-rollback path); cancels + awaits all outstanding tasks:

```python
async def _drain_tasks(self) -> None:
    tasks = list(self._tasks)
    for t in tasks:
        if not t.done():
            t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    self._tasks.clear()
```

`return_exceptions=True` prevents a cancelled task's `CancelledError` from bubbling out of `gather`.

### `Bus` changes (`packages/core/src/agent_core/bus/core.py`)

**New import** — add `Callable` to a new `collections.abc` import line (no such import exists currently):

```python
from collections.abc import Callable
```

**`Bus.__init__`** — add handle registry:

```python
self._handles: dict[str, BusHandle] = {}
```

**`_make_task_failure_hook()`** — factory that produces the per-endpoint logging hook (T3/T4 replaces the body with a supervisor call without changing `BusHandle`):

```python
def _make_task_failure_hook(self, endpoint_name: str) -> Callable[[BaseException], None]:
    def _hook(exc: BaseException) -> None:
        log.error(
            "endpoint %r: background task raised (endpoint may need restart)",
            endpoint_name,
            exc_info=exc,
        )
    return _hook
```

**`Bus.start()`** — replace the local `handle = BusHandle(self, spec.name)` construction with one that injects the hook and stores the handle:

```python
handle = BusHandle(
    self,
    spec.name,
    on_task_failure=self._make_task_failure_hook(spec.name),
)
self._handles[spec.name] = handle
```

**`Bus.stop()`** — drain each endpoint's handle tasks immediately after stopping the endpoint (reverse order preserved):

```python
for spec in reversed(list(self._endpoints_by_name.values())):
    try:
        await spec.endpoint.stop()
    except Exception:
        log.exception("error stopping endpoint %s", spec.name)
    handle = self._handles.get(spec.name)
    if handle is not None:
        await handle._drain_tasks()
```

**`Bus.start()` rollback path** (the `except Exception` block that tears down already-started specs on partial-start failure) — also drain handles:

```python
for spec in reversed(started_specs):
    try:
        await spec.endpoint.stop()
    except Exception:
        log.exception("error stopping endpoint %s during failed start", spec.name)
    handle = self._handles.get(spec.name)
    if handle is not None:
        await handle._drain_tasks()
```

The rest of the rollback block (`await self._store.close()`, `self._store = None`, `raise`) is unchanged.

### Tests (`packages/core/tests/bus/test_handle.py`)

Add `import asyncio` at the top. Add class `TestBusHandleSpawn` reusing the existing `_RecordingBus` fake.

Key test correctness note: `asyncio.Task.add_done_callback` schedules callbacks via `loop.call_soon`. This means the callback fires in a future event loop iteration, not synchronously when the task completes. Using `await asyncio.gather(task, return_exceptions=True)` causes the event loop to process the task step first and then our `_task_done` callback (registered before `gather`'s internal callback, so it fires first), allowing `gather` to resolve only after our callback has run. This ordering is reliable because Python's `asyncio.Future.__schedule_callbacks` iterates callbacks in registration order.

```python
import asyncio

class TestBusHandleSpawn:
    async def test_spawn_complete_removes_from_task_set(self):
        """Completed task is removed from the internal tracked set."""
        handle = BusHandle(_RecordingBus(), "x")

        async def _noop() -> None:
            pass

        task = handle.spawn(_noop())
        await asyncio.gather(task, return_exceptions=True)
        assert task not in handle._tasks

    async def test_spawn_raise_invokes_failure_hook_exactly_once(self):
        """Task raising invokes the injected hook exactly once."""
        failures: list[BaseException] = []
        handle = BusHandle(_RecordingBus(), "x", on_task_failure=failures.append)

        async def _boom() -> None:
            raise ValueError("oops")

        task = handle.spawn(_boom())
        await asyncio.gather(task, return_exceptions=True)
        assert len(failures) == 1
        assert isinstance(failures[0], ValueError)

    async def test_spawn_raise_does_not_propagate_to_loop(self):
        """Exception in a spawned task does NOT bubble to the event loop."""
        handle = BusHandle(_RecordingBus(), "x", on_task_failure=lambda _: None)

        async def _boom() -> None:
            raise RuntimeError("contained")

        task = handle.spawn(_boom())
        await asyncio.gather(task, return_exceptions=True)
        assert task.done() and not task.cancelled()

    async def test_drain_cancels_task_and_hook_not_fired(self):
        """CancelledError on shutdown is not treated as a failure."""
        failures: list[BaseException] = []
        handle = BusHandle(_RecordingBus(), "x", on_task_failure=failures.append)

        async def _hang() -> None:
            await asyncio.sleep(1000)

        task = handle.spawn(_hang())
        await handle._drain_tasks()
        assert task.cancelled()
        assert failures == []

    async def test_drain_cancels_all_pending_tasks_and_clears_set(self):
        """_drain_tasks() cancels every outstanding task and empties the set."""
        handle = BusHandle(_RecordingBus(), "x")
        started = asyncio.Event()

        async def _long() -> None:
            started.set()
            await asyncio.sleep(1000)

        t1 = handle.spawn(_long())
        t2 = handle.spawn(_long())
        await started.wait()
        await handle._drain_tasks()
        assert t1.cancelled()
        assert t2.cancelled()
        assert len(handle._tasks) == 0
```

## Sub-requests (topologically sorted)

1. **Add `asyncio`, `log`, `Callable`/`Coroutine` imports; add `on_task_failure` to `BusHandle.__init__`; add `_tasks`, `spawn()`, `_task_done()`, `_default_failure()`, `_drain_tasks()`** — in `packages/core/src/agent_core/bus/handle.py`. No other files change in this step.
2. **Add `Callable` import, `_handles` dict, `_make_task_failure_hook()` to `Bus`; update `Bus.start()` to store handles and inject hook; update `Bus.stop()` and start-rollback to drain handles** — in `packages/core/src/agent_core/bus/core.py`.
3. **Write unit tests** — add `import asyncio` and `TestBusHandleSpawn` to `packages/core/tests/bus/test_handle.py`.

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/bus/handle.py` | Add `asyncio` / `logging` / `Callable` / `Coroutine` imports; module-level `log`; `on_task_failure` parameter to `__init__`; `_tasks` set; `spawn()`, `_task_done()`, `_default_failure()`, `_drain_tasks()` methods |
| `packages/core/src/agent_core/bus/core.py` | Add `from collections.abc import Callable`; add `_handles` dict to `Bus.__init__`; add `_make_task_failure_hook()` factory; update `Bus.start()` to construct `BusHandle` with hook and store it; update `Bus.stop()` and start-rollback `except` block to call `handle._drain_tasks()` |
| `packages/core/tests/bus/test_handle.py` | Add `import asyncio`; add `TestBusHandleSpawn` with 5 async test methods |

No other files change. The `on_task_failure` parameter defaults to `None` (selects `_default_failure`), so all existing `BusHandle(bus, name)` construction sites compile unchanged.

## Alternatives considered

1. **Endpoint-owned cancellation** — each endpoint's `stop()` calls a `handle.drain()` method explicitly. Ruled out: bus-owned drain is more defensive; future endpoints (and any undiscovered leaky sites picked up by #291) benefit automatically without each endpoint's `stop()` needing to remember the call.
2. **`anyio.TaskGroup` for structured concurrency** — provides nursery semantics where the parent waits for all children. Ruled out: `anyio` is not a current dependency of `packages/core`; adding it for this feature alone is scope creep; asyncio's `create_task` + `gather` provides identical semantics with zero new dependencies.
3. **No failure hook injection for T2; just log in `_drain_tasks`** — simpler for now. Ruled out: the design spec explicitly names the failure callback as the seam T3/T4 wires into `EndpointSupervisor`. Adding it as an injected parameter now costs one optional constructor kwarg; omitting it would require a second breaking change to `BusHandle.__init__` at T3.

## Open questions

None. The API shape is fully specified by the issue (`spawn(coro, *, name=...) -> asyncio.Task`, done-callback semantics, `CancelledError` exemption). Both changed files (`handle.py`, `core.py`) were read and the exact injection points are verified. The `on_task_failure` default preserves backward compatibility with all existing `BusHandle` construction sites.

## Out of scope

- Call-site migration of existing leaky `asyncio.create_task()` calls (inbound, voice, discord endpoints) — that is #291.
- `EndpointSupervisor` wiring into `_make_task_failure_hook` — T3/T4 replaces the body of that factory without touching `BusHandle`.
- Migrating `HandoffJobsEndpoint._worker_task` or `ClaudeCodeMCPEndpoint._missing_ack_tasks` — those already track and cancel correctly and are not leaky sites.
- Degraded boot, delivery retry backoff (`next_attempt_at`), ack-vs-nack endpoint fixes — later T4/T5/T6 tickets.
- OS-level process supervisor — issue #265.
