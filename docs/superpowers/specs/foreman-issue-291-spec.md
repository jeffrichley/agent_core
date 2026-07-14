# Spec: migrate leaky `asyncio.create_task` sites to `BusHandle.spawn()` (issue #291)

## Goal

Replace three endpoint call-sites that spawn background work with bare `asyncio.create_task()`
with `BusHandle.spawn()` (landed in #290), eliminating task leaks past `stop()` and ensuring
unhandled exceptions reach the failure hook. See issue #291 and the design spec at
`docs/superpowers/specs/2026-07-13-agent-core-supervision-design.md`.

## Acceptance criteria

- All three named sites use `spawn()`; no bare `asyncio.create_task` remains at:
  - `packages/agent-core-inbound/src/agent_core_inbound/endpoint.py:212` (`_bus_publish_adapter`)
  - `packages/agent-core-voice/src/agent_core_voice/endpoint.py:441` (synthesis task in `deliver`)
  - `packages/agent-core-discord/src/agent_core_discord/endpoint.py:1254, 1468, 1511`
    (typing-while-pending, LRU-evict-ack removal, TTL-evict-ack removal)
- Existing endpoint tests still pass with no behavioral change on the happy path.
- `FakeBusHandle` in `packages/agent-core-discord/src/agent_core_discord/testing/fakes.py`
  and the inline `_Recording` classes in `test_endpoint_inbound.py` and
  `test_endpoint_pending_acks.py` each gain a `spawn()` method that delegates to
  `asyncio.create_task`, so tests that fire `on_message` or call `_track_pending_ack` /
  `_sweep_pending_acks_once` continue to compile and pass.
- Three new lifecycle tests (one per migrated site) assert that the spawned task is cancelled
  when the bus drains tasks on stop (i.e., `handle._drain_tasks()` cancels any outstanding task
  spawned at that site).
- All new tests pass `just test-fast`; none require subprocess/network/sleep → no
  `@pytest.mark.slow`.

## Approach

No GoF pattern applies. The engineering principle is **SRP**: the bus already owns task
lifetime via `BusHandle._drain_tasks()` (landed in #290); the endpoints' job is to opt in by
calling `spawn()` instead of `create_task()`. This is a pure call-site migration — no
architecture changes.

### What `BusHandle.spawn()` already does (`packages/core/src/agent_core/bus/handle.py`)

`spawn(coro, *, name=None)` (line 80) creates an asyncio task, registers it in
`self._tasks`, attaches `_task_done` as a done callback. `_task_done` removes the task from
the set; if it raised (non-`CancelledError`) it calls `self._on_task_failure`. `Bus.stop()`
(line 257) calls `handle._drain_tasks()` for each endpoint's handle after calling
`endpoint.stop()`, which cancels and gathers all outstanding tasks. `CancelledError` is never
routed to the failure hook. All of this is confirmed live in the repo.

### Site 1 — inbound `_bus_publish_adapter`

`packages/agent-core-inbound/src/agent_core_inbound/endpoint.py:212`:

```python
# Before
asyncio.create_task(self._handle.publish(envelope))

# After
self._handle.spawn(self._handle.publish(envelope), name="inbound-bus-publish")
```

`asyncio` remains imported (still used for `asyncio.Task | None`, `asyncio.create_task` in
`start()` for the uvicorn serve task, and `asyncio.wait_for` in `stop()`). Only this one
call-site changes.

### Site 2 — voice synthesis task

`packages/agent-core-voice/src/agent_core_voice/endpoint.py:441`:

```python
# Before
asyncio.create_task(self._handle_synthesis_request(envelope, req))

# After
self._handle.spawn(
    self._handle_synthesis_request(envelope, req),
    name=f"voice-synthesis-{envelope.id}",
)
```

`asyncio` remains imported (`asyncio.wait_for` and `asyncio.to_thread` inside
`_handle_synthesis_request`).

### Sites 3, 4, 5 — discord typing and evict-ack tasks

`packages/agent-core-discord/src/agent_core_discord/endpoint.py`:

**Line 1254** (inside `_make_on_message_handler` → `on_message`, after successful publish):
```python
# Before
asyncio.create_task(
    self._typing_while_pending(message.channel, mid),
    name=f"discord-{self.name}-typing-{mid}",
)

# After
self._handle.spawn(
    self._typing_while_pending(message.channel, mid),
    name=f"discord-{self.name}-typing-{mid}",
)
```

**Line 1468** (inside `_track_pending_ack`, on LRU eviction):
```python
# Before
asyncio.create_task(
    self._remote_remove_ack(old_id, old_emoji, old_ch),
    name=f"discord-endpoint-{self.name}-evict-ack",
)

# After
self._handle.spawn(
    self._remote_remove_ack(old_id, old_emoji, old_ch),
    name=f"discord-endpoint-{self.name}-evict-ack",
)
```

**Line 1511** (inside `_sweep_pending_acks_once`, on TTL eviction):
```python
# Before
asyncio.create_task(
    self._remote_remove_ack(head_id, emoji, channel_id),
    name=f"discord-endpoint-{self.name}-ttl-ack",
)

# After
self._handle.spawn(
    self._remote_remove_ack(head_id, emoji, channel_id),
    name=f"discord-endpoint-{self.name}-ttl-ack",
)
```

`_track_pending_ack` and `_sweep_pending_acks_once` are synchronous methods. `self._handle`
is always set at their call sites: `_track_pending_ack` is only invoked from the inbound
`on_message` handler (which runs after `start()` and asserts `self._handle is not None` at
line 1241) and from `_pending_acks_sweep_loop` (which only starts after `start()`). The
null-handle case cannot arise.

Do NOT migrate `asyncio.create_task` at line 2222 (inside `_send_typing` tool): that code
already tracks and cancels the task manually via `self._typing_tasks` and is not a leaky site.
Do NOT migrate the long-lived sweep/gateway tasks created in `start()` (lines 579, 615, 619,
624): those are assigned to named instance attributes and cancelled explicitly in `stop()`.

### Test helper changes — adding `spawn()` to fake handles

After migration, any test that starts a discord endpoint and fires `on_message` (which
reaches line 1254) or calls `_track_pending_ack`/`_sweep_pending_acks_once` will call
`self._handle.spawn()`. The following handle fakes need a `spawn()` method:

**`packages/agent-core-discord/src/agent_core_discord/testing/fakes.py` — `FakeBusHandle`**

Add after the existing `endpoints()` method:
```python
def spawn(self, coro, *, name=None):
    import asyncio
    return asyncio.create_task(coro, name=name)
```

**`packages/agent-core-discord/tests/test_endpoint_inbound.py` — `_Recording`**

The tests fire `on_message` via `fake.fire(...)`, which reaches line 1254. Add after `endpoints()`:
```python
def spawn(self, coro, *, name=None):
    import asyncio
    return asyncio.create_task(coro, name=name)
```

**`packages/agent-core-discord/tests/test_endpoint_pending_acks.py` — `_Recording`**

Tests call `ep._track_pending_ack(...)` and `ep._sweep_pending_acks_once()` directly, which
reach lines 1468 and 1511. Add the same `spawn()` method.

No other test files need modification. `test_endpoint_hardening.py` uses `_Recording` but
only delivers `ToolInvocation` envelopes — those paths never reach any spawn call site.
`test_typing_ttl.py` uses `FakeBusHandle` but calls `ep._typing_while_pending()` directly,
bypassing `on_message`.

### New lifecycle tests

Each new test uses a self-contained `_TrackingHandle` that implements real `spawn()`
+ `_drain_tasks()` semantics without a full Bus or SQLite database:

```python
import asyncio

class _TrackingHandle:
    """Minimal handle stub with real spawn+drain semantics for lifecycle tests."""

    def __init__(self):
        self._tasks: set[asyncio.Task] = set()
        self.failures: list[BaseException] = []

    def spawn(self, coro, *, name=None):
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.failures.append(exc)

    async def _drain_tasks(self) -> None:
        tasks = list(self._tasks)
        for t in tasks:
            if not t.done():
                t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def publish(self, *a, **kw):
        await asyncio.sleep(1000)  # hangs so task can be cancelled

    async def ack(self, *a, **kw): ...
    async def nack(self, *a, **kw): ...
```

**`packages/agent-core-inbound/tests/test_inbound_publish_task_lifecycle.py`** (new file):

```python
"""Lifecycle test: _bus_publish_adapter spawned task is cancelled on drain."""
from __future__ import annotations
import asyncio
import uuid
from datetime import UTC, datetime
import pytest
from agent_core_inbound.endpoint import InboundEndpoint

class _TrackingHandle: ...  # as above, with hanging publish()

def _make_ep(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_SECRET", "x")
    return InboundEndpoint(
        name="inbound",
        target_being="wren",
        listen_host="127.0.0.1",
        listen_port=18765,
        webhook_secret_env="TEST_SECRET",
        github_allowance_path=str(tmp_path / "g.toml"),
        audit_log_path=str(tmp_path / "audit.jsonl"),
        rate_limit_per_minute=30,
    )

@pytest.mark.asyncio
async def test_bus_publish_adapter_task_cancelled_on_drain(monkeypatch, tmp_path):
    """Task spawned by _bus_publish_adapter is cancelled when the bus drains."""
    ep = _make_ep(monkeypatch, tmp_path)
    handle = _TrackingHandle()
    ep._handle = handle  # inject directly, bypassing uvicorn start

    from agent_core.bus.envelope import Envelope, NotificationPayload
    ep._bus_publish_adapter(
        to="wren",
        kind="Notification",
        payload={
            "kind": "Notification",
            "source": "github",
            "urgency": "red",
            "body": {},
            "reason": "test",
        },
        urgency="red",
    )

    # yield so the task registers on the event loop
    await asyncio.sleep(0)
    assert len(handle._tasks) == 1

    # Drain — simulates what Bus.stop() does after endpoint.stop().
    await handle._drain_tasks()

    assert len(handle._tasks) == 0
    assert handle.failures == []  # CancelledError is not a failure
```

**`packages/agent-core-voice/tests/test_synthesis_task_lifecycle.py`** (new file):

```python
"""Lifecycle test: synthesis task spawned by deliver() is cancelled on drain."""
from __future__ import annotations
import asyncio
from pathlib import Path
import pytest
from agent_core_voice.endpoint import VoiceEndpoint
from agent_core_voice.protocol import VoiceInfo
from agent_core.bus.envelope import Envelope, EventPayload

class _SlowBackend:
    """Backend that hangs in synthesize so we can test cancellation."""
    SAMPLE_RATE_HZ = 24000
    SAMPLE_WIDTH_BYTES = 2
    def prepare_voice(self, voice_id, ref_wav, ref_text): ...
    def synthesize(self, voice_id, text, seed):
        import time; time.sleep(60)
    def synthesize_batch(self, voice_id, texts, seed):
        import time; time.sleep(60); return [], []

class _TrackingHandle: ...  # as above, with no-op publish() (synthesis posts back later)

@pytest.mark.asyncio
async def test_synthesis_task_cancelled_on_drain(tmp_path, ref_wav):
    """deliver() spawns a synthesis task; it is cancelled when the bus drains."""
    from agent_core_voice.envelopes import SynthesisRequestPayload

    ep = VoiceEndpoint.for_test(
        name="voice",
        backend=_SlowBackend(),
        voices={"alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    ep.register_agent("alice", "alice")
    handle = _TrackingHandle()
    await ep.start(handle)

    req_payload = SynthesisRequestPayload(text="hello")
    env = Envelope(
        id="e1",
        correlation_id="c1",
        from_="alice",
        to="voice",
        kind="Event",
        payload=EventPayload(type="SynthesisRequest", data=req_payload.model_dump()),
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    # deliver() acks immediately, then spawns the synthesis task.
    await ep.deliver(env)

    # One synthesis task is outstanding.
    assert len(handle._tasks) == 1

    # Drain — simulates Bus.stop() after endpoint.stop().
    await ep.stop()
    await handle._drain_tasks()

    assert len(handle._tasks) == 0
    assert handle.failures == []  # CancelledError is not a failure
```

Note: `_TrackingHandle.publish()` for the voice lifecycle test can be a simple no-op
(synthesis may publish a failed envelope when cancelled, but the assert is on `failures` from
exceptions, not the publish calls). Use `async def publish(self, *a, **kw): ...`.

**`packages/agent-core-discord/tests/test_discord_spawn_lifecycle.py`** (new file):

```python
"""Lifecycle tests: typing and evict-ack tasks are cancelled on drain."""
from __future__ import annotations
import asyncio
import pytest
from agent_core_discord.endpoint import DiscordEndpoint
from agent_core_discord.testing.fakes import FakeChannel, FakeDiscordClient, FakeMessage, FakeUser

class _TrackingHandle: ...  # as above, with no-op publish/ack

@pytest.mark.asyncio
async def test_typing_task_cancelled_on_drain(monkeypatch):
    """Typing task spawned by on_message is cancelled when the bus drains."""
    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    fake.user = FakeUser(id="bot", display_name="bot", bot=True)
    handle = _TrackingHandle()
    ep = DiscordEndpoint(
        name="d", target="agent", token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    msg = FakeMessage(id="m1", channel_id="100", content="hello")
    msg.author = FakeUser(id="u1", display_name="user", bot=False)
    msg.guild = type("G", (), {"id": "g1"})()
    msg.channel = ch

    # Fire on_message — publishes to bus and spawns a typing task.
    await fake.fire("on_message", msg)

    # At least one task outstanding (the typing-while-pending task).
    assert len(handle._tasks) >= 1

    # Drain.
    await ep.stop()
    await handle._drain_tasks()

    assert len(handle._tasks) == 0
    assert handle.failures == []

@pytest.mark.asyncio
async def test_evict_ack_task_cancelled_on_drain(monkeypatch):
    """_remote_remove_ack tasks spawned by _track_pending_ack are cancelled on drain."""
    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    handle = _TrackingHandle()
    ep = DiscordEndpoint(
        name="d", target="agent", token_env="X_TOK",
        pending_acks_max=1,  # Force LRU eviction on second insert.
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    # First insert — no eviction.
    ep._track_pending_ack("m1", "👀", "100")
    assert len(handle._tasks) == 0

    # Second insert — LRU evicts m1 and spawns _remote_remove_ack task.
    ep._track_pending_ack("m2", "👀", "100")
    assert len(handle._tasks) == 1  # one evict-ack task

    # Drain.
    await ep.stop()
    await handle._drain_tasks()

    assert len(handle._tasks) == 0
    assert handle.failures == []
```

## Sub-requests (topologically sorted)

1. **Add `spawn()` to `FakeBusHandle`** in
   `packages/agent-core-discord/src/agent_core_discord/testing/fakes.py` (line 499, after
   `endpoints()`). Delegates to `asyncio.create_task`.

2. **Add `spawn()` to `_Recording`** in
   `packages/agent-core-discord/tests/test_endpoint_inbound.py` (line 25) and to `_Recording`
   in `packages/agent-core-discord/tests/test_endpoint_pending_acks.py` (line 24). Same one-liner
   as above. No other test files need this.

3. **Migrate inbound site** — replace line 212 in
   `packages/agent-core-inbound/src/agent_core_inbound/endpoint.py` with
   `self._handle.spawn(self._handle.publish(envelope), name="inbound-bus-publish")`.

4. **Migrate voice site** — replace line 441 in
   `packages/agent-core-voice/src/agent_core_voice/endpoint.py` with
   `self._handle.spawn(self._handle_synthesis_request(envelope, req), name=f"voice-synthesis-{envelope.id}")`.

5. **Migrate discord sites** — replace lines 1254, 1468, 1511 in
   `packages/agent-core-discord/src/agent_core_discord/endpoint.py` with
   `self._handle.spawn(...)` equivalents (same `name=` arg, just different caller).

6. **Write lifecycle tests** — create
   `packages/agent-core-inbound/tests/test_inbound_publish_task_lifecycle.py`,
   `packages/agent-core-voice/tests/test_synthesis_task_lifecycle.py`, and
   `packages/agent-core-discord/tests/test_discord_spawn_lifecycle.py` as specified above.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-inbound/src/agent_core_inbound/endpoint.py` | Line 212: `asyncio.create_task(...)` → `self._handle.spawn(..., name="inbound-bus-publish")` |
| `packages/agent-core-voice/src/agent_core_voice/endpoint.py` | Line 441: `asyncio.create_task(...)` → `self._handle.spawn(..., name=f"voice-synthesis-{envelope.id}")` |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | Lines 1254, 1468, 1511: three `asyncio.create_task(...)` → `self._handle.spawn(...)` with same `name=` args |
| `packages/agent-core-discord/src/agent_core_discord/testing/fakes.py` | Add `spawn()` to `FakeBusHandle` (one-liner delegating to `asyncio.create_task`) |
| `packages/agent-core-discord/tests/test_endpoint_inbound.py` | Add `spawn()` to the file-local `_Recording` class |
| `packages/agent-core-discord/tests/test_endpoint_pending_acks.py` | Add `spawn()` to the file-local `_Recording` class |
| `packages/agent-core-inbound/tests/test_inbound_publish_task_lifecycle.py` | New file: one lifecycle test for the inbound publish adapter |
| `packages/agent-core-voice/tests/test_synthesis_task_lifecycle.py` | New file: one lifecycle test for the synthesis task |
| `packages/agent-core-discord/tests/test_discord_spawn_lifecycle.py` | New file: two lifecycle tests (typing task + evict-ack task) |

No other files change. `packages/core/src/agent_core/bus/handle.py` and `core.py` are already
updated by #290 and must not be modified here.

## Alternatives considered

1. **Add `_drain_tasks()` call directly to each endpoint's `stop()`** — each endpoint calls
   `handle._drain_tasks()` in its own `stop()`. Ruled out: the bus already calls
   `_drain_tasks()` from `Bus.stop()` after `endpoint.stop()`, making per-endpoint drain
   calls redundant. Adding them would require every endpoint's `stop()` to know about the
   pattern, and any future endpoint would need to remember to do the same.
2. **Migrate the `_send_typing` tool task at discord line 2222** — that code already tracks
   its task in `self._typing_tasks` and cancels it in `stop()`. Ruled out: it is not a leaky
   site; migrating it would remove the manual `_typing_tasks` tracking and require additional
   test changes for no safety gain.

## Open questions

None. All five call sites are confirmed in the live code (exact line numbers verified). The
`BusHandle.spawn()` API is already landed. The test files that need `spawn()` added to their
fakes are identified by tracing which paths call `spawn()` after migration.

## Out of scope

- `BusHandle.spawn()` API implementation — landed in #290.
- `EndpointSupervisor` wiring (`_make_task_failure_hook` body replacement) — T3/T4 tickets.
- Migrating `HandoffJobsEndpoint._worker_task` or `ClaudeCodeMCPEndpoint._missing_ack_tasks`
  — those already track and cancel correctly; they are not leaky sites.
- Migrating the discord `_send_typing` tool task (line 2222) — already tracked and not leaky.
- Migrating long-lived sweep/gateway tasks in discord `start()` (lines 579, 615, 619, 624)
  — those are assigned to named instance attrs and cancelled explicitly in `stop()`.
- ack-vs-nack fixes, delivery retry backoff, degraded boot — separate T5/T6 tickets.
- OS-level process supervisor — issue #265.
