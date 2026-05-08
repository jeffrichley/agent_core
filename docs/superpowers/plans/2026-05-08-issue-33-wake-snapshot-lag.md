# Issue #33 — Wake-builder snapshot lag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the wake-vs-list_pending snapshot drift by stripping queue-state metadata from wake notifications and moving it to `list_pending`'s response, computed atomically alongside the items it describes.

**Architecture:** Wake notifications become minimal: `meta` carries only `endpoint` and `fired_at`. `list_pending` returns the wrapped shape `{meta: {count, urgency_max, urgency_counts, by_sender, endpoint, fetched_at}, items: [...]}`. `Bus.snapshot_for_agent` follows the same minimal wake contract for uniformity. Race-free by construction — meta and items come from the same atomic read of `self._pending`.

**Tech Stack:** Python 3.12, asyncio, FastMCP, pytest, ruff. Branch: `fix/issue-33-wake-snapshot-lag`.

**Spec:** `docs/superpowers/specs/2026-05-08-issue-33-wake-snapshot-lag-design.md`

---

## Task 1: Wrap `list_pending` response shape

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — `_call_list_pending` (lines ~389-440), `_build_summary` (lines ~558-608)
- Test (new): `packages/core/tests/test_list_pending_meta_invariants.py`
- Test (modify): `packages/core/tests/test_claude_code_mcp.py`
- Test (modify): `packages/core/tests/test_claude_code_mcp_urgency_ordering.py`
- Test (modify): `packages/core/tests/test_claude_code_mcp_batching.py`

### Steps

- [ ] **Step 1: Create the failing invariant test**

Create `packages/core/tests/test_list_pending_meta_invariants.py`:

```python
"""Property tests: list_pending's meta and items must agree by construction.

The bug behind issue #33 was that wake-meta could disagree with list_pending.
The fix moves meta into list_pending's response, computed from the same
atomic read of self._pending. These tests assert that contract holds for
varied inbox states.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_core.bus.core import Bus
from agent_core.bus.envelopes import Envelope
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


def _envelope(idx: int, urgency: str = "green", from_: str = "alice", kind: str = "TextMessage") -> Envelope:
    return Envelope(
        from_=from_,
        to="agent",
        kind=kind,
        urgency=urgency,  # type: ignore[arg-type]
        payload={"text": f"msg{idx}"},
    )


@pytest.mark.parametrize(
    "envelopes, batch_window",
    [
        ([], 0),
        ([_envelope(0, "green")], 0),
        ([_envelope(0, "red"), _envelope(1, "yellow"), _envelope(2, "green")], 0),
        ([_envelope(0, "red"), _envelope(1, "red"), _envelope(2, "green")], 0),
        ([_envelope(i, "green", from_="alice") for i in range(3)], 30),
        (
            [
                _envelope(0, "yellow", from_="alice"),
                _envelope(1, "yellow", from_="alice"),
                _envelope(2, "green", from_="bob"),
            ],
            30,
        ),
    ],
)
def test_list_pending_meta_matches_items(envelopes: list[Envelope], batch_window: int) -> None:
    """meta.count, urgency_max, urgency_counts, by_sender all reconstruct from items."""

    async def _run() -> dict:
        bus = Bus()
        endpoint = ClaudeCodeMCPEndpoint(name="agent")
        bus.register_endpoint(endpoint)
        for env in envelopes:
            endpoint.queue_for_pickup(env)
        return await endpoint._call_list_pending(batch_window_seconds=batch_window)

    result = asyncio.run(_run())

    assert set(result.keys()) == {"meta", "items"}
    meta = result["meta"]
    items = result["items"]

    assert meta["count"] == len(envelopes)
    assert meta["endpoint"] == "agent"
    assert "fetched_at" in meta

    # urgency_max
    if not envelopes:
        assert meta["urgency_max"] == "green"
    else:
        order = {"red": 0, "yellow": 1, "green": 2}
        expected_max = min((e.urgency for e in envelopes), key=lambda u: order[u])
        assert meta["urgency_max"] == expected_max

    # urgency_counts
    counts = {"red": 0, "yellow": 0, "green": 0}
    for e in envelopes:
        counts[e.urgency] += 1
    assert meta["urgency_counts"] == counts

    # by_sender
    by_sender_index = {entry["from"]: entry for entry in meta["by_sender"]}
    sender_counts: dict[str, int] = {}
    for e in envelopes:
        sender_counts[e.from_] = sender_counts.get(e.from_, 0) + 1
    for sender, expected_count in sender_counts.items():
        assert by_sender_index[sender]["count"] == expected_count
```

- [ ] **Step 2: Run the failing test**

Run: `uv run pytest packages/core/tests/test_list_pending_meta_invariants.py -v`
Expected: FAIL (current `_call_list_pending` returns a flat list, not `{meta, items}`).

- [ ] **Step 3: Refactor `_build_summary` and update `_call_list_pending`**

In `packages/core/src/agent_core/endpoints/claude_code_mcp.py`, replace the existing `_build_summary` method (lines ~558-608) and `_call_list_pending` method (lines ~389-440) with:

```python
def _compute_meta(self) -> dict:
    """Compute aggregate metadata from the current pending queue.

    Reads self._pending atomically (no awaits). Used by _call_list_pending
    so meta and items come from the same read — eliminates the wake-vs-
    list_pending drift that caused issue #33.
    """
    pending = list(self._pending)
    count = len(pending)
    # urgency counts
    urg_counts = Counter(e.urgency for e in pending)
    urg_full: dict[Literal["red", "yellow", "green"], int] = {}
    for tier in self._URGENCY_ORDER:
        urgency_key = cast(Literal["red", "yellow", "green"], tier)
        urg_full[urgency_key] = int(urg_counts.get(urgency_key, 0))
    # urgency_max — highest tier present (default "green" when count == 0)
    urgency_max = "green"
    for tier in self._URGENCY_ORDER:
        urgency_key = cast(Literal["red", "yellow", "green"], tier)
        if urg_full[urgency_key] > 0:
            urgency_max = tier
            break
    # by_sender
    sender_index: dict[str, dict] = {}
    for env in pending:
        entry = sender_index.setdefault(env.from_, {"from": env.from_, "count": 0, "kinds": []})
        entry["count"] += 1
        if env.kind not in entry["kinds"]:
            entry["kinds"].append(env.kind)
    by_sender = list(sender_index.values())
    return {
        "count": count,
        "urgency_max": urgency_max,
        "urgency_counts": urg_full,
        "by_sender": by_sender,
        "endpoint": self.name,
        "fetched_at": datetime.now(UTC).isoformat(),
    }

def _build_wake_summary(self) -> dict:
    """Minimal wake notification — pure 'go look' signal.

    Carries no queue-state metadata. The agent calls list_pending for
    authoritative count/urgency_max/by_sender, computed atomically with
    the items list. See issue #33.
    """
    return {
        "content": f"INBOX: pending ({self.name})",
        "meta": {
            "endpoint": self.name,
            "fired_at": datetime.now(UTC).isoformat(),
        },
    }
```

Then update `_call_list_pending` to return the wrapped shape. Replace its body (lines ~389-440) with:

```python
async def _call_list_pending(self, batch_window_seconds: int = 0) -> dict:
    """Mailbox view sorted by urgency, optionally batched by sender.

    Returns {"meta": {...}, "items": [...]}. meta carries count, urgency_max,
    urgency_counts, by_sender, endpoint, fetched_at. items is the flat list
    of envelope dicts (when batch_window_seconds == 0) or batched/single
    entries (when > 0). meta and items are computed from the same atomic
    read of self._pending — see issue #33.
    """
    meta = self._compute_meta()
    sorted_pending = sorted(
        self._pending,
        key=lambda e: (self._URGENCY_RANK[e.urgency], e.created_at),
    )
    if batch_window_seconds <= 0:
        items: list[dict] = [self._envelope_to_dict(env) for env in sorted_pending]
        return {"meta": meta, "items": items}

    window = timedelta(seconds=batch_window_seconds)
    groups: list[dict] = []
    i = 0
    while i < len(sorted_pending):
        head = sorted_pending[i]
        j = i + 1
        run = [head]
        while j < len(sorted_pending):
            cand = sorted_pending[j]
            # ... keep existing batching logic ...
```

**Note for the implementer:** keep the rest of `_call_list_pending`'s batching logic intact (lines ~405-440 in the current file). Only change is: prepend the `meta = self._compute_meta()` call at the top and wrap the returned list as `{"meta": meta, "items": <list>}` in both branches (flat and batched).

The existing `_build_summary(urgency_floor=...)` method's body gets replaced with a thin shim — Task 2 will remove the shim entirely. The wake-firing path in `_fire_after_debounce` still references `_build_summary` until Task 2; for this task, leave that reference intact and have `_build_summary` delegate to the new `_build_wake_summary`:

```python
def _build_summary(
    self, urgency_floor: Literal["red", "yellow", "green"] | None = None
) -> dict:
    """Temporary shim — Task 2 will remove this in favor of _build_wake_summary().

    For now, return the minimal wake shape so callers (debounced wake path,
    snapshot()) get the new contract immediately. urgency_floor is ignored
    (the new wake carries no urgency).
    """
    return self._build_wake_summary()
```

- [ ] **Step 4: Update existing list_pending tests**

In `packages/core/tests/test_claude_code_mcp.py`, find every test asserting on `list_pending`'s return as a flat list. Update assertions to access `result["items"]` instead of indexing `result` directly. Where tests assert on length, change `len(result)` to `len(result["items"])` or `result["meta"]["count"]`.

In `packages/core/tests/test_claude_code_mcp_urgency_ordering.py`, the urgency-ordering assertions inspect the order of returned envelopes. Change to inspect `result["items"]`.

In `packages/core/tests/test_claude_code_mcp_batching.py`, batching tests assert on `{"type": "single"|"batch", ...}` entries. Change to inspect `result["items"]`.

The implementer should grep each file for `list_pending` calls and adjust. Pattern:

```python
# Before:
result = await endpoint._call_list_pending()
assert len(result) == 3
assert result[0]["urgency"] == "red"

# After:
result = await endpoint._call_list_pending()
assert result["meta"]["count"] == 3
assert len(result["items"]) == 3
assert result["items"][0]["urgency"] == "red"
```

- [ ] **Step 5: Run the targeted test suite**

Run: `uv run pytest packages/core/tests/test_list_pending_meta_invariants.py packages/core/tests/test_claude_code_mcp.py packages/core/tests/test_claude_code_mcp_urgency_ordering.py packages/core/tests/test_claude_code_mcp_batching.py -v`
Expected: PASS for all.

- [ ] **Step 6: Run the full suite to catch any indirect breakage**

Run: `uv run pytest`
Expected: PASS for everything that was passing before. (Some wake/snapshot tests may still be passing because of the temporary `_build_summary` shim — that's expected; Task 2 will tighten them.)

- [ ] **Step 7: Commit**

```bash
git checkout -b fix/issue-33-wake-snapshot-lag
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py \
        packages/core/tests/test_list_pending_meta_invariants.py \
        packages/core/tests/test_claude_code_mcp.py \
        packages/core/tests/test_claude_code_mcp_urgency_ordering.py \
        packages/core/tests/test_claude_code_mcp_batching.py
git commit -m "fix(claude_code_mcp): wrap list_pending response with atomic meta (#33)

Returns {meta, items} where meta carries count, urgency_max,
urgency_counts, by_sender, endpoint, fetched_at — computed from the
same atomic read of self._pending as items. Eliminates the source
of the wake-vs-list_pending drift Pepper has been catching.

_build_summary is temporarily shimmed to delegate to a new
_build_wake_summary helper; Task 2 removes the shim and tightens
the wake notification path."
```

---

## Task 2: Minimize wake notification + drop `urgency_floor` plumbing

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — `_fire_after_debounce` (lines ~675-700), `_notify_mail_arrived` (lines ~646-674), debounce-state fields (line ~190ish), remove `_build_summary` shim
- Test (new): `packages/core/tests/test_wake_meta_drift_guard.py`
- Test (modify): `packages/core/tests/test_notify_mail_arrived.py`
- Test (modify): `packages/core/tests/test_bus_daemon_push_integration.py`

### Steps

- [ ] **Step 1: Write the failing drift-guard test**

Create `packages/core/tests/test_wake_meta_drift_guard.py`:

```python
"""Drift-guard: wake notifications carry only endpoint + fired_at in meta.

If anyone re-introduces count, urgency_max, by_sender, or urgency_counts
to the wake notification's meta, this test fires. The fix for issue #33
relies on the wake being purely a 'go look' signal — re-adding queue-state
fields would re-introduce the original race.
"""

from __future__ import annotations

import asyncio

from agent_core.bus.core import Bus
from agent_core.bus.envelopes import Envelope
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint

ALLOWED_WAKE_META_KEYS = frozenset({"endpoint", "fired_at"})


def test_wake_summary_meta_keys_are_minimal() -> None:
    """The wake-only summary builder emits exactly the allowed keys."""
    endpoint = ClaudeCodeMCPEndpoint(name="agent")
    summary = endpoint._build_wake_summary()
    assert set(summary["meta"].keys()) == ALLOWED_WAKE_META_KEYS


def test_wake_content_is_fixed_string() -> None:
    """Wake content is the fixed non-lying string — not a stale snapshot."""
    endpoint = ClaudeCodeMCPEndpoint(name="agent")
    # Even if pending is non-empty, content does not reference counts.
    endpoint.queue_for_pickup(
        Envelope(from_="x", to="agent", kind="TextMessage", urgency="green", payload={})
    )
    endpoint.queue_for_pickup(
        Envelope(from_="y", to="agent", kind="TextMessage", urgency="red", payload={})
    )
    summary = endpoint._build_wake_summary()
    assert summary["content"] == "INBOX: pending (agent)"


def test_published_wake_has_no_queue_state_meta() -> None:
    """Verify the actual debounce-fire path emits the minimal shape."""

    async def _run() -> dict:
        published: list[dict] = []

        class CaptureBroker:
            async def publish(self, agent: str, event: dict) -> None:
                published.append(event)

        bus = Bus()
        endpoint = ClaudeCodeMCPEndpoint(name="agent")
        endpoint._notify_broker = CaptureBroker()
        bus.register_endpoint(endpoint)
        env = Envelope(from_="x", to="agent", kind="TextMessage", urgency="green", payload={})
        endpoint.queue_for_pickup(env)
        await endpoint._notify_mail_arrived(urgency="green")
        # Wait long enough for the debounce to fire (green = 1.0s).
        await asyncio.sleep(1.2)
        return published[-1] if published else {}

    event = asyncio.run(_run())
    assert event, "expected at least one published wake event"
    assert set(event["meta"].keys()) == ALLOWED_WAKE_META_KEYS
```

- [ ] **Step 2: Run the failing test**

Run: `uv run pytest packages/core/tests/test_wake_meta_drift_guard.py -v`
Expected: PASS for the first two unit tests (they call `_build_wake_summary` directly, which exists from Task 1). The third test (`test_published_wake_has_no_queue_state_meta`) may PASS already due to Task 1's shim — that's fine; this task tightens the implementation behind it.

- [ ] **Step 3: Drop `urgency_floor` plumbing and the `_build_summary` shim**

In `packages/core/src/agent_core/endpoints/claude_code_mcp.py`:

(a) Remove the `_debounce_urgency_floor` field initialization in `__init__` (search for `self._debounce_urgency_floor` and delete the assignment).

(b) Update `_notify_mail_arrived` to remove all `_debounce_urgency_floor` reads/writes. The new version:

```python
async def _notify_mail_arrived(self, urgency: str = "green") -> None:
    """Schedule a debounced push announcing inbox activity.

    Called by deliver() on each arrival. Red arrivals wake promptly,
    yellow waits briefly, green waits long enough to collect bursts.
    A more urgent arrival shortens a pending timer; less urgent
    arrivals never delay an already-pending urgent push.

    The wake notification itself carries no queue-state — see issue #33.
    The urgency parameter only affects debounce timing, not wake content.
    """
    delay = self._notify_debounce_seconds_by_urgency.get(urgency, 1.0)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + delay

    if self._debounce_task is not None and not self._debounce_task.done():
        if self._debounce_deadline is not None and deadline >= self._debounce_deadline:
            return
        self._debounce_task.cancel()
    self._debounce_deadline = deadline
    self._debounce_task = asyncio.create_task(self._fire_after_debounce(delay))
```

(c) Update `_fire_after_debounce` to call `_build_wake_summary()` instead of `_build_summary(urgency_floor=...)`:

```python
async def _fire_after_debounce(self, delay: float) -> None:
    task = asyncio.current_task()
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    if task is self._debounce_task:
        self._debounce_deadline = None
    summary = self._build_wake_summary()

    # Always publish to the broker so /notify/<agent> subscribers
    # (the channel relay) wake the agent regardless of whether the
    # daemon's HTTP MCP session is currently captured.
    if self._notify_broker is not None:
        try:
            await self._notify_broker.publish(self.name, summary)
        except Exception:
            log.warning("endpoint '%s': broker publish failed", self.name, exc_info=True)

    # ... keep the existing in-session push logic below this line ...
```

**Note for the implementer:** keep everything below the broker-publish block in `_fire_after_debounce` intact (the in-session push to `self._sessions`).

(d) Delete the temporary `_build_summary` shim added in Task 1. The `snapshot()` method (used by `Bus.snapshot_for_agent`) currently calls `self._build_summary()`; update it to call `self._build_wake_summary()` instead. Task 3 expands on snapshot semantics — for now this one-line change keeps tests green.

```python
def snapshot(self) -> dict:
    """Public wrapper used by Bus.snapshot_for_agent.

    Emits the same minimal wake shape as a regular debounced wake (see
    issue #33) — one contract for everything wake-shaped.
    """
    return self._build_wake_summary()
```

- [ ] **Step 4: Update `test_notify_mail_arrived.py`**

In `packages/core/tests/test_notify_mail_arrived.py`, find every assertion that inspects `count`, `urgency_max`, `urgency_counts`, or `by_sender` in the wake notification's meta. Update them either to:
- assert those keys are NOT present (when checking the wake), OR
- delete the assertion entirely (when the test was simply verifying the value).

Pattern:

```python
# Before:
assert summary["meta"]["count"] == 3
assert summary["meta"]["urgency_max"] == "red"

# After:
assert "count" not in summary["meta"]
assert "urgency_max" not in summary["meta"]
# Or simply:
assert set(summary["meta"].keys()) == {"endpoint", "fired_at"}
```

Tests verifying wake-fire timing, debounce coalescing, cancel behavior, and broker publish should keep working with no changes.

- [ ] **Step 5: Update `test_bus_daemon_push_integration.py`**

Same pattern: drop assertions on count/urgency_max/by_sender in the pushed wake notifications. Keep assertions on push timing, session targeting, and event delivery.

- [ ] **Step 6: Run the targeted test suite**

Run: `uv run pytest packages/core/tests/test_wake_meta_drift_guard.py packages/core/tests/test_notify_mail_arrived.py packages/core/tests/test_bus_daemon_push_integration.py -v`
Expected: PASS for all.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS for everything except possibly `test_bus_snapshot_for_agent.py` (handled in Task 3) and `test_stdio_server.py` / `test_end_to_end_relay.py` if they assert on wake meta (handled in Task 4).

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py \
        packages/core/tests/test_wake_meta_drift_guard.py \
        packages/core/tests/test_notify_mail_arrived.py \
        packages/core/tests/test_bus_daemon_push_integration.py
git commit -m "fix(claude_code_mcp): minimize wake notification meta (#33)

Wake notifications now carry only endpoint + fired_at. count,
urgency_max, urgency_counts, by_sender are gone — they were the
race-prone fields. Agents read authoritative state via list_pending
(see Task 1, which already wrapped its return shape).

Drops _debounce_urgency_floor plumbing — the only consumer was the
wake's urgency_max field, which no longer exists. Adds a drift-guard
test asserting wake meta keys remain minimal."
```

---

## Task 3: `Bus.snapshot_for_agent` follows the minimal wake shape

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — `snapshot()` (already updated in Task 2 step 3d, this task adds tests + verifies)
- Test (modify): `packages/core/tests/test_bus_snapshot_for_agent.py`

### Steps

- [ ] **Step 1: Update test_bus_snapshot_for_agent.py**

In `packages/core/tests/test_bus_snapshot_for_agent.py`, find every assertion on the snapshot's `meta` fields. Update them to assert the new minimal shape:

```python
# Before:
snapshot = bus.snapshot_for_agent("agent")
assert snapshot["meta"]["count"] == 2
assert snapshot["meta"]["urgency_max"] == "yellow"

# After:
snapshot = bus.snapshot_for_agent("agent")
assert set(snapshot["meta"].keys()) == {"endpoint", "fired_at"}
assert snapshot["meta"]["endpoint"] == "agent"
```

Tests verifying that the snapshot fires, that it targets the right agent, and that it triggers a wake should keep working unchanged. Only the meta-shape assertions move.

If a test was specifically verifying that `urgency_max` flowed through correctly from envelopes, replace it with a test that calls `list_pending` after the snapshot and asserts on `meta.urgency_max` there — the contract has moved, not gone.

- [ ] **Step 2: Run the snapshot tests**

Run: `uv run pytest packages/core/tests/test_bus_snapshot_for_agent.py -v`
Expected: PASS.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest`
Expected: PASS for everything except possibly `test_stdio_server.py` / `test_end_to_end_relay.py` (handled in Task 4).

- [ ] **Step 4: Commit**

```bash
git add packages/core/tests/test_bus_snapshot_for_agent.py
git commit -m "fix(snapshot_for_agent): align with minimal wake contract (#33)

Bus.snapshot_for_agent emits the same minimal {endpoint, fired_at}
meta as a regular debounced wake. One wake contract everywhere —
agents have a single parser path. Future synthetic wake sources
inherit it.

Source change for snapshot() landed alongside the debounce-path
update in the previous commit; this commit covers the test
assertions."
```

---

## Task 4: Regression test for the original bug shape

**Files:**
- Test (modify): `packages/core/tests/test_notify_mail_arrived.py` — add the deterministic-sequence regression test

### Steps

- [ ] **Step 1: Add the regression test**

Append to `packages/core/tests/test_notify_mail_arrived.py`:

```python
import asyncio

import pytest

from agent_core.bus.core import Bus
from agent_core.bus.envelopes import Envelope
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


@pytest.mark.asyncio
async def test_wake_and_list_pending_disagreement_eliminated() -> None:
    """Issue #33 regression: agent drains envelopes between wake-fire and consumption.

    Reproduces the failure shape Pepper saw: count=3 reported, only 1 actually
    pending. Under the new contract, the wake carries no count/urgency_max,
    and list_pending's meta agrees with its own items by construction.
    """
    published: list[dict] = []

    class CaptureBroker:
        async def publish(self, agent: str, event: dict) -> None:
            published.append(event)

    bus = Bus()
    endpoint = ClaudeCodeMCPEndpoint(name="agent")
    endpoint._notify_broker = CaptureBroker()
    bus.register_endpoint(endpoint)

    # Queue 3 envelopes; debounce window starts on first arrival.
    e0 = Envelope(from_="alice", to="agent", kind="TextMessage", urgency="red", payload={"text": "0"})
    e1 = Envelope(from_="alice", to="agent", kind="TextMessage", urgency="red", payload={"text": "1"})
    e2 = Envelope(from_="alice", to="agent", kind="TextMessage", urgency="green", payload={"text": "2"})
    endpoint.queue_for_pickup(e0)
    endpoint.queue_for_pickup(e1)
    endpoint.queue_for_pickup(e2)
    await endpoint._notify_mail_arrived(urgency="red")  # red = 0.05s debounce

    # Simulate the agent draining the two red envelopes mid-debounce
    # (this is what Pepper does between wakes — finishes processing the
    # prior batch). Drain happens BEFORE the debounce fires.
    endpoint._pending = [e for e in endpoint._pending if e.id not in {e0.id, e1.id}]

    # Let the debounce fire.
    await asyncio.sleep(0.2)

    # Wake notification: must be the minimal shape, with no stale count.
    assert published, "expected wake to fire"
    wake = published[-1]
    assert set(wake["meta"].keys()) == {"endpoint", "fired_at"}
    assert wake["content"] == "INBOX: pending (agent)"

    # list_pending: meta agrees with the actual remaining envelope.
    result = await endpoint._call_list_pending()
    assert result["meta"]["count"] == 1
    assert result["meta"]["urgency_max"] == "green"
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == e2.id
```

- [ ] **Step 2: Run the regression test**

Run: `uv run pytest packages/core/tests/test_notify_mail_arrived.py::test_wake_and_list_pending_disagreement_eliminated -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/test_notify_mail_arrived.py
git commit -m "test(claude_code_mcp): regression test for wake/list_pending drift (#33)

Reproduces the shape Pepper caught — agent drains envelopes between
wake-fire and consumption. Under the new contract:
- wake carries no count/urgency_max, so it cannot lie
- list_pending's meta is computed from the same atomic read as items,
  so meta.count always matches len(items)

This test would have failed under the pre-fix code path (it fixates
on the disagreement between the wake's snapshot and list_pending's
later read). Under the fix it passes by construction."
```

---

## Task 5: Update contract documentation strings

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — `instructions=` kwarg in `__init__` (lines ~171-185)
- Modify: `packages/agent-core-channel/src/agent_core_channel/stdio_server.py` — `_RELAY_INSTRUCTIONS` (lines ~33-44)
- Modify (if relevant): `packages/agent-core-channel/README.md`
- Modify (if relevant): `docs/cutover/notification-surfaces.md`
- Test (modify): `packages/agent-core-channel/tests/test_stdio_server.py` (assertion-shape updates if any)
- Test (modify): `packages/agent-core-channel/tests/test_end_to_end_relay.py` (assertion-shape updates if any)

### Steps

- [ ] **Step 1: Rewrite the FastMCP `instructions=` kwarg in `claude_code_mcp.py`**

Replace lines ~171-185 (the `instructions=` block in `__init__`) with:

```python
instructions=(
    f"You are agent '{name}'. The bus pushes you notifications with method "
    '"notifications/claude/channel" when envelopes arrive in your mailbox. '
    'Each notification\'s params contain "content" (a fixed string of the form '
    '"INBOX: pending (<endpoint>)") and "meta" (endpoint, fired_at). The '
    "wake is purely a 'go look' signal — it carries no queue-state. "
    "On receipt: call list_pending() to read the actual envelopes (set "
    "batch_window_seconds=30 to fold human-paced bursts from the same "
    'sender). list_pending returns {"meta": {count, urgency_max, '
    "urgency_counts, by_sender, endpoint, fetched_at}, \"items\": [...]}; "
    "meta is the authoritative aggregate, computed atomically with items. "
    "Process each item, then call handle(envelope_id) on each to ack and "
    "remove from the queue. Send replies via the send tool. "
    "Routine delivery Acknowledgments for your own recent outbounds may "
    "be auto-cleared without a wake; you are still notified for failures, "
    "urgent acks, other envelope kinds, and missing-ack timeouts."
),
```

- [ ] **Step 2: Rewrite `_RELAY_INSTRUCTIONS` in `stdio_server.py`**

Replace lines ~33-44 with:

```python
_RELAY_INSTRUCTIONS = (
    "Inbox wake notifications for an agent-core agent. "
    'Messages arrive as JSON-RPC notifications with method "notifications/claude/channel". '
    'The params object has "content" (a fixed "INBOX: pending (<endpoint>)" string) '
    'and "meta" (endpoint, fired_at). The wake is a pure "go look" signal — it '
    "carries no queue-state. "
    "When such a notification arrives, treat it as a wake signal: "
    "call mcp__agent-core__list_pending to fetch the authoritative "
    '{"meta": {count, urgency_max, urgency_counts, by_sender, endpoint, '
    'fetched_at}, "items": [...]} response. Higher urgency tiers '
    "(red > yellow > green) should be addressed first — read meta.urgency_max "
    "and the per-item urgency. Respond via mcp__agent-core__send when "
    "appropriate. Do not wait for user input - the notification IS the prompt."
)
```

- [ ] **Step 3: Check `packages/agent-core-channel/README.md`**

Run: `grep -n "urgency_max\|by_sender\|count.*pending" packages/agent-core-channel/README.md` (use the Grep tool, not bash grep).
Expected: if matches found, update the prose to reflect the new contract. If no matches, skip.

- [ ] **Step 4: Check `docs/cutover/notification-surfaces.md`**

Run: `grep -n "urgency_max\|by_sender\|wake meta" docs/cutover/notification-surfaces.md` (use the Grep tool).
Expected: if matches found describing the wake's meta fields, update the prose. The current sweep showed no specific field references, but check the prose around line 26+ for any "summary contains count..." phrasing that still implies the old shape and update it.

- [ ] **Step 5: Update relay tests**

In `packages/agent-core-channel/tests/test_stdio_server.py`, find any assertions on:
- `_RELAY_INSTRUCTIONS` content (likely a substring check) — update to match new wording.
- Wake notification meta-field shape — drop `count`/`urgency_max`/`by_sender`.

In `packages/agent-core-channel/tests/test_end_to_end_relay.py`, find any assertions on:
- `list_pending` return shape — update to inspect `result["items"]` and `result["meta"]`.
- Wake notification meta — same as above.

- [ ] **Step 6: Run the relay tests**

Run: `uv run pytest packages/agent-core-channel/tests/test_stdio_server.py packages/agent-core-channel/tests/test_end_to_end_relay.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS — should be 597+ existing tests plus the 3 new tests files (~10 new tests total).

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py \
        packages/agent-core-channel/src/agent_core_channel/stdio_server.py \
        packages/agent-core-channel/tests/test_stdio_server.py \
        packages/agent-core-channel/tests/test_end_to_end_relay.py
# Also add README/notification-surfaces.md if they were updated.
git commit -m "docs(claude_code_mcp,channel): sync wake-contract instructions (#33)

Both the FastMCP server's instructions= kwarg and the channel relay's
_RELAY_INSTRUCTIONS now describe the new wake shape: minimal meta
(endpoint + fired_at), pure 'go look' signal, list_pending carries
authoritative aggregate metadata in its wrapped {meta, items}
response."
```

---

## Task 6: Final verification + PR

**Files:** none modified.

### Steps

- [ ] **Step 1: Full test suite**

Run: `uv run pytest`
Expected: All tests pass. New count should be 597 (pre-existing) + ~10 new (invariant tests parametrized + drift-guard + regression) = ~607 passing.

- [ ] **Step 2: Lint**

Run: `uv run ruff check .`
Expected: clean.

- [ ] **Step 3: Type-check (if configured)**

Run: `uv run mypy packages/core/src packages/agent-core-channel/src` (skip if mypy isn't configured for the project).
Expected: clean.

- [ ] **Step 4: Verify branch state**

Run: `git log --oneline main..HEAD`
Expected: 5 commits on `fix/issue-33-wake-snapshot-lag` (one per Task 1-5).

- [ ] **Step 5: Push and open PR**

```bash
git push -u origin fix/issue-33-wake-snapshot-lag
gh pr create --title "fix: wake-builder snapshot lag (#33)" --body "$(cat <<'EOF'
## Summary

Fixes #33. Wake notifications and `list_pending` no longer drift.

- Wake notification meta minimized to `{endpoint, fired_at}`.
- `list_pending` returns `{meta, items}` where meta carries `count`, `urgency_max`, `urgency_counts`, `by_sender`, computed atomically from the same `self._pending` read as items.
- `Bus.snapshot_for_agent` follows the same minimal wake contract.
- Drops `_debounce_urgency_floor` plumbing (its only consumer was the now-removed wake `urgency_max`).

Approved by consumer (Pepper) for Option B (hybrid). Cutover validated on testbot first per the durable hands-off rule.

## Test plan

- [ ] `uv run pytest` — all green
- [ ] `uv run ruff check .` — clean
- [ ] Validate on testbot: send mixed-urgency burst, confirm wake content is fixed string and list_pending meta matches items
- [ ] Then restart Pepper to pick up new contract

## Spec

`docs/superpowers/specs/2026-05-08-issue-33-wake-snapshot-lag-design.md`
EOF
)"
```

Expected: PR opens cleanly. Return the PR URL.

---

## Self-review checklist (run by orchestrator after plan-write)

- [x] Spec coverage: Task 1 covers list_pending wrap; Task 2 covers wake minimization; Task 3 covers snapshot_for_agent; Task 4 covers regression test; Task 5 covers contract documentation; Task 6 covers verification.
- [x] Placeholder scan: no TBD/TODO/"add appropriate"/etc.
- [x] Type consistency: `_build_wake_summary`, `_compute_meta`, `_call_list_pending` signatures used uniformly across tasks.
- [x] Conventional commits: `fix(scope):`, `test(scope):`, `docs(scope):` per repo style. No `Co-Authored-By` trailer (matches repo convention verified via `git log -3`).
- [x] Branch + PR convention: `fix/issue-33-wake-snapshot-lag` per the roadmap doc; `Closes #33` will auto-close via the PR title `(#33)`.
