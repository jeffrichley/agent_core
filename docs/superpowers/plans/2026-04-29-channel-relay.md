# Channel Relay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the agent-side channel relay (`agent-core-channel`) and the daemon-side notification broker so plain Claude Code agents wake autonomously on bus arrivals.

**Architecture:** Two halves on one branch. Daemon side gets a `NotificationBroker` (per-agent fan-out), a `/notify/<agent>` SSE route on the existing HTTPHost, a `Bus.snapshot_for_agent` helper for initial-wake-on-connect, and a one-line publish hook in `_fire_after_debounce`. New package `packages/agent-core-channel` runs as a stdio MCP subprocess of Claude Code, declares the `claude/channel` experimental capability, opens an SSE listener to the daemon, and re-emits each event as `notifications/claude/channel` over stdio.

**Tech Stack:** Python 3.12, uv workspace, Starlette (existing), Uvicorn (existing), `mcp` low-level Server (no FastMCP for the relay — FastMCP doesn't expose experimental capabilities), `httpx` for SSE consumption, Typer for the CLI. The `agent-core-channel` package depends only on `mcp`, `anyio`, `httpx`, `typer` — NOT on `agent-core`.

---

## Task ordering and rationale

```
Tasks 1–4: Daemon side (additive — does not change responsive-inbox PR semantics).
  1. NotificationBroker (pure dataclass, isolated unit)
  2. Bus.snapshot_for_agent helper (used by initial-wake-on-connect)
  3. /notify/<agent> SSE route on HTTPHost + initial wake
  4. Hook publish call in _fire_after_debounce + runner wiring

Tasks 5–7: Relay side (new package, no dependency on responsive-inbox commits).
  5. agent-core-channel package skeleton + workspace registration
  6. SSE client with reconnect/backoff
  7. Stdio MCP server + run_relay coroutine

Task 8: Cross-package integration test (Layer 3 — proves the full wire path).

Task 9: Re-run responsive-inbox plan's Task 9 live validation
        (Layer 4 — Claude Code's channel handler is the only thing left to verify).
```

Daemon-side tasks are strictly additive — they add new files and one extra line to `_fire_after_debounce`. The responsive-inbox PR's existing 9 commits keep their meaning.

Relay-side tasks build a self-contained package with no dependency on `agent-core`. The relay is a thin pass-through; it doesn't import bus internals.

---

## Task 1: `NotificationBroker` — per-agent fan-out

**Files:**
- Create: `packages/core/src/agent_core/bus/notify_broker.py`
- Test: `packages/core/tests/test_notify_broker.py` (new)

A small async pub/sub broker that lets multiple subscribers per agent each receive a copy of every published event. One bounded `asyncio.Queue` per subscriber, kept in a `set` for O(1) add/remove. Slow-consumer drop with a WARN log — acceptable because `list_pending` is authoritative.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_notify_broker.py`:

```python
"""NotificationBroker: per-agent fan-out for /notify/<agent> subscribers."""

from __future__ import annotations

import asyncio

import pytest

from agent_core.bus.notify_broker import NotificationBroker


@pytest.mark.asyncio
async def test_subscribe_returns_queue():
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")
    assert isinstance(q, asyncio.Queue)


@pytest.mark.asyncio
async def test_publish_fans_out_to_all_subscribers_for_agent():
    broker = NotificationBroker()
    q1 = await broker.subscribe("agent-a")
    q2 = await broker.subscribe("agent-a")
    await broker.publish("agent-a", {"hello": 1})
    assert q1.get_nowait() == {"hello": 1}
    assert q2.get_nowait() == {"hello": 1}


@pytest.mark.asyncio
async def test_publish_isolates_by_agent():
    broker = NotificationBroker()
    q_a = await broker.subscribe("agent-a")
    q_b = await broker.subscribe("agent-b")
    await broker.publish("agent-a", {"hello": 1})
    assert q_a.get_nowait() == {"hello": 1}
    assert q_b.empty()


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue():
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")
    await broker.unsubscribe("agent-a", q)
    await broker.publish("agent-a", {"hello": 1})
    # Original queue receives nothing because it was unsubscribed.
    assert q.empty()


@pytest.mark.asyncio
async def test_unsubscribe_empty_set_removes_agent_key():
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")
    await broker.unsubscribe("agent-a", q)
    # Internal: agent key cleaned up so dict doesn't grow forever.
    assert "agent-a" not in broker._subs


@pytest.mark.asyncio
async def test_publish_to_unknown_agent_is_noop():
    broker = NotificationBroker()
    # No subscribers, no exception.
    await broker.publish("agent-ghost", {"hello": 1})


@pytest.mark.asyncio
async def test_full_queue_drops_event_with_warning(caplog):
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")
    # Fill the bounded queue (default maxsize=128).
    for i in range(128):
        q.put_nowait({"i": i})
    with caplog.at_level("WARNING"):
        await broker.publish("agent-a", {"overflow": True})
    assert any("dropped" in rec.message for rec in caplog.records)
    # Original 128 still present.
    assert q.qsize() == 128
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_notify_broker.py -v`
Expected: ImportError — `agent_core.bus.notify_broker` doesn't exist yet.

- [ ] **Step 3: Implement NotificationBroker**

Create `packages/core/src/agent_core/bus/notify_broker.py`:

```python
"""Per-agent fan-out broker for notification subscribers.

Used by the /notify/<agent> SSE endpoint to deliver pushed envelope summaries
to a stdio channel relay (or any subscriber). Each subscriber gets its own
bounded queue; publish() fans out a copy of the event to all subscribers for
the agent. Slow consumers drop events with a WARN log — list_pending is
authoritative, so missing one push is recoverable on the next poll.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_DEFAULT_QUEUE_MAX = 128


class NotificationBroker:
    """Fan-out broker for per-agent notification subscribers."""

    def __init__(self, queue_max: int = _DEFAULT_QUEUE_MAX) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict]]] = {}
        self._lock = asyncio.Lock()
        self._queue_max = queue_max

    async def subscribe(self, agent: str) -> asyncio.Queue[dict]:
        """Register a subscriber for *agent* and return its queue."""
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=self._queue_max)
        async with self._lock:
            self._subs.setdefault(agent, set()).add(q)
        return q

    async def unsubscribe(self, agent: str, q: asyncio.Queue[dict]) -> None:
        """Remove a subscriber's queue. Cleans up the agent key when empty."""
        async with self._lock:
            subs = self._subs.get(agent)
            if subs:
                subs.discard(q)
                if not subs:
                    del self._subs[agent]

    async def publish(self, agent: str, event: dict) -> None:
        """Fan-out an event to all current subscribers for *agent*.

        A snapshot of the subscriber set is taken under the lock; we then
        publish without holding the lock so a slow Queue.put cannot block
        unsubscribes. Full queues drop the event with a WARN.
        """
        async with self._lock:
            subs = list(self._subs.get(agent, ()))
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning(
                    "notify broker: dropped event for %s (slow consumer)", agent
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_notify_broker.py -v`
Expected: 7 passed.

- [ ] **Step 5: Run the full core suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: green.

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check packages/core/ && uv run ruff format packages/core/`
Expected: clean (pre-existing errors elsewhere unchanged).

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/agent_core/bus/notify_broker.py packages/core/tests/test_notify_broker.py
git commit -m "feat(bus): add NotificationBroker for per-agent fan-out"
```

---

## Task 2: `Bus.snapshot_for_agent` helper

**Files:**
- Modify: `packages/core/src/agent_core/bus/core.py`
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py`
- Test: `packages/core/tests/test_bus_snapshot_for_agent.py` (new)

`Bus.snapshot_for_agent(name)` returns the current notification-summary dict for an agent (same shape as `_build_summary` produces) or `None` if the named endpoint isn't a `ClaudeCodeMCPEndpoint`. Used by the `/notify/<agent>` route to emit an immediate snapshot when a relay first connects, so an agent reconnecting with pending mail gets woken.

The implementation delegates to a new public method `ClaudeCodeMCPEndpoint.snapshot()` that returns the same dict `_build_summary` produces.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_bus_snapshot_for_agent.py`:

```python
"""Bus.snapshot_for_agent: returns the current pending summary for an agent."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core.endpoints.stub import StubEndpoint


def _env(eid: str, frm: str = "stub", urgency: str = "green") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_=frm,
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=eid),
        urgency=urgency,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_snapshot_for_agent_returns_summary_when_pending(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    bus.register(EndpointSpec(endpoint=ep))
    ep._pending = [_env("a"), _env("b", urgency="red")]
    summary = bus.snapshot_for_agent("agent")
    assert summary is not None
    assert summary["meta"]["count"] == 2
    assert summary["meta"]["urgency_max"] == "red"
    assert summary["meta"]["endpoint"] == "agent"


@pytest.mark.asyncio
async def test_snapshot_for_agent_returns_zero_count_when_empty(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    bus.register(EndpointSpec(endpoint=ep))
    summary = bus.snapshot_for_agent("agent")
    assert summary is not None
    assert summary["meta"]["count"] == 0


@pytest.mark.asyncio
async def test_snapshot_for_agent_returns_none_for_unknown_agent(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    summary = bus.snapshot_for_agent("ghost")
    assert summary is None


@pytest.mark.asyncio
async def test_snapshot_for_agent_returns_none_for_non_claude_endpoint(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    stub = StubEndpoint(name="probe", description="stub")
    bus.register(EndpointSpec(endpoint=stub))
    summary = bus.snapshot_for_agent("probe")
    assert summary is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_bus_snapshot_for_agent.py -v`
Expected: AttributeError on `bus.snapshot_for_agent` (method doesn't exist).

- [ ] **Step 3: Add `snapshot()` method to ClaudeCodeMCPEndpoint**

In `packages/core/src/agent_core/endpoints/claude_code_mcp.py`, add a new public method just below `_build_summary` (search for `def _build_summary`):

```python
    def snapshot(self) -> dict:
        """Public wrapper around _build_summary; used by Bus.snapshot_for_agent.

        Returns the same dict shape as the push pipeline produces, so a
        snapshot emitted on relay connect looks identical to a real push.
        """
        return self._build_summary()
```

- [ ] **Step 4: Add `snapshot_for_agent` to Bus**

In `packages/core/src/agent_core/bus/core.py`, find the `Bus` class. Add a new method (placement: near other public read-only helpers like `endpoints()` if present; otherwise just before `start()`):

```python
    def snapshot_for_agent(self, name: str) -> dict | None:
        """Return the current notification summary for an agent, or None.

        Only ClaudeCodeMCPEndpoint instances support snapshots; other endpoint
        types return None. Used by the /notify/<agent> SSE route to emit an
        immediate state event when a relay connects, so reconnecting agents
        with pending mail get woken without waiting for the next arrival.
        """
        ep_spec = self._endpoints_by_name.get(name)
        if ep_spec is None:
            return None
        ep = ep_spec.endpoint
        snapshot_fn = getattr(ep, "snapshot", None)
        if snapshot_fn is None:
            return None
        return snapshot_fn()
```

(If `_endpoints_by_name` doesn't exist on Bus by that name, find the actual attribute holding registered endpoints — `_endpoints` or similar — and use that. The test that exercises this assumes a `name → spec` mapping is available.)

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_bus_snapshot_for_agent.py -v`
Expected: 4 passed.

- [ ] **Step 6: Run the full core suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: green.

- [ ] **Step 7: Lint and format**

Run: `uv run ruff check packages/core/ && uv run ruff format packages/core/`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/agent_core/bus/core.py packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/tests/test_bus_snapshot_for_agent.py
git commit -m "feat(bus): add snapshot_for_agent helper for initial-wake-on-connect"
```

---

## Task 3: `/notify/<agent>` SSE route on HTTPHost

**Files:**
- Modify: `packages/core/src/agent_core/bus/http_host.py`
- Test: `packages/core/tests/test_notify_route.py` (new)

Add a new Starlette `Route` for `/notify/{agent}` directly on `HTTPHost.start()`'s router. The route returns a `StreamingResponse` (text/event-stream) that:

1. Subscribes to the broker for the path's `agent` parameter.
2. On first connect: if a snapshot callable was provided and returns a non-empty summary, emit it immediately as the first event.
3. Loop forever pulling from the queue and yielding `data: <json>\n\n` lines.
4. On stream close (client disconnect or server shutdown), unsubscribe from the broker.

`HTTPHost.__init__` gains two optional kwargs:
- `notify_broker: NotificationBroker | None = None`
- `notify_snapshot: Callable[[str], dict | None] | None = None`

When both are provided, the `/notify/{agent}` route is registered. When either is None, the route is omitted (back-compat: existing tests that construct HTTPHost without these kwargs still work).

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_notify_route.py`:

```python
"""HTTPHost /notify/<agent> SSE route: subscribe + initial wake + stream events."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_core.bus.http_host import HTTPHost
from agent_core.bus.notify_broker import NotificationBroker


@pytest.mark.asyncio
async def test_notify_route_streams_published_events():
    broker = NotificationBroker()
    host = HTTPHost(bind_port=0, notify_broker=broker, notify_snapshot=lambda _name: None)
    await host.start()
    try:
        url = f"http://127.0.0.1:{host.port}/notify/agent-a"
        events = []
        async with httpx.AsyncClient(timeout=5.0) as client:
            async with client.stream("GET", url) as resp:
                # Drive a publish on a background task once the stream is open.
                async def push_after_delay():
                    await asyncio.sleep(0.2)
                    await broker.publish("agent-a", {"meta": {"count": 1}})

                pump = asyncio.create_task(push_after_delay())
                try:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[len("data: "):]))
                            break  # got our event
                finally:
                    pump.cancel()
        assert events == [{"meta": {"count": 1}}]
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_notify_route_emits_initial_snapshot_when_pending_exists():
    broker = NotificationBroker()

    def fake_snapshot(name: str) -> dict | None:
        if name == "agent-a":
            return {"content": "INBOX: 1 pending", "meta": {"count": 1, "endpoint": "agent-a"}}
        return None

    host = HTTPHost(bind_port=0, notify_broker=broker, notify_snapshot=fake_snapshot)
    await host.start()
    try:
        url = f"http://127.0.0.1:{host.port}/notify/agent-a"
        events = []
        async with httpx.AsyncClient(timeout=5.0) as client:
            async with client.stream("GET", url) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: "):]))
                        break  # initial snapshot received
        assert len(events) == 1
        assert events[0]["meta"]["count"] == 1
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_notify_route_skips_initial_snapshot_when_count_zero():
    broker = NotificationBroker()

    def fake_snapshot(_name: str) -> dict | None:
        return {"content": "INBOX: 0 pending", "meta": {"count": 0, "endpoint": "agent-a"}}

    host = HTTPHost(bind_port=0, notify_broker=broker, notify_snapshot=fake_snapshot)
    await host.start()
    try:
        url = f"http://127.0.0.1:{host.port}/notify/agent-a"
        async with httpx.AsyncClient(timeout=2.0) as client:
            async with client.stream("GET", url) as resp:
                # No initial snapshot. Drive a real publish to confirm the
                # stream is live but the snapshot was suppressed.
                async def push_after_delay():
                    await asyncio.sleep(0.2)
                    await broker.publish("agent-a", {"meta": {"count": 1}})

                pump = asyncio.create_task(push_after_delay())
                events = []
                try:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[len("data: "):]))
                            break
                finally:
                    pump.cancel()
        # The first event we received was the post-connect publish, not the
        # zero-count snapshot.
        assert events == [{"meta": {"count": 1}}]
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_notify_route_unsubscribes_on_disconnect():
    broker = NotificationBroker()
    host = HTTPHost(bind_port=0, notify_broker=broker, notify_snapshot=lambda _: None)
    await host.start()
    try:
        url = f"http://127.0.0.1:{host.port}/notify/agent-a"
        async with httpx.AsyncClient(timeout=2.0) as client:
            async with client.stream("GET", url):
                # Open and close immediately.
                pass
        # Give the server a moment to run the unsubscribe finally block.
        await asyncio.sleep(0.2)
        # The agent-a subscriber set should be cleaned up.
        assert "agent-a" not in broker._subs
    finally:
        await host.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_notify_route.py -v`
Expected: TypeError — HTTPHost.__init__ doesn't accept `notify_broker` / `notify_snapshot` kwargs yet.

- [ ] **Step 3: Add stop() method to HTTPHost if not already present**

Verify whether `HTTPHost` has a `stop()` method (the existing `bus_daemon` integration test calls `await http_host.stop()`). If it does NOT exist, add this method just after `start()`:

```python
    async def stop(self) -> None:
        """Stop the uvicorn server cleanly."""
        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            try:
                await asyncio.wait_for(self._serve_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._serve_task.cancel()
        self._started = False
```

If `stop()` already exists, leave it alone.

- [ ] **Step 4: Wire broker + snapshot into HTTPHost**

In `packages/core/src/agent_core/bus/http_host.py`:

Add imports near the top:

```python
import json
from collections.abc import AsyncIterator, Callable
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

from agent_core.bus.notify_broker import NotificationBroker
```

Modify the `__init__` signature:

```python
    def __init__(
        self,
        *,
        bind_host: str = "127.0.0.1",
        bind_port: int = 8788,
        notify_broker: NotificationBroker | None = None,
        notify_snapshot: Callable[[str], dict | None] | None = None,
    ):
        self._bind_host = bind_host
        self._requested_port = bind_port
        self._mounts: list[MCPHostable] = []
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task | None = None
        self._started = False
        self._notify_broker = notify_broker
        self._notify_snapshot = notify_snapshot
```

In `start()`, just before `router = Router(...)`, build the routes list to include the optional `/notify/{agent}`:

```python
        sub_apps = [m.asgi_app() for m in self._mounts]
        mount_prefixes = {m.mount for m in self._mounts}

        routes: list = [
            Mount(m.mount, app=app) for m, app in zip(self._mounts, sub_apps)
        ]
        if self._notify_broker is not None and self._notify_snapshot is not None:
            routes.append(
                Route(
                    "/notify/{agent}",
                    endpoint=self._make_notify_handler(),
                    methods=["GET"],
                )
            )

        router = Router(
            routes=routes,
            redirect_slashes=False,
            lifespan=_make_lifespan(sub_apps),
        )
```

Add the handler factory as a method on `HTTPHost`:

```python
    def _make_notify_handler(self):
        broker = self._notify_broker
        snapshot = self._notify_snapshot
        assert broker is not None and snapshot is not None  # guard for type-checker

        async def _notify(request: Request) -> StreamingResponse:
            agent = request.path_params["agent"]
            queue = await broker.subscribe(agent)

            async def event_stream() -> AsyncIterator[bytes]:
                try:
                    initial = snapshot(agent)
                    if initial is not None and initial.get("meta", {}).get("count", 0) > 0:
                        yield f"data: {json.dumps(initial)}\n\n".encode("utf-8")
                    while True:
                        event = await queue.get()
                        yield f"data: {json.dumps(event)}\n\n".encode("utf-8")
                finally:
                    await broker.unsubscribe(agent, queue)

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        return _notify
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_notify_route.py -v`
Expected: 4 passed.

- [ ] **Step 6: Run the full core suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: green. Existing tests that construct `HTTPHost(bind_port=0)` without the new kwargs still work because both default to None (route is omitted).

- [ ] **Step 7: Lint and format**

Run: `uv run ruff check packages/core/ && uv run ruff format packages/core/`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/agent_core/bus/http_host.py packages/core/tests/test_notify_route.py
git commit -m "feat(http-host): add /notify/<agent> SSE route + initial-wake-on-connect"
```

---

## Task 4: Hook `_fire_after_debounce` into the broker + runner wiring

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py`
- Modify: `packages/core/src/agent_core/bus/runner.py`
- Test: `packages/core/tests/test_notify_broker_publish_hook.py` (new)

Two pieces:

1. **Hook:** `ClaudeCodeMCPEndpoint` accepts an optional `notify_broker` in `__init__`. After `_fire_after_debounce` sends to `_active_session`, it also calls `await broker.publish(self.name, summary)` if a broker is set. This is the load-bearing one-line change that makes pushes reach the relay.

2. **Wiring:** `runner.py`'s `build_bus_from_config` instantiates a `NotificationBroker`, passes it to `HTTPHost`, and passes it to every `ClaudeCodeMCPEndpoint` it constructs.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_notify_broker_publish_hook.py`:

```python
"""ClaudeCodeMCPEndpoint publishes to the broker on each push."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


def _env(eid: str = "e1") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="src",
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=eid),
        urgency="green",
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_endpoint_publishes_to_broker_when_session_active():
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")

    class _RecordingSession:
        async def send_message(self, _msg) -> None:
            pass

    ep = ClaudeCodeMCPEndpoint(name="agent-a", mount="/mcp/a", notify_broker=broker)
    ep._register_session(_RecordingSession())
    ep._pending = [_env("e1")]

    await ep._notify_mail_arrived()
    await asyncio.sleep(0.1)  # let debounce fire

    event = q.get_nowait()
    assert event["meta"]["count"] == 1
    assert event["meta"]["endpoint"] == "agent-a"


@pytest.mark.asyncio
async def test_endpoint_publishes_to_broker_even_when_no_session():
    """Even with no Claude Code session attached, the relay should still
    receive notifications — that's the whole point of the relay path.
    """
    broker = NotificationBroker()
    q = await broker.subscribe("agent-a")

    ep = ClaudeCodeMCPEndpoint(name="agent-a", mount="/mcp/a", notify_broker=broker)
    # No session registered.
    ep._pending = [_env("e1")]

    await ep._notify_mail_arrived()
    await asyncio.sleep(0.1)

    event = q.get_nowait()
    assert event["meta"]["count"] == 1


@pytest.mark.asyncio
async def test_endpoint_with_no_broker_still_works():
    """Back-compat: endpoint constructed without a broker is fine."""
    ep = ClaudeCodeMCPEndpoint(name="agent-a", mount="/mcp/a")  # no broker
    ep._pending = [_env("e1")]

    # Should not raise.
    await ep._notify_mail_arrived()
    await asyncio.sleep(0.1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_notify_broker_publish_hook.py -v`
Expected: TypeError — `ClaudeCodeMCPEndpoint.__init__` doesn't accept `notify_broker`.

- [ ] **Step 3: Wire the broker into ClaudeCodeMCPEndpoint**

In `packages/core/src/agent_core/endpoints/claude_code_mcp.py`:

Add to imports (near other `agent_core.bus.*` imports):

```python
from agent_core.bus.notify_broker import NotificationBroker
```

Update `__init__` signature and body:

```python
    def __init__(
        self,
        *,
        name: str,
        mount: str,
        notify_broker: "NotificationBroker | None" = None,
    ):
        self.name = name
        self.mount = mount
        # ... existing FastMCP construction ...
        self._handle: "BusHandle | None" = None
        self._pending: list[Envelope] = []
        self._session_active: bool = False
        self._active_session: Any = None
        self._notify_debounce_seconds: float = 0.05
        self._debounce_task: asyncio.Task | None = None
        self._notify_broker = notify_broker
        self._mcp.add_middleware(SessionRegistry(self))
        self._register_tools()
```

Update `_fire_after_debounce` — find this method and add a `broker.publish` call. The two cases (session attached vs not) BOTH need to publish to the broker so the relay receives notifications even when no Claude Code HTTP MCP session is currently captured. Replace the existing body:

```python
    async def _fire_after_debounce(self) -> None:
        try:
            await asyncio.sleep(self._notify_debounce_seconds)
        except asyncio.CancelledError:
            return
        summary = self._build_summary()

        # Always publish to the broker so /notify/<agent> subscribers
        # (the channel relay) wake the agent regardless of whether the
        # daemon's HTTP MCP session is currently captured.
        if self._notify_broker is not None:
            try:
                await self._notify_broker.publish(self.name, summary)
            except Exception:
                log.warning(
                    "endpoint '%s': broker publish failed", self.name, exc_info=True
                )

        session = self._active_session
        if session is None:
            log.info(
                "endpoint '%s': debounce fired; no active session, skipping HTTP push",
                self.name,
            )
            return
        try:
            message = self._make_channel_notification(summary)
            log.info(
                "endpoint '%s': pushing notifications/claude/channel to session %d (count=%d)",
                self.name,
                id(session),
                summary["meta"]["count"],
            )
            await session.send_message(message)
            log.info("endpoint '%s': push to session %d returned", self.name, id(session))
        except Exception:
            log.warning(
                "endpoint '%s': push to active session failed; clearing slot",
                self.name,
                exc_info=True,
            )
            if self._active_session is session:
                self._active_session = None
                self._session_active = False
```

- [ ] **Step 4: Run hook test to verify it passes**

Run: `uv run pytest packages/core/tests/test_notify_broker_publish_hook.py -v`
Expected: 3 passed.

- [ ] **Step 5: Wire runner.py**

In `packages/core/src/agent_core/bus/runner.py`, find `build_bus_from_config`. After the bus is constructed and before the HTTPHost is built, add:

```python
    from agent_core.bus.notify_broker import NotificationBroker
    notify_broker = NotificationBroker()
```

Then thread it into both:

a) `HTTPHost(...)` construction — find where HTTPHost is instantiated. Add the kwargs:

```python
    http_host = HTTPHost(
        bind_host=http_cfg.get("bind_host", "127.0.0.1"),
        bind_port=http_cfg.get("bind_port", 8788),
        notify_broker=notify_broker,
        notify_snapshot=bus.snapshot_for_agent,
    )
```

b) `ClaudeCodeMCPEndpoint` construction — find where endpoints are instantiated by class path lookup. After the endpoint is constructed via `cls(**params)`, if it's a `ClaudeCodeMCPEndpoint`, attach the broker:

```python
    instance = cls(**params)
    # Inject the notify broker into ClaudeCodeMCPEndpoint instances so
    # they can fan-out push events to /notify/<agent> subscribers.
    from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
    if isinstance(instance, ClaudeCodeMCPEndpoint):
        instance._notify_broker = notify_broker
```

(If runner already has a clean way to pass kwargs by inspecting the constructor signature, prefer that. The post-construction injection is the conservative shape that doesn't require runner changes for non-ClaudeCode endpoints.)

- [ ] **Step 6: Run the full core suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: green. The bus daemon integration test should still pass.

- [ ] **Step 7: Lint and format**

Run: `uv run ruff check packages/core/ && uv run ruff format packages/core/`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/src/agent_core/bus/runner.py packages/core/tests/test_notify_broker_publish_hook.py
git commit -m "feat(claude-mcp,runner): publish push summaries to NotificationBroker"
```

---

## Task 5: `agent-core-channel` package skeleton + workspace registration

**Files:**
- Create: `packages/agent-core-channel/pyproject.toml`
- Create: `packages/agent-core-channel/src/agent_core_channel/__init__.py`
- Create: `packages/agent-core-channel/src/agent_core_channel/__main__.py`
- Create: `packages/agent-core-channel/tests/__init__.py`
- Create: `packages/agent-core-channel/tests/test_cli.py`
- Modify: `pyproject.toml` (root) — add to `[tool.uv.sources]`, `[tool.ruff].src`, `[tool.pytest.ini_options].testpaths`

Stand up the empty package shell with a Typer CLI that parses args and prints them. No real relay logic yet — tasks 6 and 7 fill that in.

- [ ] **Step 1: Create the package directory and pyproject.toml**

Create `packages/agent-core-channel/pyproject.toml`:

```toml
[project]
name = "agent-core-channel"
version = "0.1.0"
description = "Stdio MCP channel relay — bridges agent-core daemon notifications to Claude Code's wake mechanism."
requires-python = ">=3.12"
dependencies = [
    "mcp>=1.0",
    "anyio>=4.0",
    "httpx>=0.27",
    "typer>=0.12",
]

[project.scripts]
agent-core-channel = "agent_core_channel.__main__:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core_channel"]
```

Create `packages/agent-core-channel/src/agent_core_channel/__init__.py`:

```python
"""agent-core-channel — stdio MCP relay for the agent-core daemon."""
```

Create `packages/agent-core-channel/src/agent_core_channel/__main__.py`:

```python
"""Typer CLI entry point for agent-core-channel.

This is a tiny shim that parses --agent and --daemon-url, then hands off to
run_relay(). The real relay logic lives in agent_core_channel.stdio_server.
"""

from __future__ import annotations

import anyio
import typer

app = typer.Typer(add_completion=False)


@app.command()
def main(
    agent: str = typer.Option(..., "--agent", help="Agent name on the bus."),
    daemon_url: str = typer.Option(
        "http://127.0.0.1:8788",
        "--daemon-url",
        help="agent-core daemon URL (default: http://127.0.0.1:8788).",
    ),
) -> None:
    """Run the agent-core stdio channel relay."""
    from agent_core_channel.stdio_server import run_relay

    anyio.run(run_relay, agent, daemon_url)


if __name__ == "__main__":
    app()
```

Create `packages/agent-core-channel/tests/__init__.py` (empty).

Create `packages/agent-core-channel/tests/test_cli.py`:

```python
"""Smoke tests for the Typer CLI entrypoint."""

from __future__ import annotations

from typer.testing import CliRunner

from agent_core_channel.__main__ import app


def test_cli_help_runs():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--agent" in result.output
    assert "--daemon-url" in result.output


def test_cli_requires_agent():
    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "Missing option" in result.output or "required" in result.output.lower()
```

- [ ] **Step 2: Register the package in the workspace**

In the root `pyproject.toml`, find `[tool.uv.sources]` and add:

```toml
agent-core-channel = { workspace = true }
```

Find `[tool.ruff]` and update `src`:

```toml
[tool.ruff]
line-length = 100
src = [
    "packages/core/src",
    "packages/notify/src",
    "packages/credentials/src",
    "packages/agent-core-channel/src",
]
```

Find `[tool.pytest.ini_options]` and update `testpaths`:

```toml
testpaths = [
    "packages/core/tests",
    "packages/credentials/tests",
    "packages/agent-core-discord/tests",
    "packages/agent-core-channel/tests",
]
```

- [ ] **Step 3: Sync the workspace**

Run: `uv sync`
Expected: agent-core-channel installed; new `agent-core-channel` script available in `.venv/Scripts/`.

- [ ] **Step 4: Run the CLI smoke tests**

Run: `uv run pytest packages/agent-core-channel/tests/test_cli.py -v`
Expected: 2 passed.

- [ ] **Step 5: Verify the entry point**

Run: `uv run agent-core-channel --help`
Expected: Typer-formatted help text showing `--agent` and `--daemon-url`.

(This will fail at the `from agent_core_channel.stdio_server import run_relay` line if `agent-core-channel` is invoked WITHOUT `--help`. That's fine — the help-only path doesn't hit the import. Subsequent tasks add `stdio_server.py`.)

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check packages/agent-core-channel/ && uv run ruff format packages/agent-core-channel/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add packages/agent-core-channel/ pyproject.toml uv.lock
git commit -m "feat(channel): scaffold agent-core-channel package + Typer CLI"
```

---

## Task 6: SSE client with reconnect/backoff

**Files:**
- Create: `packages/agent-core-channel/src/agent_core_channel/sse_client.py`
- Test: `packages/agent-core-channel/tests/test_sse_client.py`

A small async iterator that opens an SSE connection to `<daemon_url>/notify/<agent>`, parses `data:` lines, yields decoded JSON dicts. On connection failure or stream close, retries with exponential backoff (2s → 4s → 8s → cap 30s). Backoff resets on a successful event reception.

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-channel/tests/test_sse_client.py`:

```python
"""SSE client: parse data: lines, retry with backoff on failure."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_core_channel.sse_client import iter_notify_events


class _FakeStreamResponse:
    """Mimics httpx.Response.aiter_lines() behavior for one batch of lines."""

    def __init__(self, lines: list[str]):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    """Mimics httpx.AsyncClient.stream(). Each call yields the next scripted response.

    A response can be a list[str] (lines) or an Exception (raised on stream open).
    """

    def __init__(self, scripted: list):
        self._scripted = list(scripted)
        self.calls: list[tuple[str, str]] = []

    def stream(self, method: str, url: str):
        self.calls.append((method, url))
        nxt = self._scripted.pop(0)

        class _Cm:
            async def __aenter__(_self):
                if isinstance(nxt, Exception):
                    raise nxt
                return _FakeStreamResponse(nxt)

            async def __aexit__(_self, *exc):
                return False

        return _Cm()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_sse_client_yields_parsed_data_lines():
    client = _FakeAsyncClient(
        scripted=[
            [
                'data: {"meta": {"count": 1}}',
                "",
                'data: {"meta": {"count": 2}}',
                "",
            ]
        ]
    )

    events: list[dict] = []
    async for ev in iter_notify_events(
        agent="agent-a",
        daemon_url="http://127.0.0.1:8788",
        client_factory=lambda: client,
        max_events=2,
    ):
        events.append(ev)

    assert events == [{"meta": {"count": 1}}, {"meta": {"count": 2}}]
    assert client.calls == [("GET", "http://127.0.0.1:8788/notify/agent-a")]


@pytest.mark.asyncio
async def test_sse_client_reconnects_after_stream_close():
    """When a stream ends, the client immediately reconnects."""
    client = _FakeAsyncClient(
        scripted=[
            ['data: {"first": 1}', ""],   # first stream, then closes
            ['data: {"second": 2}', ""],  # reconnect
        ]
    )

    events: list[dict] = []
    async for ev in iter_notify_events(
        agent="a",
        daemon_url="http://127.0.0.1:8788",
        client_factory=lambda: client,
        max_events=2,
        backoff_initial=0.001,  # speed up the test
        backoff_max=0.001,
    ):
        events.append(ev)

    assert events == [{"first": 1}, {"second": 2}]
    assert len(client.calls) == 2  # reconnected


@pytest.mark.asyncio
async def test_sse_client_retries_on_connection_error():
    """On exception during stream open, retry with backoff."""
    client = _FakeAsyncClient(
        scripted=[
            ConnectionError("daemon down"),
            ConnectionError("still down"),
            ['data: {"after_retry": true}', ""],
        ]
    )

    events: list[dict] = []
    async for ev in iter_notify_events(
        agent="a",
        daemon_url="http://127.0.0.1:8788",
        client_factory=lambda: client,
        max_events=1,
        backoff_initial=0.001,
        backoff_max=0.001,
    ):
        events.append(ev)

    assert events == [{"after_retry": True}]
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_sse_client_skips_non_data_lines():
    """Comments, empty lines, and other SSE fields are ignored."""
    client = _FakeAsyncClient(
        scripted=[
            [
                ":heartbeat",
                "event: ping",
                "id: 42",
                'data: {"real": true}',
                "",
            ]
        ]
    )

    events: list[dict] = []
    async for ev in iter_notify_events(
        agent="a",
        daemon_url="http://127.0.0.1:8788",
        client_factory=lambda: client,
        max_events=1,
    ):
        events.append(ev)

    assert events == [{"real": True}]


@pytest.mark.asyncio
async def test_sse_client_handles_malformed_json():
    """Malformed data: lines are logged and skipped, not fatal."""
    client = _FakeAsyncClient(
        scripted=[
            [
                "data: not json at all",
                "",
                'data: {"valid": true}',
                "",
            ]
        ]
    )

    events: list[dict] = []
    async for ev in iter_notify_events(
        agent="a",
        daemon_url="http://127.0.0.1:8788",
        client_factory=lambda: client,
        max_events=1,
    ):
        events.append(ev)

    # The malformed line is dropped; the valid one comes through.
    assert events == [{"valid": True}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-channel/tests/test_sse_client.py -v`
Expected: ImportError — `agent_core_channel.sse_client` doesn't exist.

- [ ] **Step 3: Implement the SSE client**

Create `packages/agent-core-channel/src/agent_core_channel/sse_client.py`:

```python
"""Async SSE client: open /notify/<agent>, yield parsed JSON events.

On stream close or connection error, reconnects with exponential backoff
(2s → 4s → 8s → cap 30s by default; configurable for tests). Backoff resets
on successful event reception.

The factory pattern (client_factory=) lets tests inject a fake httpx-like
client; production calls iter_notify_events without overriding it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import anyio
import httpx

log = logging.getLogger(__name__)


def _default_client_factory() -> httpx.AsyncClient:
    # No total timeout — SSE streams stay open indefinitely. We rely on the
    # underlying connection being closed by the server (or an OS-level
    # disconnect) to break the iteration.
    return httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0))


async def iter_notify_events(
    agent: str,
    daemon_url: str,
    *,
    client_factory: Callable[[], Any] = _default_client_factory,
    max_events: int | None = None,
    backoff_initial: float = 2.0,
    backoff_max: float = 30.0,
) -> AsyncIterator[dict]:
    """Yield JSON events from /notify/<agent> until cancelled.

    Reconnects forever on stream close / connection error, with exponential
    backoff. Successful event reception resets the backoff. ``max_events`` is
    a test hook — when set, the iterator stops after that many events.
    """
    url = f"{daemon_url.rstrip('/')}/notify/{agent}"
    backoff = backoff_initial
    emitted = 0

    while True:
        try:
            async with client_factory() as client:
                async with client.stream("GET", url) as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload_text = line[len("data: "):]
                        try:
                            event = json.loads(payload_text)
                        except json.JSONDecodeError:
                            log.warning(
                                "sse client: dropped malformed event for %s: %r",
                                agent,
                                payload_text,
                            )
                            continue
                        backoff = backoff_initial  # reset on successful event
                        yield event
                        emitted += 1
                        if max_events is not None and emitted >= max_events:
                            return
            # Stream ended cleanly; reconnect immediately (no backoff).
            log.debug("sse client: stream for %s closed; reconnecting", agent)
        except Exception as exc:
            log.warning(
                "sse client: connection error for %s: %s; retrying in %.1fs",
                agent,
                exc,
                backoff,
            )
            await anyio.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/agent-core-channel/tests/test_sse_client.py -v`
Expected: 5 passed.

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check packages/agent-core-channel/ && uv run ruff format packages/agent-core-channel/`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/sse_client.py packages/agent-core-channel/tests/test_sse_client.py
git commit -m "feat(channel): SSE client with reconnect/backoff"
```

---

## Task 7: Stdio MCP server + run_relay coroutine

**Files:**
- Create: `packages/agent-core-channel/src/agent_core_channel/stdio_server.py`
- Test: `packages/agent-core-channel/tests/test_stdio_server.py`

The stdio MCP server uses `mcp.server.lowlevel.server.Server` (NOT FastMCP — FastMCP doesn't expose experimental capabilities). It declares `experimental.claude/channel = {}`, exposes zero tools/resources/prompts, and has a write-stream-injection helper so the SSE consumer can emit `notifications/claude/channel` events from outside a request handler.

`run_relay(agent, daemon_url)` is the entry point coroutine: spawns two concurrent tasks under `anyio.create_task_group` — the MCP server loop on stdin/stdout, and the SSE consumer pump that writes back into the MCP write stream. Either task ending cancels the other.

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-channel/tests/test_stdio_server.py`:

```python
"""Stdio MCP server: handshake declares claude/channel capability, no tools.

The end-to-end test (Layer 3) lives in test_end_to_end_relay.py and exercises
the full pipeline. These tests focus on the MCP handshake itself.
"""

from __future__ import annotations

import json

import anyio
import pytest

from agent_core_channel.stdio_server import build_initialization_options


def test_initialization_options_declare_claude_channel_capability():
    """init_options.capabilities.experimental must contain claude/channel."""
    init_options = build_initialization_options(server_name="agent-core-channel")
    caps = init_options.capabilities
    # Pydantic / BaseModel — accept either dict-style or attribute-style.
    experimental = getattr(caps, "experimental", None) or {}
    assert "claude/channel" in experimental


def test_initialization_options_declare_no_tools_resources_prompts():
    """The relay exposes none of the standard MCP server features."""
    init_options = build_initialization_options(server_name="agent-core-channel")
    caps = init_options.capabilities
    # Tools/resources/prompts capabilities should be absent or default-empty.
    # We don't enforce a specific representation; just verify nothing is advertised.
    tools = getattr(caps, "tools", None)
    resources = getattr(caps, "resources", None)
    prompts = getattr(caps, "prompts", None)
    # If any are set, they must indicate empty/no listChanged tracking — but
    # the cleanest assertion is "all None / falsy".
    assert not tools
    assert not resources
    assert not prompts


@pytest.mark.asyncio
async def test_emit_channel_notification_writes_jsonrpc_to_stream():
    """emit_channel_notification serializes a SessionMessage to the write stream."""
    from agent_core_channel.stdio_server import emit_channel_notification

    send_stream, receive_stream = anyio.create_memory_object_stream(max_buffer_size=8)

    summary = {
        "content": "INBOX: 1 pending",
        "meta": {"count": 1, "endpoint": "agent-a"},
    }
    await emit_channel_notification(send_stream, summary)

    # Pull the SessionMessage and inspect it.
    msg = await receive_stream.receive()
    root = msg.message.root
    assert root.method == "notifications/claude/channel"
    assert root.params == summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-channel/tests/test_stdio_server.py -v`
Expected: ImportError — `agent_core_channel.stdio_server` doesn't exist.

- [ ] **Step 3: Implement the stdio MCP server**

Create `packages/agent-core-channel/src/agent_core_channel/stdio_server.py`:

```python
"""Stdio MCP server for the channel relay.

Uses the low-level mcp.server.Server API so we can declare the
experimental ``claude/channel`` capability — FastMCP doesn't expose
that yet. The server exposes zero tools, zero resources, zero prompts;
its only job is to keep the stdio MCP handshake alive and provide a
write stream that the SSE pump can use to emit
``notifications/claude/channel`` events.
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.shared.session import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification

from agent_core_channel.sse_client import iter_notify_events

log = logging.getLogger(__name__)


def build_initialization_options(server_name: str = "agent-core-channel"):
    """Construct InitializationOptions that declare the claude/channel capability.

    No tools, resources, or prompts are advertised. Notification options are
    default (no list-changed tracking).
    """
    server = Server(server_name)
    notification_options = NotificationOptions()
    experimental: dict[str, dict[str, Any]] = {"claude/channel": {}}
    return server.create_initialization_options(
        notification_options=notification_options,
        experimental_capabilities=experimental,
    )


async def emit_channel_notification(
    write_stream: "anyio.abc.ObjectSendStream[SessionMessage]",
    summary: dict,
) -> None:
    """Write a notifications/claude/channel SessionMessage to the MCP stream."""
    notification = JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/claude/channel",
        params=summary,
    )
    msg = SessionMessage(message=JSONRPCMessage(notification))
    await write_stream.send(msg)


async def _sse_pump(
    agent: str,
    daemon_url: str,
    write_stream: "anyio.abc.ObjectSendStream[SessionMessage]",
) -> None:
    """Read events from /notify/<agent> and emit them as MCP notifications."""
    async for summary in iter_notify_events(agent=agent, daemon_url=daemon_url):
        try:
            await emit_channel_notification(write_stream, summary)
        except Exception:
            log.warning("sse pump: emit failed; continuing", exc_info=True)


async def run_relay(agent: str, daemon_url: str) -> None:
    """Run the channel relay until stdin closes or a fatal error.

    Two concurrent tasks under one task group:
    - The MCP stdio server loop (Server.run reading from stdin, writing to stdout).
    - The SSE pump (consume daemon /notify/<agent>, write notifications onto the
      same MCP write stream).

    When stdin closes (Claude Code shut down), Server.run() returns and the
    task group cancels the SSE pump. When the SSE pump dies (which it shouldn't
    — it has its own retry loop), the task group cancels Server.run().
    """
    server = Server("agent-core-channel")
    init_options = build_initialization_options()

    async with stdio_server() as (read_stream, write_stream):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_sse_pump, agent, daemon_url, write_stream)
            await server.run(read_stream, write_stream, init_options)
            # Server.run returned (stdin closed). Cancel the SSE pump.
            tg.cancel_scope.cancel()
```

- [ ] **Step 4: Run stdio_server tests**

Run: `uv run pytest packages/agent-core-channel/tests/test_stdio_server.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full agent-core-channel suite**

Run: `uv run pytest packages/agent-core-channel/tests/ -v`
Expected: 10 passed (5 sse_client + 3 stdio_server + 2 cli).

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check packages/agent-core-channel/ && uv run ruff format packages/agent-core-channel/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/stdio_server.py packages/agent-core-channel/tests/test_stdio_server.py
git commit -m "feat(channel): stdio MCP server with claude/channel capability + run_relay"
```

---

## Task 8: Cross-package end-to-end integration test

**Files:**
- Test: `packages/agent-core-channel/tests/test_end_to_end_relay.py` (new)

The Layer 3 test from the spec. Real bus + real HTTPHost + real `/notify/<agent>` route + real `ClaudeCodeMCPEndpoint` + real `NotificationBroker`. The relay's `run_relay` runs in-process with `anyio.create_memory_object_stream` pairs replacing stdin/stdout. Inject an envelope via the stub endpoint → assert the fake "Claude Code stdin" receives a `SessionMessage` whose method is `notifications/claude/channel`.

This is the analogue of the responsive-inbox PR's Task 8. Proves the **full wire path** without needing a real Claude Code instance.

- [ ] **Step 1: Write the test**

Create `packages/agent-core-channel/tests/test_end_to_end_relay.py`:

```python
"""End-to-end: real bus + HTTPHost + relay coroutine driving fake stdio.

Validates the complete chain:
    bus.publish → ClaudeCodeMCPEndpoint.deliver → broker.publish →
    /notify/<agent> SSE → relay sse_pump → emit_channel_notification →
    MCP stdio write stream.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.http_host import HTTPHost
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core.endpoints.stub import StubEndpoint


@pytest.mark.asyncio
async def test_bus_arrival_reaches_relay_stdio_stream(tmp_path: Path):
    pytest.importorskip("uvicorn")

    # 1. Bus + endpoints + broker.
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    broker = NotificationBroker()
    agent_ep = ClaudeCodeMCPEndpoint(
        name="agent", mount="/mcp/agent", notify_broker=broker
    )
    stub = StubEndpoint(name="stub", description="probe")
    bus.register(EndpointSpec(endpoint=agent_ep))
    bus.register(EndpointSpec(endpoint=stub))

    # 2. HTTPHost with /notify/<agent> route.
    host = HTTPHost(
        bind_port=0,
        notify_broker=broker,
        notify_snapshot=bus.snapshot_for_agent,
    )
    host.mount(agent_ep)
    await host.start()
    daemon_url = f"http://127.0.0.1:{host.port}"

    try:
        await bus.start()

        # 3. Stand up the relay's two tasks against a fake stdio pair.
        from agent_core_channel.sse_client import iter_notify_events
        from agent_core_channel.stdio_server import emit_channel_notification

        send_to_relay, _read_relay = anyio_pair = await _make_memory_pair()
        write_from_relay, read_from_relay = await _make_memory_pair()

        async def relay_pump():
            async for summary in iter_notify_events(
                agent="agent", daemon_url=daemon_url
            ):
                await emit_channel_notification(write_from_relay, summary)

        relay_task = asyncio.create_task(relay_pump())

        try:
            # Give the relay a moment to subscribe to /notify/agent before publishing.
            await asyncio.sleep(0.3)

            # 4. Publish via stub.
            env = Envelope(
                id="e1",
                correlation_id="c1",
                from_="stub",
                to="agent",
                kind="TextMessage",
                payload=TextMessagePayload(text="hello"),
                urgency="green",
                created_at=datetime.now(timezone.utc),
            )
            await stub._handle.publish(env)

            # 5. Assert the relay emits a notifications/claude/channel within 2s.
            received = None
            for _ in range(40):
                try:
                    msg = read_from_relay.receive_nowait()
                    received = msg
                    break
                except Exception:
                    await asyncio.sleep(0.05)
            assert received is not None, "relay never emitted a notification"
            root = received.message.root
            assert root.method == "notifications/claude/channel"
            assert root.params["meta"]["count"] >= 1
            assert root.params["meta"]["endpoint"] == "agent"
        finally:
            relay_task.cancel()
            try:
                await relay_task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        await bus.stop()
        await host.stop()


async def _make_memory_pair():
    """Convenience: anyio.create_memory_object_stream returns (send, receive)."""
    import anyio

    return anyio.create_memory_object_stream(max_buffer_size=16)
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest packages/agent-core-channel/tests/test_end_to_end_relay.py -v`
Expected: 1 passed. The test exercises the full path: arrival on the bus → daemon broker publish → SSE event on the wire → relay parses → emits as MCP notification on the fake stdio.

- [ ] **Step 3: Run the full multi-package suite**

Run: `uv run pytest -v`
Expected: green across `packages/core/tests/`, `packages/credentials/tests/`, `packages/agent-core-discord/tests/`, `packages/agent-core-channel/tests/`.

- [ ] **Step 4: Lint and format**

Run: `uv run ruff check packages/agent-core-channel/ && uv run ruff format packages/agent-core-channel/`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-channel/tests/test_end_to_end_relay.py
git commit -m "test(channel): real-bus end-to-end relay integration"
```

---

## Task 9: Re-run live testbot validation (Layer 4)

**Files:**
- (No code changes; daemon config + testbot config + manual validation only.)
- Update: `~/.testbot/.mcp.json`
- Update: `docs/superpowers/plans/2026-04-29-responsive-inbox.md` (mark Task 9 PASS once validation succeeds)

This task re-runs the responsive-inbox plan's deferred Task 9 — but now with the channel relay loaded so STEP 1 (autonomous wake) is expected to PASS.

- [ ] **Step 1: Install agent-core-channel as a tool**

```bash
uv tool install --from packages/agent-core-channel agent-core-channel
```

Verify the binary is on PATH:

```bash
agent-core-channel --help
```

Expected: Typer help text printed.

- [ ] **Step 2: Update `~/.testbot/.mcp.json`**

Replace the file with:

```json
{
  "mcpServers": {
    "agent-core": {
      "type": "http",
      "url": "http://localhost:8788/mcp/agent-testbot"
    },
    "agent-core-channel": {
      "command": "agent-core-channel",
      "args": ["--agent", "agent-testbot"]
    }
  }
}
```

- [ ] **Step 3: Restart the daemon**

```bash
uv run agent-core daemon stop || true
uv run agent-core daemon start
```

Tail the log for ~5 seconds:

```bash
tail -30 ~/.agent-core/daemon.log
```

Expected: HTTPHost listening, ClaudeCodeMCPEndpoint started, no errors. The /notify/<agent> route is now live (verify by curl):

```bash
curl -N --max-time 2 http://127.0.0.1:8788/notify/agent-testbot
```

Expected: connection holds (no immediate response, no error). Curl will time out after 2s — that's the goal: the SSE stream stays open. If you immediately get an HTTP 4xx/5xx, the route isn't wired correctly; re-check Task 4's runner wiring.

- [ ] **Step 4: Launch testbot with the channel flag**

In a separate terminal, in `~/.testbot/`:

```bash
cd ~/.testbot
claude --dangerously-load-development-channels server:agent-core-channel
```

Verify in the daemon log that the relay subscribed to `/notify/agent-testbot`:

```bash
tail ~/.agent-core/daemon.log
```

Expected: a new HTTP request line for `/notify/agent-testbot` shortly after testbot launches.

- [ ] **Step 5: Drive STEP 1 in testbot — push wakes the agent**

Paste this prompt into the testbot Claude Code session:

```
STEP 1 — Push wakes the agent on a single envelope:

Send a single envelope addressed back to agent-testbot:
  Call mcp__agent-core__send with:
    to: "agent-testbot"
    kind: "TextMessage"
    payload: { "kind": "TextMessage", "text": "self-ping" }

After the send returns, do NOTHING. Just wait. If a notification arrives that
wakes you autonomously, great — call list_pending and handle the envelope.

PASS criteria:
- A new turn fires WITHOUT my prompting you.
- list_pending shows the envelope you sent yourself.
- urgency is "green".

FAIL criteria:
- No autonomous turn fired within 30 seconds.

Report PASS/FAIL with timestamp.
```

Wait. Expected: testbot's turn fires autonomously within ~1s of the send returning. If FAIL, debug:
- Daemon log shows `pushing notifications/claude/channel` — confirms daemon side.
- Daemon log shows `/notify/agent-testbot` request — confirms relay subscribed.
- testbot log (Claude Code's own debug output) should show a `notifications/claude/channel` event being processed by the channel handler.

- [ ] **Step 6: Drive STEP 2 — burst coalescing**

Paste:

```
STEP 2 — Burst arrivals coalesce into one notification:
Send 5 envelopes back-to-back via mcp__agent-core__send with
payload {"kind":"TextMessage","text":"burst-N"} for N=0..4.

You should see ONE autonomous turn fire (debounced) covering all 5.
list_pending should return 5 envelopes.

PASS:
- Exactly one autonomous turn fired (one wake).
- list_pending returns 5 envelopes.

Report PASS/FAIL.
```

- [ ] **Step 7: Drive STEP 3 — urgency ordering**

Paste:

```
STEP 3 — list_pending sorts by urgency tier:
Send three envelopes (in this order) via mcp__agent-core__send to yourself:
  1. urgency="green", text="green-msg"
  2. urgency="yellow", text="yellow-msg"
  3. urgency="red", text="red-msg"

Then call list_pending. Expected order: red → yellow → green.

PASS:
- Three envelopes returned.
- First is red-msg, second yellow-msg, third green-msg.

Report PASS/FAIL.
```

- [ ] **Step 8: Drive STEP 4 — same-sender batching**

Paste:

```
STEP 4 — list_pending(batch_window_seconds=30) groups same-sender bursts:
Send three envelopes from yourself in quick succession.

Then call list_pending(batch_window_seconds=30). Expected: 1 entry of
type="batch" containing 3 envelopes.

Then call list_pending() (default). Expected: 3 flat entries.

PASS:
- Batched call returns 1 batch group with 3 envelopes.
- Default call returns 3 flat entries.

Report PASS/FAIL.
```

- [ ] **Step 9: Drive STEP 5 — disconnect/reconnect**

Manual procedure (Jeff drives the disconnect; testbot drives the catch-up). Paste:

```
STEP 5 — Mailbox catches up on reconnect:
a) Tell me when ready. I'll close your Claude Code session.
b) While you're disconnected, I'll send a TextMessage envelope to
   agent-testbot (from a stub publish on the daemon side).
c) I'll restart your Claude Code session.
d) On reconnect, you should either get an autonomous wake (initial-wake-on-
   connect snapshot fires) OR you can call list_pending immediately and
   confirm the envelope sent during your absence is present.

PASS:
- list_pending after reconnect shows the envelope sent while you were offline.
- No data loss.

Report PASS/FAIL when I tell you the cycle is complete.
```

- [ ] **Step 10: Compile final validation report**

After STEPS 1–5 all PASS, write the report:

```
Sub-project I (Responsive Inbox) — Final Validation Report

STEP 1 (autonomous push-wake): PASS / FAIL
STEP 2 (burst coalescing): PASS / FAIL
STEP 3 (urgency ordering): PASS / FAIL
STEP 4 (same-sender batching): PASS / FAIL
STEP 5 (mailbox-authoritative on reconnect): PASS / FAIL

Daemon log: no ALTER TABLE errors, no unhandled exceptions in the
notification path.

Ship: YES / NO
```

Save it to `docs/responsive-inbox-validation-2026-04-29.md` for the PR description to reference.

- [ ] **Step 11: Update the responsive-inbox plan**

In `docs/superpowers/plans/2026-04-29-responsive-inbox.md`, find the Task 9 header and add a one-line addendum below it:

```markdown
> **2026-04-29 update:** Task 9 was deferred until the channel relay landed. With `agent-core-channel` registered in `~/.testbot/.mcp.json` and testbot launched with `--dangerously-load-development-channels server:agent-core-channel`, all 5 STEPS now PASS. See `docs/responsive-inbox-validation-2026-04-29.md` for the full report.
```

- [ ] **Step 12: Update ROADMAP**

In `docs/ROADMAP.md`, find the row for Sub-project I. Update the status to 🟢 with both PR links, and expand the Notes column to mention both packages:

```markdown
| **I** | **Responsive inbox** | 🟢 Shipped — [`responsive-inbox-design.md`](superpowers/specs/2026-04-29-responsive-inbox-design.md) (daemon side) + [`channel-relay-design.md`](superpowers/specs/2026-04-29-channel-relay-design.md) (relay side). Combined PR: ... | — | Daemon-side push pipeline + agent-side stdio channel relay (`agent-core-channel`). Plain Claude Code agents now wake autonomously on bus arrivals. |
```

- [ ] **Step 13: Commit validation evidence and roadmap updates**

```bash
git add docs/responsive-inbox-validation-2026-04-29.md docs/superpowers/plans/2026-04-29-responsive-inbox.md docs/ROADMAP.md
git commit -m "docs: sub-project I validation report + roadmap update"
```

---

## Final wrap

After Task 9 STEP 1–5 all PASS:

```bash
git push -u origin feat/responsive-inbox
gh pr create --title "feat: responsive inbox (sub-project I) — push pipeline + channel relay" --body "$(cat <<'EOF'
## Summary

Sub-project I, both halves:

**Part 1 — daemon-side push pipeline** (existing 9 commits):
- `urgency` field on `Envelope` (green|yellow|red, default green) + SQLite ALTER TABLE migration
- `list_pending` sorts by urgency tier (red → yellow → green, FIFO within tier) and accepts `batch_window_seconds` for same-sender batching
- `SessionRegistry` middleware (replaces `_SessionTracker`); keys by `mcp-session-id` header
- `_notify_mail_arrived` debounced push of `notifications/claude/channel` summaries
- `DiscordEndpoint` applies `urgencyRedRegex` rule on inbound TextMessage

**Part 2 — agent-side channel relay** (new commits):
- `NotificationBroker` for per-agent fan-out
- `/notify/<agent>` SSE route on the existing HTTPHost + initial-wake-on-connect
- New package `agent-core-channel`: stdio MCP server declaring `experimental.claude/channel`, SSE consumer, Typer CLI
- Re-runs live testbot validation: STEP 1 (autonomous wake) flips from FAIL → PASS

## Test plan
- [ ] All ~40 unit + integration tests pass in CI
- [ ] Real-bus end-to-end relay test (Layer 3) passes
- [ ] Live testbot validation (Layer 4): all 5 STEPS PASS
- [ ] Daemon migrates `~/.agent-core/bus.sqlite` cleanly on first boot

## Limitations / future work
- Plain Claude Code requires `--dangerously-load-development-channels server:agent-core-channel` to load the channel — when Anthropic stabilizes the channels API, the launch flag changes (relay code stays the same).
- Single relay per agent in v1; multi-relay fan-out is broker-shaped but not validated.
- Loopback bind only (inherited from HTTPHost).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
