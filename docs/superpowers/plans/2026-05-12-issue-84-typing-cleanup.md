# Issue #84 v2 — discord-pepper typing-cleanup linkage (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `_send`'s post-send cleanup to clear `_awaiting_reply_ids` by the inbound's Discord message_id when the outbound carries bus-level `envelope.in_reply_to`. Add a 90s per-task lazy TTL safety net for orphan cases.

**Architecture:** Translation at two outbound-construction sites (`_deliver_text_message` and `_inject_channel_id`). New optional `_SendArgs.cleanup_inbound_message_id` field carries the inbound's Discord message_id through to `_send`'s existing post-send block, which extends with a parallel `if`. `_awaiting_reply_ids` gains a sibling `_awaiting_reply_ids_timestamps` dict managed at six existing call sites; `_typing_while_pending` checks TTL on each poll tick with a self-healing missing-key path.

**Tech Stack:** Python 3.12+, Pydantic v2, `pytest`/`pytest-asyncio`, discord.py 2.x, `time.monotonic()` for TTL clock. Use `uv run --no-sync` for pytest. Conventional commits, no `Co-Authored-By` trailer.

**Branch:** `feat/issue-84-typing-cleanup` (already created off main; spec committed at `42617c8`).

---

## Phase 1 — Schema

### Task 1: Add `_SendArgs.cleanup_inbound_message_id` field

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/args.py:15-20`

No tests at this layer — the field is exercised at the handler-integration layer in later tasks. Adding a standalone unit test for "Pydantic accepts an optional string field" is cargo-cult.

- [ ] **Step 1: Add the new field to `_SendArgs`**

In `packages/agent-core-discord/src/agent_core_discord/args.py`, locate `_SendArgs` (around line 15):

```python
class _SendArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    text: str | None = None
    embeds: list[dict[str, Any]] | None = None
    reply_to: str | None = None
    files: list[str] | None = None
```

Add `cleanup_inbound_message_id: str | None = None` at the end of the field list:

```python
class _SendArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    text: str | None = None
    embeds: list[dict[str, Any]] | None = None
    reply_to: str | None = None
    files: list[str] | None = None
    cleanup_inbound_message_id: str | None = None
```

- [ ] **Step 2: Run the existing discord test suite to confirm no regression**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests -x -q`

Expected: all green. The new field is optional with default `None`; no existing caller is affected.

- [ ] **Step 3: Lint check**

Run: `uv run --no-sync ruff check packages/agent-core-discord/src/agent_core_discord/args.py`

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/args.py
git commit -m "feat(discord): add _SendArgs.cleanup_inbound_message_id optional field (#84)"
```

---

## Phase 2 — Pair-management infrastructure

### Task 2: Add `_awaiting_reply_ids_timestamps` + update six call sites

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py` (init at line 282; six pair-management sites at lines 782, 904, 908, 1095, 1137, 1159)
- Create: `packages/agent-core-discord/tests/test_awaiting_reply_pair.py`

The six sites have three pair-management shapes:
- **Seed (line 904):** `_awaiting_reply_ids.add(mid)` → also `_timestamps[mid] = time.monotonic()`
- **Discard (lines 908, 1095, 1137, 1159):** `_awaiting_reply_ids.discard(id)` → also `_timestamps.pop(id, None)`
- **Bulk clear (line 782):** `_awaiting_reply_ids.clear()` → also `_timestamps.clear()`

- [ ] **Step 1: Write the four Group 1 tests**

Create `packages/agent-core-discord/tests/test_awaiting_reply_pair.py`:

```python
"""Tests for `_awaiting_reply_ids` + `_awaiting_reply_ids_timestamps` pair-management (#84)."""

from __future__ import annotations

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core_discord.testing.fakes import (
    FakeBusHandle,
    FakeChannel,
    FakeDiscordClient,
    FakeMessage,
    FakeUser,
)


async def _started(monkeypatch):
    """Construct a started DiscordEndpoint with a fake bus handle and fake client."""
    from datetime import UTC, datetime  # noqa: F401 — used by callers
    from agent_core_discord.endpoint import DiscordEndpoint

    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    fake.user = FakeUser(id="bot", display_name="bot", bot=True)
    handle = FakeBusHandle(name="discord-test")
    ep = DiscordEndpoint(
        name="discord-test", target="agent-test", token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    return ep, handle, fake


@pytest.mark.asyncio
async def test_awaiting_reply_id_and_timestamp_seeded_together_on_message(monkeypatch):
    """on_message seeding: both `_awaiting_reply_ids` and `_awaiting_reply_ids_timestamps`
    receive the Discord message_id together."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    inbound = FakeMessage(id="m-seed", channel_id="100", content="hi")
    inbound.channel = ch
    inbound.author = FakeUser(id="user-1", display_name="someone", bot=False)
    try:
        await fake.fire("on_message", inbound)
        assert "m-seed" in ep._awaiting_reply_ids
        assert "m-seed" in ep._awaiting_reply_ids_timestamps
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_clear_pending_ack_clears_both_sets(monkeypatch):
    """_clear_pending_ack helper discards from BOTH _awaiting_reply_ids
    and _awaiting_reply_ids_timestamps. Covers the three sites that funnel
    through this helper (lines 1095, 1137, 1159)."""
    import time

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    ep._awaiting_reply_ids.add("m-clear")
    ep._awaiting_reply_ids_timestamps["m-clear"] = time.monotonic()
    try:
        await ep._clear_pending_ack(ch, "m-clear")
        assert "m-clear" not in ep._awaiting_reply_ids
        assert "m-clear" not in ep._awaiting_reply_ids_timestamps
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_awaiting_reply_id_cleared_on_publish_rollback(monkeypatch):
    """on_message's publish-failure rollback (line 908) clears BOTH sets
    so a failed publish doesn't leave orphan tracking state."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    inbound = FakeMessage(id="m-fail", channel_id="100", content="x")
    inbound.channel = ch
    inbound.author = FakeUser(id="user-2", display_name="someone", bot=False)

    # Force publish to raise.
    async def _raise_publish(*args, **kwargs):
        raise RuntimeError("simulated publish failure")

    monkeypatch.setattr(handle, "publish", _raise_publish)

    try:
        with pytest.raises(RuntimeError):
            await fake.fire("on_message", inbound)
        assert "m-fail" not in ep._awaiting_reply_ids
        assert "m-fail" not in ep._awaiting_reply_ids_timestamps
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_awaiting_reply_id_cleared_on_endpoint_stop(monkeypatch):
    """stop() bulk-clear (line 782) clears BOTH _awaiting_reply_ids
    and _awaiting_reply_ids_timestamps."""
    import time

    ep, handle, fake = await _started(monkeypatch)
    ep._awaiting_reply_ids.add("m-stop")
    ep._awaiting_reply_ids_timestamps["m-stop"] = time.monotonic()
    await ep.stop()
    assert ep._awaiting_reply_ids == set()
    assert ep._awaiting_reply_ids_timestamps == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_awaiting_reply_pair.py -v`

Expected: all 4 FAIL with `AttributeError: 'DiscordEndpoint' object has no attribute '_awaiting_reply_ids_timestamps'`.

- [ ] **Step 3: Add the timestamps dict initialization**

In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, locate `_awaiting_reply_ids` declaration (around line 282):

```python
        # Discord message ids we published to the bus and have not "finished"
        # yet (cleared when ack reaction is removed or TTL/LRU evicts).
        self._awaiting_reply_ids: set[str] = set()
```

Add the sibling timestamps dict immediately after:

```python
        # Discord message ids we published to the bus and have not "finished"
        # yet (cleared when ack reaction is removed or TTL/LRU evicts).
        self._awaiting_reply_ids: set[str] = set()
        # Sibling timestamps map — same insertion/deletion pairs as
        # `_awaiting_reply_ids`. Per-task lazy TTL safety net inside
        # `_typing_while_pending` evicts orphan entries after
        # `_TYPING_TTL_SECONDS`. See spec doc for the pair-management
        # discipline (#84).
        self._awaiting_reply_ids_timestamps: dict[str, float] = {}
```

- [ ] **Step 4: Update the six pair-management call sites**

**Site 1 — line 782 (bulk clear in `stop()`):** locate

```python
        # Drop typing / threading state so background typing tasks exit promptly.
        self._awaiting_reply_ids.clear()
```

Add the sibling clear immediately after:

```python
        # Drop typing / threading state so background typing tasks exit promptly.
        self._awaiting_reply_ids.clear()
        self._awaiting_reply_ids_timestamps.clear()
```

**Site 2 — line 904 (seed in `on_message`):** locate

```python
            mid = str(message.id)
            self._awaiting_reply_ids.add(mid)
            try:
                await self._handle.publish(env)
```

Modify to also set the timestamp:

```python
            import time  # ensure time is imported at module top; remove this comment if already present
            mid = str(message.id)
            self._awaiting_reply_ids.add(mid)
            self._awaiting_reply_ids_timestamps[mid] = time.monotonic()
            try:
                await self._handle.publish(env)
```

(If `import time` is already at the top of `endpoint.py`, drop the inline import comment. Don't add a duplicate import.)

**Site 3 — line 908 (publish-rollback discard):** locate

```python
            except BaseException:
                self._awaiting_reply_ids.discard(mid)
                self._inbound_envelope_discord.pop(env.id, None)
                raise
```

Add the sibling pop:

```python
            except BaseException:
                self._awaiting_reply_ids.discard(mid)
                self._awaiting_reply_ids_timestamps.pop(mid, None)
                self._inbound_envelope_discord.pop(env.id, None)
                raise
```

**Site 4 — line 1095 (buffer overflow discard):** locate

```python
            self._awaiting_reply_ids.discard(old_id)
```

Add the sibling pop:

```python
            self._awaiting_reply_ids.discard(old_id)
            self._awaiting_reply_ids_timestamps.pop(old_id, None)
```

**Site 5 — line 1137 (TTL sweep discard):** locate

```python
            self._awaiting_reply_ids.discard(head_id)
```

Add the sibling pop:

```python
            self._awaiting_reply_ids.discard(head_id)
            self._awaiting_reply_ids_timestamps.pop(head_id, None)
```

**Site 6 — line 1159 (`_clear_pending_ack` helper):** locate

```python
    async def _clear_pending_ack(self, channel, message_id: str) -> None:
        mid = str(message_id)
        self._awaiting_reply_ids.discard(mid)
```

Add the sibling pop:

```python
    async def _clear_pending_ack(self, channel, message_id: str) -> None:
        mid = str(message_id)
        self._awaiting_reply_ids.discard(mid)
        self._awaiting_reply_ids_timestamps.pop(mid, None)
```

- [ ] **Step 5: Run Group 1 tests, verify all 4 pass**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_awaiting_reply_pair.py -v`

Expected: 4 passed.

Run broader suite: `uv run --no-sync pytest packages/agent-core-discord/tests -x -q`

Expected: all green.

Lint: `uv run --no-sync ruff check packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_awaiting_reply_pair.py`

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_awaiting_reply_pair.py
git commit -m "feat(discord): pair-manage _awaiting_reply_ids with timestamps dict (#84)"
```

---

## Phase 3 — TTL polling check + self-heal

### Task 3: Add `_TYPING_TTL_SECONDS` class attribute + extend `_typing_while_pending`

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py` (class attribute near top of `DiscordEndpoint`; `_typing_while_pending` at lines 381-400)
- Create: `packages/agent-core-discord/tests/test_typing_ttl.py`

- [ ] **Step 1: Write the three Group 3 tests**

Create `packages/agent-core-discord/tests/test_typing_ttl.py`:

```python
"""Tests for `_typing_while_pending` TTL safety net + self-heal (#84)."""

from __future__ import annotations

import asyncio
import time

import pytest

from agent_core_discord.testing.fakes import (
    FakeBusHandle,
    FakeChannel,
    FakeDiscordClient,
    FakeUser,
)


async def _started(monkeypatch):
    from agent_core_discord.endpoint import DiscordEndpoint

    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    fake.user = FakeUser(id="bot", display_name="bot", bot=True)
    handle = FakeBusHandle(name="discord-test")
    ep = DiscordEndpoint(
        name="discord-test", target="agent-test", token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    return ep, handle, fake


@pytest.mark.asyncio
async def test_typing_evicts_after_ttl_when_no_cleanup_fires(monkeypatch):
    """Orphan entry exceeds TTL → polling loop evicts both sets and exits."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    # Seed with an artificially old timestamp.
    ep._awaiting_reply_ids.add("m-old")
    ep._awaiting_reply_ids_timestamps["m-old"] = time.monotonic() - 100.0
    try:
        # Dispatch the polling task; one poll tick (0.2s) is enough.
        task = asyncio.create_task(ep._typing_while_pending(ch, "m-old"))
        await asyncio.sleep(0.3)
        assert task.done()
        assert "m-old" not in ep._awaiting_reply_ids
        assert "m-old" not in ep._awaiting_reply_ids_timestamps
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_typing_does_not_evict_within_ttl_window(monkeypatch):
    """Fresh entry stays in both sets while polling loop runs."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    ep._awaiting_reply_ids.add("m-fresh")
    ep._awaiting_reply_ids_timestamps["m-fresh"] = time.monotonic() - 10.0
    try:
        task = asyncio.create_task(ep._typing_while_pending(ch, "m-fresh"))
        await asyncio.sleep(0.3)
        # Still pending — TTL is 90s, we're 10s in.
        assert not task.done()
        assert "m-fresh" in ep._awaiting_reply_ids
        assert "m-fresh" in ep._awaiting_reply_ids_timestamps
        # Clean shutdown.
        ep._awaiting_reply_ids.discard("m-fresh")
        ep._awaiting_reply_ids_timestamps.pop("m-fresh", None)
        await asyncio.sleep(0.3)
        assert task.done()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_typing_evicts_immediately_on_missing_timestamp_self_heal(monkeypatch):
    """Self-healing property: entry in `_awaiting_reply_ids` but missing
    timestamp triggers immediate eviction. `get(mid, 0)` returns 0;
    `time.monotonic() - 0` is a huge delta > 90s.
    """
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    # Simulate a pair-management slip: id in set, no timestamp.
    ep._awaiting_reply_ids.add("m-slipped")
    # Deliberately do NOT set _awaiting_reply_ids_timestamps["m-slipped"].
    try:
        task = asyncio.create_task(ep._typing_while_pending(ch, "m-slipped"))
        await asyncio.sleep(0.3)
        assert task.done()
        assert "m-slipped" not in ep._awaiting_reply_ids
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_typing_ttl.py -v`

Expected: tests 1 and 3 FAIL (no TTL check yet — the polling loop never exits on stale entries). Test 2 passes by accident (no TTL = loop keeps running, which is what test 2 asserts during the first half).

- [ ] **Step 3: Add `_TYPING_TTL_SECONDS` class attribute**

In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, locate the `DiscordEndpoint` class declaration. Near the top of the class body (before `__init__`), add:

```python
class DiscordEndpoint(Endpoint):
    """..."""

    # Per-task lazy TTL for `_awaiting_reply_ids`. Evicted inside
    # `_typing_while_pending` to prevent stale typing indicators when
    # explicit cleanup doesn't fire (cache miss, no in_reply_to,
    # dismissed-without-reply, etc.). 90s is the upper bound on observed
    # realistic compose windows with ~25% headroom.
    _TYPING_TTL_SECONDS: float = 90.0
```

(Place it after the docstring and before `__init__`. Class attributes go in this position by convention.)

- [ ] **Step 4: Extend `_typing_while_pending` polling loop**

Locate `_typing_while_pending` (lines 381-400):

```python
    async def _typing_while_pending(self, channel: Any, message_id: str) -> None:
        """Hold Discord 'typing…' until this message is cleared from the awaiting set."""
        typing_factory = getattr(channel, "typing", None)
        if typing_factory is None:
            return
        try:
            async with typing_factory():
                while message_id in self._awaiting_reply_ids:
                    # Short poll so ack clear / stop() drops the id promptly; the
                    # typing context manager (discord.py) keeps the indicator fresh.
                    await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug(
                "discord(%s): typing loop for message %s ended",
                self.name,
                message_id,
                exc_info=True,
            )
```

Extend the inner `while` loop with a TTL check + self-healing eviction. The replacement loop body:

```python
        try:
            async with typing_factory():
                while message_id in self._awaiting_reply_ids:
                    # TTL safety net (#84): orphan entries (no explicit cleanup
                    # fired, agent dismissed without reply, cache miss) evict
                    # after _TYPING_TTL_SECONDS. Missing-timestamp self-heals
                    # via `get(mid, 0)` → huge delta → immediate eviction.
                    ts = self._awaiting_reply_ids_timestamps.get(message_id, 0)
                    if time.monotonic() - ts > self._TYPING_TTL_SECONDS:
                        self._awaiting_reply_ids.discard(message_id)
                        self._awaiting_reply_ids_timestamps.pop(message_id, None)
                        break
                    # Short poll so ack clear / stop() drops the id promptly; the
                    # typing context manager (discord.py) keeps the indicator fresh.
                    await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug(
                "discord(%s): typing loop for message %s ended",
                self.name,
                message_id,
                exc_info=True,
            )
```

(Confirm `time` is imported at module top. If not, add `import time` to the existing imports.)

- [ ] **Step 5: Run tests, verify all 3 pass**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_typing_ttl.py -v`

Expected: 3 passed.

Run broader suite: `uv run --no-sync pytest packages/agent-core-discord/tests -x -q`

Expected: all green.

Lint: `uv run --no-sync ruff check packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_typing_ttl.py`

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_typing_ttl.py
git commit -m "feat(discord): 90s TTL safety net + self-heal in _typing_while_pending (#84)"
```

---

## Phase 4 — Translation at `_deliver_text_message` + `_send` cleanup

### Task 4: Wire TextMessage envelope path + extend `_send` cleanup block

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py` (translation in `_deliver_text_message` lines 638-697; cleanup extension in `_send` around line 1284)
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append 3 tests)

This task implements both the translation site AND the cleanup extension because the named-symptom test needs both pieces in place to pass.

- [ ] **Step 1: Write the three Group 2 TextMessage envelope tests**

Append to `packages/agent-core-discord/tests/test_endpoint_outbound.py`:

```python
@pytest.mark.asyncio
async def test_text_message_envelope_with_in_reply_to_clears_typing(monkeypatch):
    """Issue #84 named-symptom regression lock: outbound TextMessage envelope
    with bus-level in_reply_to clears typing via the inbound's Discord
    message_id. If this test ever flakes or fails, the bug has returned.
    """
    import time

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    # Simulate prior inbound: seed _awaiting_reply_ids and _recent_inbounds.
    ep._awaiting_reply_ids.add("inbound-discord-mid")
    ep._awaiting_reply_ids_timestamps["inbound-discord-mid"] = time.monotonic()
    from agent_core.bus.envelope import Envelope, TextMessagePayload
    from datetime import UTC, datetime
    inbound_env = Envelope(
        id="inbound-env-id", correlation_id="c", from_="discord-test", to="agent-test",
        kind="TextMessage", payload=TextMessagePayload(text="hi"),
        metadata={"discord": {"channel_id": "500", "message_id": "inbound-discord-mid"}},
        created_at=datetime.now(UTC),
    )
    ep._record_inbound(inbound_env)
    try:
        outbound = Envelope(
            id="agent-reply", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text="replying"),
            in_reply_to="inbound-env-id",  # bus-level linkage
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(outbound)
        # Typing cleanup fired via the new path.
        assert "inbound-discord-mid" not in ep._awaiting_reply_ids
        assert "inbound-discord-mid" not in ep._awaiting_reply_ids_timestamps
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_text_message_envelope_without_in_reply_to_does_not_clear_typing(monkeypatch):
    """No bus-level linkage → no cleanup. _awaiting_reply_ids retains the
    inbound's mid (TTL safety net is what eventually clears it)."""
    import time

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    ep._awaiting_reply_ids.add("inbound-discord-mid")
    ep._awaiting_reply_ids_timestamps["inbound-discord-mid"] = time.monotonic()
    from agent_core.bus.envelope import Envelope, TextMessagePayload
    from datetime import UTC, datetime
    try:
        outbound = Envelope(
            id="agent-broadcast", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text="proactive"),
            # NO in_reply_to set.
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(outbound)
        # No cleanup; mid still tracked.
        assert "inbound-discord-mid" in ep._awaiting_reply_ids
        assert "inbound-discord-mid" in ep._awaiting_reply_ids_timestamps
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_text_message_envelope_with_cache_miss_does_not_clear_typing(monkeypatch):
    """Cache miss (inbound not in _recent_inbounds): cleanup no-ops cleanly."""
    import time

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    ep._awaiting_reply_ids.add("inbound-discord-mid")
    ep._awaiting_reply_ids_timestamps["inbound-discord-mid"] = time.monotonic()
    # Deliberately do NOT call _record_inbound; _recent_inbounds is empty.
    from agent_core.bus.envelope import Envelope, TextMessagePayload
    from datetime import UTC, datetime
    try:
        outbound = Envelope(
            id="agent-reply", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text="orphan reply"),
            in_reply_to="never-recorded-env-id",  # cache miss
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(outbound)
        # No crash; mid still tracked (TTL will eventually clean it up).
        assert "inbound-discord-mid" in ep._awaiting_reply_ids
    finally:
        await ep.stop()
```

If `_started`'s name differs in this file, adapt to whatever fixture the surrounding tests use.

- [ ] **Step 2: Run tests to verify they fail (test 1) or pass (tests 2-3)**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -k "in_reply_to_clears_typing or without_in_reply_to or cache_miss_does_not" -v`

Expected:
- `test_text_message_envelope_with_in_reply_to_clears_typing` FAILS — no translation + no cleanup wiring yet.
- `test_text_message_envelope_without_in_reply_to_does_not_clear_typing` PASSES (no in_reply_to → no translation attempt → no cleanup → mid stays).
- `test_text_message_envelope_with_cache_miss_does_not_clear_typing` PASSES (translation no-ops on cache miss → no cleanup → mid stays).

- [ ] **Step 3: Add translation at `_deliver_text_message`**

In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, locate `_deliver_text_message`. Just before the `args = _SendArgs(...)` construction (around line 691):

```python
        text_for_send: str | None = envelope.payload.text
        if embeds_data and not text_for_send:
            text_for_send = None

        args = _SendArgs(
            channel_id=str(channel_id),
            text=text_for_send,
            embeds=embeds_data,
            reply_to=reply_to,
        )
        return await self._send(args)
```

Insert the translation logic and pass the new field:

```python
        text_for_send: str | None = envelope.payload.text
        if embeds_data and not text_for_send:
            text_for_send = None

        # Translate bus-level in_reply_to to inbound's Discord message_id for
        # typing cleanup (#84). Cache miss / missing metadata / no in_reply_to
        # all degrade to None → cleanup no-ops → TTL safety net.
        cleanup_inbound_message_id: str | None = None
        if envelope.in_reply_to:
            inbound = self._recent_inbounds.get(envelope.in_reply_to)
            if inbound:
                discord_meta = (inbound.metadata or {}).get("discord") or {}
                cleanup_inbound_message_id = discord_meta.get("message_id")

        args = _SendArgs(
            channel_id=str(channel_id),
            text=text_for_send,
            embeds=embeds_data,
            reply_to=reply_to,
            cleanup_inbound_message_id=cleanup_inbound_message_id,
        )
        return await self._send(args)
```

(The existing `files=files` from #64 if present should stay; only `cleanup_inbound_message_id` is added. Order of kwargs is stylistic.)

- [ ] **Step 4: Extend `_send`'s post-send cleanup block**

Locate the post-send cleanup in `_send` (around line 1284):

```python
            if args.reply_to:
                await self._clear_pending_ack(ch, args.reply_to)
```

Add the parallel cleanup immediately after:

```python
            if args.reply_to:
                await self._clear_pending_ack(ch, args.reply_to)
            if args.cleanup_inbound_message_id:
                await self._clear_pending_ack(ch, args.cleanup_inbound_message_id)
```

If `_send` has multiple post-send cleanup sites (e.g., one per chunked-message branch), add the parallel `if` at each site that currently has the `args.reply_to` cleanup.

- [ ] **Step 5: Run the three tests, verify all pass**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -k "in_reply_to_clears_typing or without_in_reply_to or cache_miss_does_not" -v`

Expected: 3 passed.

Run broader suite: `uv run --no-sync pytest packages/agent-core-discord/tests -x -q`

Expected: all green.

Lint clean.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "feat(discord): wire TextMessage typing-cleanup via bus in_reply_to (#84)"
```

---

## Phase 5 — Translation at `_inject_channel_id`

### Task 5: Wire ToolInvocation path

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py` (`_inject_channel_id` closure inside `_dispatch`)
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append 2 tests)

- [ ] **Step 1: Write tests 8 and 9 (ToolInvocation paths)**

Append to `packages/agent-core-discord/tests/test_endpoint_outbound.py`:

```python
@pytest.mark.asyncio
async def test_tool_invocation_send_with_in_reply_to_clears_typing(monkeypatch):
    """Parallel path: ToolInvocation `send` verb with bus-level in_reply_to
    clears typing via _inject_channel_id's translation."""
    import time

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    ep._awaiting_reply_ids.add("inbound-discord-mid")
    ep._awaiting_reply_ids_timestamps["inbound-discord-mid"] = time.monotonic()
    from agent_core.bus.envelope import Envelope, TextMessagePayload
    from datetime import UTC, datetime
    inbound_env = Envelope(
        id="inbound-env-id", correlation_id="c", from_="discord-test", to="agent-test",
        kind="TextMessage", payload=TextMessagePayload(text="hi"),
        metadata={"discord": {"channel_id": "500", "message_id": "inbound-discord-mid"}},
        created_at=datetime.now(UTC),
    )
    ep._record_inbound(inbound_env)
    try:
        env = _envelope(
            "e", "agent-test", "discord-test",
            _toolcall("send", {"channel_id": "500", "text": "verb reply"}),
        )
        env.in_reply_to = "inbound-env-id"
        await ep.deliver(env)
        assert "inbound-discord-mid" not in ep._awaiting_reply_ids
        assert "inbound-discord-mid" not in ep._awaiting_reply_ids_timestamps
    finally:
        await ep.stop()


@pytest.mark.parametrize("verb_name,args_extra", [
    ("edit", {"message_id": "m-edit", "text": "new text"}),
    ("react", {"message_id": "m-react", "emoji": "👍"}),
    ("send_briefing", {
        "date_line": "test", "focus": "f", "calendar": "c",
        "critical_items": [], "warning_items": [],
    }),
])
@pytest.mark.asyncio
async def test_tool_invocation_verbs_clear_typing_via_in_reply_to(
    monkeypatch, verb_name, args_extra
):
    """Parameterized: every ToolInvocation verb that hits _inject_channel_id
    benefits from the typing-cleanup translation."""
    import time

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    ep._awaiting_reply_ids.add("inbound-discord-mid")
    ep._awaiting_reply_ids_timestamps["inbound-discord-mid"] = time.monotonic()
    from agent_core.bus.envelope import Envelope, TextMessagePayload
    from datetime import UTC, datetime
    inbound_env = Envelope(
        id="inbound-env-id", correlation_id="c", from_="discord-test", to="agent-test",
        kind="TextMessage", payload=TextMessagePayload(text="hi"),
        metadata={"discord": {"channel_id": "500", "message_id": "inbound-discord-mid"}},
        created_at=datetime.now(UTC),
    )
    ep._record_inbound(inbound_env)
    # Pre-seed messages for edit/react.
    if verb_name in ("edit", "react"):
        ch._messages[args_extra["message_id"]] = FakeMessage(
            id=args_extra["message_id"], channel_id="500",
        )
    try:
        env = _envelope(
            "e", "agent-test", "discord-test",
            _toolcall(verb_name, {**args_extra}),
        )
        env.in_reply_to = "inbound-env-id"
        await ep.deliver(env)
        assert "inbound-discord-mid" not in ep._awaiting_reply_ids, (
            f"verb={verb_name} did not clear typing via in_reply_to"
        )
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -k "tool_invocation_send_with_in_reply_to or tool_invocation_verbs_clear_typing" -v`

Expected: FAILs — `_inject_channel_id` doesn't translate in_reply_to to cleanup_inbound_message_id yet.

- [ ] **Step 3: Extend `_inject_channel_id`**

In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, locate `_inject_channel_id` inside `_dispatch` (around line 714):

```python
        def _inject_channel_id(raw: dict) -> dict:
            if "channel_id" not in raw or not raw["channel_id"]:
                raw = dict(raw)
                raw["channel_id"] = self._resolve_channel_id(env)
            return raw
```

Extend to also translate in_reply_to → cleanup_inbound_message_id:

```python
        def _inject_channel_id(raw: dict) -> dict:
            if "channel_id" not in raw or not raw["channel_id"]:
                raw = dict(raw)
                raw["channel_id"] = self._resolve_channel_id(env)
            # Typing-cleanup translation (#84): bus-level in_reply_to →
            # inbound's Discord message_id via _recent_inbounds. Cache miss
            # / missing metadata degrade to None → cleanup no-ops → TTL net.
            if env.in_reply_to and "cleanup_inbound_message_id" not in raw:
                inbound = self._recent_inbounds.get(env.in_reply_to)
                if inbound:
                    discord_meta = (inbound.metadata or {}).get("discord") or {}
                    cid = discord_meta.get("message_id")
                    if cid:
                        raw = dict(raw)  # copy-on-write if not already copied
                        raw["cleanup_inbound_message_id"] = cid
            return raw
```

Note the `dict(raw)` copy: the existing logic copies on write for channel_id. The new logic does the same conditionally so we don't mutate the caller's dict unexpectedly. (If `raw` was already copied by the channel_id branch, copying again is harmless.)

- [ ] **Step 4: Run tests, verify all pass**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -k "tool_invocation_send_with_in_reply_to or tool_invocation_verbs_clear_typing" -v`

Expected: 4 passed (1 + 3 parameterized).

Run broader suite: `uv run --no-sync pytest packages/agent-core-discord/tests -x -q`

Expected: all green.

Lint clean.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "feat(discord): wire ToolInvocation typing-cleanup via bus in_reply_to (#84)"
```

---

## Phase 6 — Regression lock

### Task 6: Backward-compat lock on `args.reply_to` path

**Files:**
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append 1 test)

- [ ] **Step 1: Write the regression test**

Append to `packages/agent-core-discord/tests/test_endpoint_outbound.py`:

```python
@pytest.mark.asyncio
async def test_existing_clear_pending_ack_via_args_reply_to_path_unchanged(monkeypatch):
    """Pre-existing `if args.reply_to: _clear_pending_ack(args.reply_to)` path
    still works when `cleanup_inbound_message_id` is None. Locks backward-compat
    for Discord-UI threaded-reply outbounds that don't carry bus-level in_reply_to.
    Canary test: should pass green-first if Task 4 is implemented correctly.
    """
    import time

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    # Pre-seed the message-to-reply-to in the fake channel.
    ch._messages["target-mid"] = FakeMessage(id="target-mid", channel_id="500")
    ep._awaiting_reply_ids.add("target-mid")
    ep._awaiting_reply_ids_timestamps["target-mid"] = time.monotonic()
    try:
        env = _envelope(
            "e", "agent-test", "discord-test",
            _toolcall("send", {
                "channel_id": "500",
                "text": "discord-ui threaded reply",
                "reply_to": "target-mid",  # Discord UI feature, NOT bus in_reply_to
            }),
        )
        # Note: env.in_reply_to NOT set; only args.reply_to is.
        await ep.deliver(env)
        # The pre-existing args.reply_to cleanup path cleared typing.
        assert "target-mid" not in ep._awaiting_reply_ids
        assert "target-mid" not in ep._awaiting_reply_ids_timestamps
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run test, verify it passes green-first**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_existing_clear_pending_ack_via_args_reply_to_path_unchanged -v`

Expected: PASS green-first. If it fails, that's diagnostic information that the `args.reply_to` path has drifted (an unintended consequence of Task 4's `_send` cleanup extension), not a #84 bug to fix.

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "test(discord): regression lock for args.reply_to cleanup path (#84)"
```

---

## Phase 7 — Ship

### Task 7: Full gate + Pepper end-of-ticket ping + push + PR + merge

- [ ] **Step 1: Run the full quality gate**

Run: `just check`

Expected: lint clean (ruff), typecheck clean (mypy), import contracts clean, all tests pass. 13 new tests added across this branch:
- Group 1: 4 tests (`test_awaiting_reply_pair.py`)
- Group 2: 5 tests (in `test_endpoint_outbound.py`)
- Group 3: 3 tests (`test_typing_ttl.py`)
- Group 4: 1 test (in `test_endpoint_outbound.py`)

Also explicitly run the **full repo-wide** test suite to apply the #64 broader-suite-check lesson — `_SendArgs` is shared across the package:

Run: `uv run --no-sync pytest packages -q`

Expected: all green.

If any failures: fix and recommit before proceeding. Do not push red.

- [ ] **Step 2: End-of-ticket status ping to Pepper**

Per the working norm (memory: `project_pepper_end_of_ticket_status_ping.md`), surface end-of-ticket status to Pepper before push:

```
🪶 → 🌶️: #84 implementation complete. Branch `feat/issue-84-typing-cleanup`
ready to push. Full gate green: <N> tests passing, lint/mypy/contracts clean.
13 new tests across 4 groups. About to push + open PR + merge to main per
Jeff's standing authorization for high-priority work. Last chance to flag
anything before PR opens. 🪶
```

Wait briefly (~60s) for Pepper's response. If she flags anything, address before proceeding. If silent, proceed — the contract is "surface state, don't block on response."

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feat/issue-84-typing-cleanup
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(#84): typing-cleanup linkage via bus in_reply_to + 90s TTL safety net" --body "$(cat <<'EOF'
## Summary

- Extend `_send`'s post-send cleanup to clear `_awaiting_reply_ids` by the inbound's Discord message_id when the outbound carries bus-level `envelope.in_reply_to`. Translation at two outbound-construction sites (`_deliver_text_message` and `_inject_channel_id`) via the existing `_recent_inbounds` cache from #83.
- New optional `_SendArgs.cleanup_inbound_message_id` field carries the inbound's Discord message_id through to `_send`.
- 90s per-task lazy TTL safety net on `_awaiting_reply_ids` for orphan cases (cache miss, no `in_reply_to`, dismissed-without-reply). Self-healing on pair-management slips via `get(mid, 0)` → huge delta → immediate eviction.
- `_awaiting_reply_ids` gains sibling `_awaiting_reply_ids_timestamps` dict managed at six existing call sites (lines 782, 904, 908, 1095, 1137, 1159).

Closes #84.

### Bug shape clarification (per criterion-watch with Pepper, 2026-05-12)

The original #84 v1 filing was speculative-architecture (no lived symptom) and was closed as no-named-symptom. Jeff then provided a lived instance: typing indicator persists *after* outbound publishes when `in_reply_to` is unset. The corrected framing — the bug is in cleanup, not keep-alive — drove this design.

## Spec & plan

- Spec: `docs/superpowers/specs/2026-05-12-issue-84-typing-cleanup-design.md`
- Plan: `docs/superpowers/plans/2026-05-12-issue-84-typing-cleanup.md`

## Test plan

- [x] Group 1: Pair-management discipline at all six call sites (4 tests).
- [x] Group 2: Cleanup wiring on both outbound paths — TextMessage envelope happy path (the named-symptom regression lock), no-in_reply_to no-op, cache-miss degradation, ToolInvocation `send` parallel path, parameterized across `edit`/`react`/`send_briefing` (5 tests).
- [x] Group 3: 90s TTL safety net — TTL eviction, within-window persistence, self-heal on missing timestamp (3 tests).
- [x] Group 4: Regression lock — pre-existing `args.reply_to` cleanup path unchanged (1 test).
- [x] `just check` green: lint, mypy, contracts, all tests.
- [x] `pytest packages -q` repo-wide green (broader-suite-check per #64 lesson; `_SendArgs` is shared).

## Deferred to followups (out of scope, see spec "Followups")

- WARNING-log on TTL eviction (trigger: observed diagnostic incident).
- Background sweep task for `_awaiting_reply_ids` (trigger: observed memory leak instance).
- TextMessage handler unification via `_resolve_channel_id` (still deferred from #83's Followups #5).
- Buffer-pressure investigation (#87, watch-only `priority:low`).
- Per-verb cleanup divergence tests (trigger: verb-specific symptom).
EOF
)"
```

- [ ] **Step 5: Merge to main**

Per Jeff's standing authorization for high-priority work this cycle, and matching the merge style used for #83 (PR #85) and #64 (PR #86) — classic merge commit + branch delete:

```bash
gh pr merge <PR_NUMBER> --merge --delete-branch
```

Verify:

```bash
gh pr view <PR_NUMBER> --json state,mergedAt,mergeCommit
gh issue view 84 --json state,closedAt
```

Expected: PR `MERGED`, issue `CLOSED`.

- [ ] **Step 6: End-of-ticket close ping to Pepper**

```
🪶 → 🌶️: #84 shipped. PR #<N> merged at <sha>, issue #84 closed.
<N> tests green. Cycle status: <summary if Pepper wants one>.
```

---

## Self-Review

After writing the full plan, checked against the spec:

**Spec coverage:**
- Architecture (translation at both sites + TTL safety net): Tasks 4, 5, 3.
- Components (3 files touched): `args.py` Task 1, `endpoint.py` Tasks 2/3/4/5, tests across Tasks 2/3/4/5/6.
- Data flow (steps 1-6 in spec): seeding at Task 2, translation at Tasks 4/5, cleanup at Task 4, polling-loop TTL at Task 3.
- Error handling table (5 rows): cache-miss (Task 4 test), missing-metadata (Task 4 test), `_clear_pending_ack` raises (pre-existing), TTL eviction mid-flight (Task 3 acceptance), pair-management slip self-heal (Task 3 test 12).
- Pre-existing semantics inherited: documented in spec, not tested per the spec's "out-of-scope test classes."
- Security considerations: documented in spec; no new test surface.
- Testing groups 1-4 (13 tests): Group 1 = 4 (Task 2), Group 2 = 5 (Tasks 4 + 5), Group 3 = 3 (Task 3), Group 4 = 1 (Task 6). Total = 13. ✓
- Followups (5 items): listed in PR body.
- Implementation order: Tasks 1-7 match the spec's 10-step TDD order (schema → pair-management → TTL → translation × 2 → cleanup extension → regression lock → ship).

**Placeholder scan:** No TBDs, no "implement later," no "similar to Task N," no "add appropriate handling." Every code block contains real code; every step has an exact pytest command and expected output.

**Type consistency:**
- `_SendArgs.cleanup_inbound_message_id: str | None`: consistent across Tasks 1, 4, 5.
- `_awaiting_reply_ids_timestamps: dict[str, float]`: consistent across Tasks 2, 3.
- `_TYPING_TTL_SECONDS: float = 90.0`: consistent in Task 3 (class attr) and Task 3 polling-loop reference.
- `time.monotonic()` for timestamps: consistent.

No issues found. Plan stands.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-12-issue-84-typing-cleanup.md`. Two execution options:

1. **Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, two-stage review (spec compliance + code quality) between each. Same flow used successfully for #83 and #64.
2. **Inline Execution** — Execute tasks in this session via `superpowers:executing-plans`, batch with checkpoints.

Which approach?
