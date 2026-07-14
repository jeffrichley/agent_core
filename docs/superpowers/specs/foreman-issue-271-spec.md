# Spec: `BusHandle.spawn()` tracked-task API + migrate leaky sites (issue #271)

## Goal

Add a `spawn()` method to `BusHandle` that creates, tracks, and supervises background asyncio tasks on a per-endpoint basis. Migrate the three named leaky call-sites (inbound `_bus_publish_adapter`, voice synthesis, discord typing/reaction tasks) from bare `asyncio.create_task()` to `spawn()`. The bus cancels and awaits tracked tasks when it stops an endpoint.

Issue: https://github.com/jeffrichley/agent_core/issues/271  
Design spec: `docs/superpowers/specs/2026-07-13-agent-core-supervision-design.md`

## Acceptance criteria

- `BusHandle.spawn(coro, *, name=None) -> asyncio.Task` exists; registered tasks are cancelled and awaited when the bus stops the endpoint (no leak past `stop()`).
- An unhandled exception in a spawned task invokes the failure hook exactly once and does not crash the event loop.
- `CancelledError` on shutdown is not routed to the failure hook.
- The three named leaky sites use `spawn()` — no bare `asyncio.create_task()` remaining at those lines:
  - `packages/agent-core-inbound/src/agent_core_inbound/endpoint.py:212` — `_bus_publish_adapter`
  - `packages/agent-core-voice/src/agent_core_voice/endpoint.py:441` — synthesis task
  - `packages/agent-core-discord/src/agent_core_discord/endpoint.py:1254, 1468, 1511` — typing-while-pending, LRU-evict ack removal, TTL-evict ack removal
- Unit tests cover: spawn+complete (task removed from set), spawn+raise (hook fired exactly once, loop unaffected), spawn+stop (task cancelled, hook NOT fired).

## Approach

No GoF pattern fits cleanly. The engineering principle is DIP: the failure signal callback is injected into `BusHandle` at construction time by the bus, so T3's `EndpointSupervisor` can substitute its own hook without touching `BusHandle`. For T2 the bus provides a logging closure.

### `BusHandle` changes (`packages/core/src/agent_core/bus/handle.py`)

Add three new members to `BusHandle.__init__`:

```python
import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

self._tasks: set[asyncio.Task] = set()
self._on_task_failure: Callable[[BaseException], None]
```

Add an `on_task_failure` keyword parameter (default `None`; if `None`, the handle uses its own `_default_failure` logging method so callers that don't need injection still work):

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

`spawn()` creates the task, registers it, attaches the done callback:

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

Done callback — removes task from set; routes non-cancellation exceptions to the hook:

```python
def _task_done(self, task: asyncio.Task) -> None:
    self._tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        self._on_task_failure(exc)
```

Default (T2) failure hook — logs the exception:

```python
def _default_failure(self, exc: BaseException) -> None:
    log.error(
        "endpoint %r: background task raised (endpoint may need restart)",
        self._endpoint_name,
        exc_info=exc,
    )
```

Bus-facing drain method — cancel + await all tracked tasks (called by `Bus.stop()` after `endpoint.stop()`):

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

`asyncio` and `log = logging.getLogger(__name__)` must be added to `handle.py`'s imports.

### `Bus` changes (`packages/core/src/agent_core/bus/core.py`)

Add to `Bus.__init__`:

```python
self._handles: dict[str, BusHandle] = {}
```

In `Bus.start()`, replace `handle = BusHandle(self, spec.name)` with:

```python
handle = BusHandle(
    self,
    spec.name,
    on_task_failure=self._make_task_failure_hook(spec.name),
)
self._handles[spec.name] = handle
```

Add the factory method on `Bus`:

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

`Callable` must be added to the imports from `collections.abc`.

In `Bus.stop()`, after each `await spec.endpoint.stop()` call, drain that endpoint's handle tasks:

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

Also drain handles in the `Bus.start()` rollback path (already-started endpoints being torn down on partial start failure):

```python
except Exception:
    for spec in reversed(started_specs):
        try:
            await spec.endpoint.stop()
        except Exception:
            log.exception(...)
        handle = self._handles.get(spec.name)
        if handle is not None:
            await handle._drain_tasks()
    await self._store.close()
    ...
```

### Leaky site migrations

**`packages/agent-core-inbound/src/agent_core_inbound/endpoint.py:212`**

`_bus_publish_adapter` is a sync method called from a FastAPI async handler. Replace:
```python
asyncio.create_task(self._handle.publish(envelope))
```
with:
```python
self._handle.spawn(self._handle.publish(envelope), name="inbound-bus-publish")
```
Remove the now-unused `asyncio.create_task` call; `asyncio` may still be imported for `self._serve_task`.

**`packages/agent-core-voice/src/agent_core_voice/endpoint.py:441`**

Replace:
```python
asyncio.create_task(self._handle_synthesis_request(envelope, req))
```
with:
```python
self._handle.spawn(
    self._handle_synthesis_request(envelope, req),
    name=f"voice-synthesis-{envelope.id}",
)
```

**`packages/agent-core-discord/src/agent_core_discord/endpoint.py:1254`** (inside `on_message`, called from `_make_on_message_handler`):

Replace:
```python
asyncio.create_task(
    self._typing_while_pending(message.channel, mid),
    name=f"discord-{self.name}-typing-{mid}",
)
```
with:
```python
self._handle.spawn(
    self._typing_while_pending(message.channel, mid),
    name=f"discord-{self.name}-typing-{mid}",
)
```

**`packages/agent-core-discord/src/agent_core_discord/endpoint.py:1468`** (inside `_track_pending_ack`):

Replace:
```python
asyncio.create_task(
    self._remote_remove_ack(old_id, old_emoji, old_ch),
    name=f"discord-endpoint-{self.name}-evict-ack",
)
```
with:
```python
self._handle.spawn(
    self._remote_remove_ack(old_id, old_emoji, old_ch),
    name=f"discord-endpoint-{self.name}-evict-ack",
)
```

**`packages/agent-core-discord/src/agent_core_discord/endpoint.py:1511`** (inside `_sweep_pending_acks_once`):

Replace:
```python
asyncio.create_task(
    self._remote_remove_ack(head_id, emoji, channel_id),
    name=f"discord-endpoint-{self.name}-ttl-ack",
)
```
with:
```python
self._handle.spawn(
    self._remote_remove_ack(head_id, emoji, channel_id),
    name=f"discord-endpoint-{self.name}-ttl-ack",
)
```

Both `_track_pending_ack` and `_sweep_pending_acks_once` are sync methods. `self._handle` is always set at their call sites: they are invoked only from `deliver()` (which guards on `self._handle is None`) and from `_pending_acks_sweep_loop()` (which only runs after `start()`), so the null-handle case cannot arise.

### Tests (`packages/core/tests/bus/test_handle.py`)

Add class `TestBusHandleSpawn` reusing the existing `_RecordingBus` fake.

```python
import asyncio
import pytest
from agent_core.bus.handle import BusHandle

class TestBusHandleSpawn:
    async def test_spawn_complete_removes_from_task_set(self):
        """Completed task is removed from the internal set."""
        handle = BusHandle(_RecordingBus(), "x")
        async def _noop():
            pass
        task = handle.spawn(_noop())
        await task
        assert task not in handle._tasks

    async def test_spawn_raise_invokes_failure_hook_exactly_once(self):
        """Task raising invokes the hook exactly once; loop keeps running."""
        failures: list[BaseException] = []
        handle = BusHandle(_RecordingBus(), "x", on_task_failure=failures.append)
        async def _boom():
            raise ValueError("oops")
        task = handle.spawn(_boom())
        await asyncio.sleep(0)   # yield so the task runs
        assert len(failures) == 1
        assert isinstance(failures[0], ValueError)

    async def test_spawn_raise_does_not_propagate_to_loop(self):
        """An exception in a spawned task does NOT bubble out of the event loop."""
        handle = BusHandle(_RecordingBus(), "x", on_task_failure=lambda _: None)
        async def _boom():
            raise RuntimeError("contained")
        task = handle.spawn(_boom())
        await asyncio.sleep(0)
        assert task.done() and not task.cancelled()

    async def test_spawn_cancelled_on_drain_does_not_invoke_hook(self):
        """CancelledError on shutdown is not treated as a failure."""
        failures: list[BaseException] = []
        handle = BusHandle(_RecordingBus(), "x", on_task_failure=failures.append)
        async def _hang():
            await asyncio.sleep(1000)
        task = handle.spawn(_hang())
        await handle._drain_tasks()
        assert task.cancelled()
        assert failures == []

    async def test_drain_cancels_all_pending_tasks(self):
        """_drain_tasks() cancels every outstanding task."""
        handle = BusHandle(_RecordingBus(), "x")
        started = asyncio.Event()
        async def _long():
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

1. **Add `asyncio`, `log`, `Callable`/`Coroutine` imports and `spawn()` + `_task_done()` + `_default_failure()` + `_drain_tasks()` to `BusHandle`** in `packages/core/src/agent_core/bus/handle.py`. Add `on_task_failure` parameter to `__init__`. No other files change in this step.
2. **Add `_handles` dict and `_make_task_failure_hook()` to `Bus`** in `packages/core/src/agent_core/bus/core.py`. Update `Bus.start()` to store handles and inject the hook. Update `Bus.stop()` and the start-rollback block to drain handles. Add `Callable` to `collections.abc` import.
3. **Migrate `InboundEndpoint._bus_publish_adapter`** in `packages/agent-core-inbound/src/agent_core_inbound/endpoint.py:212` — replace bare `asyncio.create_task` with `self._handle.spawn()`.
4. **Migrate `VoiceEndpoint.deliver`** in `packages/agent-core-voice/src/agent_core_voice/endpoint.py:441` — replace bare `asyncio.create_task` with `self._handle.spawn()`.
5. **Migrate three discord leaky sites** in `packages/agent-core-discord/src/agent_core_discord/endpoint.py:1254, 1468, 1511` — replace bare `asyncio.create_task` calls with `self._handle.spawn()`.
6. **Write unit tests** — add `TestBusHandleSpawn` to `packages/core/tests/bus/test_handle.py`.

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/bus/handle.py` | Add `asyncio`/`log`/`Callable`/`Coroutine` imports; add `on_task_failure` to `__init__`; add `_tasks` set, `spawn()`, `_task_done()`, `_default_failure()`, `_drain_tasks()` |
| `packages/core/src/agent_core/bus/core.py` | Add `Callable` to `collections.abc` import; add `_handles` dict to `Bus.__init__`; add `_make_task_failure_hook()` factory; update `Bus.start()` to store handles + inject hook; update `Bus.stop()` and start-rollback to call `handle._drain_tasks()` |
| `packages/agent-core-inbound/src/agent_core_inbound/endpoint.py` | Line 212: `asyncio.create_task(...)` → `self._handle.spawn(...)` |
| `packages/agent-core-voice/src/agent_core_voice/endpoint.py` | Line 441: `asyncio.create_task(...)` → `self._handle.spawn(...)` |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | Lines 1254, 1468, 1511: `asyncio.create_task(...)` → `self._handle.spawn(...)` |
| `packages/core/tests/bus/test_handle.py` | Add `TestBusHandleSpawn` with 5 async test methods |

No other files change. The `on_task_failure` parameter defaults to `None` (triggers `_default_failure`), so all existing `BusHandle(bus, name)` construction sites in tests compile unchanged.

## Alternatives considered

1. **Endpoint-owned cancellation: each endpoint's `stop()` calls `handle.stop_tasks()`** — would require touching every endpoint's `stop()` individually, and future endpoints (or undiscovered leaky sites) would need to remember to do so. Ruled out: bus-owned drain is more defensive — the bus always drains, regardless of what the endpoint's `stop()` does.
2. **anyio `TaskGroup` for structured concurrency** — provides nursery semantics (parent waits for children). Ruled out: `anyio` is not a current dependency of `packages/core`; adding it for this alone is scope creep; asyncio's `create_task` + `gather` provides the same semantics with zero new dependencies.
3. **No failure hook for T2; just log in `_drain_tasks`** — simpler. Ruled out: the design spec explicitly calls out the failure hook as the seam T3/T4 wires into the supervisor. Adding it now costs one parameter; omitting it would require a second interface break at T3.

## Open questions

None. The API shape is specified by the issue (`spawn(coro, *, name=...) -> asyncio.Task`, done-callback semantics, `CancelledError` exemption). The three leaky sites are named and their exact line numbers are verified against the live code. The test cases are enumerated in the AC.

## Out of scope

- `EndpointSupervisor` state machine (T3) — T3 will replace `_make_task_failure_hook`'s body with a supervisor call without touching `BusHandle`.
- Degraded boot (T4).
- Delivery retry backoff and `next_attempt_at` (T5).
- ack-vs-nack fixes in inbound/discord (T6).
- Migrating `handoff_jobs._worker_task` or `claude_code_mcp._missing_ack_tasks` — those already track and cancel correctly; `spawn()` is for the leaky fire-and-forget sites, not for replacing every task-management pattern.
- Migrating `discord._typing_tasks` (the `send_typing` tool at line 2222) — already tracked in a named set and cancelled in `stop()`; it is not a leaky site.
- Adding `supervisor:` YAML keys or documentation examples.
