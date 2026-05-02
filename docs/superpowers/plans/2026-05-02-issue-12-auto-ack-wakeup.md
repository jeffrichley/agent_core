# Issue 12 — Auto-ack wakeup suppression — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-handle routine green `Acknowledgment` envelopes for recent agent outbounds in `ClaudeCodeMCPEndpoint` without channel wake, while waking on failures, non-acks, and missing-ack timeouts, per [`2026-05-02-issue-12-auto-ack-wakeup-design.md`](../specs/2026-05-02-issue-12-auto-ack-wakeup-design.md).

**Architecture:** In-memory **recent-outbound registry** (id → registered monotonic time) filled when the MCP `send` tool successfully publishes; **`deliver()`** classifies inbound envelopes before `_notify_mail_arrived`; eligible green acks call `BusHandle.ack(ack_id)` and skip `_pending` + notify. **Per-outbound `asyncio.Task`** timers (or `call_later`) implement missing-ack; **`stop()`** cancels timers and clears registry.

**Tech stack:** Python 3.12, asyncio, existing `Envelope` / `AcknowledgmentPayload`, pytest-asyncio, YAML `params` for `builtin.claude_code_mcp` via `build_bus_from_config`.

---

## File map

| File | Responsibility |
|------|------------------|
| `packages/core/src/agent_core/endpoints/claude_code_mcp.py` | Registry, timers, `deliver()` / `send()` / `stop()`, MCP instructions |
| `packages/core/tests/test_claude_code_mcp_auto_ack.py` | New focused tests (keep `test_notify_mail_arrived.py` stable unless assertions need tiny updates) |
| `packages/core/tests/test_runner_http_host.py` | Assert new YAML `params` keys construct without `BusBootError` |
| `packages/core/src/agent_core/bus/runner.py` | Optional: merge `bus.*` defaults into claude MCP params (only if you want global defaults without repeating YAML) |
| `docs/examples/*.yaml` or bus example YAML | Document new optional `params` for operators |

---

### Task 1: Failing test — auto-handled green ack emits no channel notification

**Files:**
- Create: `packages/core/tests/test_claude_code_mcp_auto_ack.py`
- Modify: (none yet)

- [ ] **Step 1: Write the failing test**

Use the same `_RecordingSession` pattern as `test_notify_mail_arrived.py`. Stub `BusHandle` with an async `ack` that records ids.

```python
"""Tests for issue #12: auto-handle routine green acks without channel wake."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from agent_core.bus.envelope import AcknowledgmentPayload, Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _RecordingSession:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send_message(self, message) -> None:
        from mcp.shared.session import SessionMessage

        assert isinstance(message, SessionMessage)
        self.sent.append(message)


class _StubHandle:
    def __init__(self) -> None:
        self.acked: list[str] = []

    async def ack(self, envelope_id: str) -> None:
        self.acked.append(envelope_id)

    async def publish(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        raise RuntimeError("not used in this test")


def _speed_up_debounce(ep: ClaudeCodeMCPEndpoint) -> None:
    ep._notify_debounce_seconds_by_urgency = {"red": 0.01, "yellow": 0.03, "green": 0.05}


@pytest.mark.asyncio
async def test_green_ack_matching_recent_outbound_skips_channel_push():
    """Routine green Ack for a tracked outbound must not call _notify_mail_arrived path."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    _speed_up_debounce(ep)
    stub = _StubHandle()
    from typing import cast
    from agent_core.bus.handle import BusHandle

    await ep.start(cast(BusHandle, stub))

    outbound_id = "outbound-1"
    # Simulate send() having registered this id (Task 2 will wire real registration).
    ep._recent_outbound_ids[outbound_id] = asyncio.get_running_loop().time()

    session = _RecordingSession()
    ep._register_session(session)

    ack = Envelope(
        id="ack-1",
        correlation_id="c1",
        in_reply_to=outbound_id,
        from_="discord",
        to="agent",
        kind="Acknowledgment",
        payload=AcknowledgmentPayload(of=outbound_id, note='{"ok":true}'),
        urgency="green",
        created_at=datetime.now(UTC),
    )
    await ep.deliver(ack)

    await asyncio.sleep(0.15)
    assert session.sent == [], "auto-handled ack must not push notifications/claude/channel"
    assert "ack-1" in stub.acked
    assert all(e.id != "ack-1" for e in ep._pending)
```

**Note:** `await ep.start(cast(BusHandle, stub))` — `_StubHandle` must implement `ack` and `publish` at minimum for later tasks.

Add a second test in the same file (or Step 3 of Task 2): **auto-ack with zero sessions** must `await ep.deliver(ack)` without raising `EndpointUnavailable` (auto path must not require HTTP MCP session).

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd E:/workspaces/ai/agents/agent_core/.worktrees/fix-issue-12-ack
uv run pytest packages/core/tests/test_claude_code_mcp_auto_ack.py::test_green_ack_matching_recent_outbound_skips_channel_push -v
```

Expected: **FAIL** (attribute `_recent_outbound_ids` missing, or push still fires).

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/test_claude_code_mcp_auto_ack.py
git commit -m "test: add failing auto-ack channel suppression case (issue #12)"
```

---

### Task 2: Minimal registry + `deliver()` auto-handle branch

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py`

- [ ] **Step 1: Add state in `__init__`**

After `self._notify_broker = ...`:

```python
self.wake_on_all_acknowledgments: bool = wake_on_all_acknowledgments
self._outbound_registry_ttl_seconds: float = float(outbound_registry_ttl_seconds)
self._missing_ack_default_seconds: float = float(missing_ack_default_seconds)
self._max_tracked_outbounds: int = int(max_tracked_outbounds)
self._recent_outbound_ids: dict[str, float] = {}  # id -> loop.time() registered
self._missing_ack_tasks: dict[str, asyncio.Task[None]] = {}
```

Add constructor kwargs **with defaults** so existing call sites unchanged:

```python
def __init__(
    self,
    *,
    name: str,
    mount: str,
    notify_broker: NotificationBroker | None = None,
    wake_on_all_acknowledgments: bool = False,
    outbound_registry_ttl_seconds: float = 900.0,
    missing_ack_default_seconds: float = 30.0,
    max_tracked_outbounds: int = 10_000,
):
```

- [ ] **Step 2: Add `_is_routine_green_ack(self, envelope: Envelope) -> bool`**

Logic:

- `False` if `wake_on_all_acknowledgments`.
- `False` unless `envelope.kind == "Acknowledgment"` and `isinstance(envelope.payload, AcknowledgmentPayload)`.
- `False` if `envelope.urgency != "green"`.
- `False` if `envelope.in_reply_to` is None or `envelope.payload.of != envelope.in_reply_to`.
- `False` if note indicates failure: `envelope.payload.note is not None and envelope.payload.note.startswith("error:")`.
- `True` if `envelope.in_reply_to` in `self._recent_outbound_ids` and not TTL-expired (compare `loop.time() - registered_at <= self._outbound_registry_ttl_seconds`).

- [ ] **Step 3: Add `_evict_stale_outbounds(self) -> None`**

Remove entries past TTL; if size > `_max_tracked_outbounds`, drop oldest by registered time.

- [ ] **Step 4: Rewrite `deliver()`**

Pseudocode:

```python
async def deliver(self, envelope: Envelope) -> None:
    if self._is_routine_green_ack(envelope):
        if self._handle is None:
            return  # or raise? match spec: still need ack — raise RuntimeError consistent with handle()
        await self._handle.ack(envelope.id)
        rid = envelope.in_reply_to
        assert rid is not None
        self._recent_outbound_ids.pop(rid, None)
        self._cancel_missing_ack(rid)
        return  # do not raise EndpointUnavailable — bridge ack is satisfied without MCP session
    self.queue_for_pickup(envelope)
    await self._notify_mail_arrived(envelope.urgency)
    if not self._sessions:
        raise EndpointUnavailable(...)
```

Import `AcknowledgmentPayload` from `agent_core.bus.envelope`.

- [ ] **Step 5: Run Task 1 test — expect PASS**

```bash
uv run pytest packages/core/tests/test_claude_code_mcp_auto_ack.py::test_green_ack_matching_recent_outbound_skips_channel_push -v
```

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py
git commit -m "feat(mcp): auto-handle routine green acks without wake (issue #12)"
```

---

### Task 3: Register outbound on `send` + cancel timer helper

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py` (`send` tool inner function)

- [ ] **Step 1: After `await self._handle.publish(env)` succeeds**, if not `wake_on_all_acknowledgments`:

```python
loop = asyncio.get_running_loop()
now = loop.time()
self._evict_stale_outbounds()
self._recent_outbound_ids[env.id] = now
self._schedule_missing_ack(env.id, env.metadata)
```

- [ ] **Step 2: Implement `_cancel_missing_ack(self, outbound_id: str) -> None`**

Pop task from `_missing_ack_tasks`, `task.cancel()`, await if needed in `stop()` only (sync cancel in deliver path without await is OK if task handles CancelledError).

- [ ] **Step 3: New test — outbound not registered → wake still happens**

```python
@pytest.mark.asyncio
async def test_green_ack_without_registered_outbound_still_pushes():
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    _speed_up_debounce(ep)
    stub = _StubHandle()
    await ep.start(cast(Any, stub))
    session = _RecordingSession()
    ep._register_session(session)
    ack = Envelope(
        id="ack-2",
        correlation_id="c2",
        in_reply_to="unknown-outbound",
        from_="x",
        to="agent",
        kind="Acknowledgment",
        payload=AcknowledgmentPayload(of="unknown-outbound", note=None),
        urgency="green",
        created_at=datetime.now(UTC),
    )
    await ep.deliver(ack)
    await asyncio.sleep(0.15)
    assert len(session.sent) == 1
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest packages/core/tests/test_claude_code_mcp_auto_ack.py -v
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/tests/test_claude_code_mcp_auto_ack.py
git commit -m "feat(mcp): track outbounds on send for auto-ack matching"
```

---

### Task 4: Failure and urgency paths

**Files:**
- Modify: `packages/core/tests/test_claude_code_mcp_auto_ack.py`

- [ ] **Step 1: Tests**

1. `test_error_note_ack_always_wakes` — note `error: boom`, assert `len(session.sent) >= 1` after debounce.
2. `test_yellow_ack_wakes` — `urgency="yellow"`, assert push.
3. `test_malformed_of_mismatch_wakes` — `payload.of` != `in_reply_to`, assert push.

- [ ] **Step 2: Run + commit**

```bash
uv run pytest packages/core/tests/test_claude_code_mcp_auto_ack.py -v
git commit -am "test(mcp): cover ack wake paths for issue #12"
```

---

### Task 5: Missing-ack timer

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py`

- [ ] **Step 1: `_schedule_missing_ack(self, outbound_id: str, metadata: dict[str, Any]) -> None`**

Read optional `metadata.get("agent_core", {})` sub-dict: if dict and `ack_timeout_seconds` is int/float, use it; else `self._missing_ack_default_seconds`. Cancel any existing task for same id.

```python
async def _missing_ack_fire(outbound_id: str) -> None:
    self._missing_ack_tasks.pop(outbound_id, None)
    if outbound_id not in self._recent_outbound_ids:
        return
    # still waiting for ack
    self._recent_outbound_ids.pop(outbound_id, None)
    await self._notify_mail_arrived("yellow")
```

Schedule: `self._missing_ack_tasks[outbound_id] = asyncio.create_task(_delayed())` where `_delayed` sleeps then calls `_missing_ack_fire`. Use `asyncio.sleep` with the computed timeout.

- [ ] **Step 2: Test with freezegun or short timeout**

Use `missing_ack_default_seconds=0.08` on endpoint constructor in test; register outbound manually; sleep `0.2`; assert `session.sent` non-empty and outbound removed from registry.

- [ ] **Step 3: Test metadata override**

`metadata={"agent_core": {"ack_timeout_seconds": 0.2}}` — assert timer respects (use coarse asyncio.sleep).

- [ ] **Step 4: `stop()` cleanup**

In `stop()`, cancel `_debounce_task` (existing), then:

```python
for t in list(self._missing_ack_tasks.values()):
    t.cancel()
self._missing_ack_tasks.clear()
self._recent_outbound_ids.clear()
```

- [ ] **Step 5: Run full core tests subset + commit**

```bash
uv run pytest packages/core/tests/test_claude_code_mcp_auto_ack.py packages/core/tests/test_notify_mail_arrived.py -q
git commit -am "feat(mcp): missing-ack timer and stop cleanup (issue #12)"
```

---

### Task 6: `wake_on_all_acknowledgments` + YAML params

**Files:**
- Modify: `packages/core/tests/test_runner_http_host.py`

- [ ] **Step 1: Extend YAML in a new test**

```yaml
  - type: builtin.claude_code_mcp
    name: agent-flags
    params:
      mount: /mcp/agent-flags
      wake_on_all_acknowledgments: true
      missing_ack_default_seconds: 12
      outbound_registry_ttl_seconds: 600
```

`await build_bus_from_config(cfg)` — assert no `BusBootError`, and `bus._endpoints_by_name["agent-flags"].endpoint.wake_on_all_acknowledgments is True` (reach through `EndpointSpec` attribute name as used elsewhere in tests).

- [ ] **Step 2: Unit test debug flag**

With `wake_on_all_acknowledgments=True`, pre-register outbound AND deliver green ack — assert **push happens** (current behavior).

- [ ] **Step 3: Run + commit**

```bash
uv run pytest packages/core/tests/test_runner_http_host.py packages/core/tests/test_claude_code_mcp_auto_ack.py -q
git commit -am "feat(runner): wire claude MCP auto-ack kwargs from YAML"
```

---

### Task 7: MCP instructions + broker parity

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py` (FastMCP `instructions=` string)

- [ ] **Step 1: Append short paragraph**

Explain routine delivery acks may be auto-cleared without wake; failures and missing-ack still wake; `list_pending` remains authoritative for everything else.

- [ ] **Step 2: Broker on auto-ack**

Design says broker is for relay; auto-handle should **not** publish a mailbox summary for silent acks. Confirm `_fire_after_debounce` is only called via `_notify_mail_arrived` — no change if auto-path skips it. Add a test with `notify_broker=RecordingBroker` from `test_notify_broker_publish_hook.py` patterns — assert **no** `publish` when auto-ack.

- [ ] **Step 3: Run + commit**

```bash
uv run pytest packages/core/tests/test_claude_code_mcp_auto_ack.py packages/core/tests/test_notify_broker_publish_hook.py -q
git commit -am "docs(mcp): document auto-ack behavior in agent instructions"
```

---

### Task 8: Regression sweep + integration

- [ ] **Step 1: Full package core tests**

```bash
uv run pytest packages/core/tests/ -q --tb=no
```

- [ ] **Step 2: Fix `test_bus_daemon_push_integration` / Discord tests** if they assumed every ack wakes — update expectations or fixtures.

- [ ] **Step 3: Commit**

```bash
git commit -am "test: align integration tests with auto-ack (issue #12)"
```

---

## Spec coverage checklist (self-review)

| Spec section | Task(s) |
|--------------|---------|
| Recent-outbound registry + TTL + cap | 2, 3, 6 |
| Auto-handle path (ack, no pending, no notify) | 1–2 |
| Wake on non-routine | 4, 6 |
| Missing-ack | 5 |
| Debug flag | 6 |
| MCP instructions | 7 |
| Broker / HTTP push parity | 7 |
| `stop()` cleanup | 5 |
| YAML operator config | 6 |
| Inspectability (persistence ack) | 2 (`ack()` same path) |

**Placeholder scan:** None intentional.

**Type consistency:** `Envelope.payload` for Ack kind is `AcknowledgmentPayload`; use `isinstance` before accessing `.of` / `.note`.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-02-issue-12-auto-ack-wakeup.md`.

**1. Subagent-driven (recommended)** — Fresh subagent per task, review between tasks, fast iteration.

**2. Inline execution** — Run tasks in this session with executing-plans checkpoints.

Which approach do you want for implementation?
