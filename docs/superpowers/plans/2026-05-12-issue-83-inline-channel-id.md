# Issue #83 — Inline channel_id + auto-echo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Spec:** `docs/superpowers/specs/2026-05-12-issue-83-inline-channel-id-design.md`
>
> **Branch:** `feat/issue-83-inline-channel-id` (already created; spec already committed at `79db14f`).

**Goal:** Surface `channel_id` and `channel_name` on `<inbox>` wake previews (A) and add a channel resolution chain on `discord-pepper` outbound — explicit → `in_reply_to` cache → hard error (C) — eliminating the manual-channel_id-maintenance failure mode from 2026-05-12.

**Architecture:** Two surfaces independent at the code layer. (A) Helper extraction + per-namespace if-block in `packages/agent-core-channel/src/agent_core_channel/rendering.py`. (C) Channel resolver + bounded LRU+TTL cache in `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, mirroring the existing `claude_code_mcp.py:217` pattern at N=2.

**Tech Stack:** Python 3.12, pytest, pytest-asyncio, ruff, mypy, uv workspace.

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `packages/agent-core-channel/src/agent_core_channel/rendering.py` | A: `_inbox_attrs` helper, per-namespace discord block, callers wired | Modify |
| `packages/agent-core-channel/tests/test_rendering.py` | Helper unit tests (5.1) | Modify (append) |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | C: `_recent_inbounds` cache, `_resolve_channel_id` resolver, verb call-site migration | Modify |
| `packages/agent-core-discord/tests/test_resolve_channel_id.py` | Resolver unit tests, combined raises+caplog (5.2) | Create |
| `packages/agent-core-discord/tests/test_recent_inbounds.py` | Cache lifecycle tests (5.3) | Create |
| `packages/agent-core-discord/tests/test_endpoint_outbound.py` | Verb coverage parameterized tests, regression locks (5.4, 5.6) | Modify (append) |
| `packages/agent-core-discord/tests/test_endpoint_inbound.py` | E2E composition test (5.5), inbound-recording verification | Modify (append) |

---

## Phase 1 — A: rendering helper

### Task 1: Extract `_inbox_attrs` helper with framework attrs (TDD)

**Files:**
- Modify: `packages/agent-core-channel/src/agent_core_channel/rendering.py`
- Test: `packages/agent-core-channel/tests/test_rendering.py`

- [ ] **Step 1: Write the failing test**

Append to `test_rendering.py`:

```python
from agent_core_channel.rendering import _inbox_attrs


def test_inbox_attrs_framework_only_no_metadata():
    env = {
        "id": "abc",
        "kind": "TextMessage",
        "from": "discord-pepper",
        "urgency": "green",
    }
    attrs = _inbox_attrs(env)
    assert attrs == [
        "kind='TextMessage'",
        "from='discord-pepper'",
        "urgency='green'",
        "envelope_id='abc'",
    ]


def test_inbox_attrs_includes_in_reply_to_when_set():
    env = {
        "id": "abc",
        "kind": "TextMessage",
        "from": "discord-pepper",
        "urgency": "green",
        "in_reply_to": "parent-xyz",
    }
    attrs = _inbox_attrs(env)
    assert "in_reply_to='parent-xyz'" in attrs


def test_inbox_attrs_omits_in_reply_to_when_none():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green",
        "in_reply_to": None,
    }
    attrs = _inbox_attrs(env)
    assert not any("in_reply_to" in a for a in attrs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest packages/agent-core-channel/tests/test_rendering.py::test_inbox_attrs_framework_only_no_metadata -v`

Expected: `ImportError: cannot import name '_inbox_attrs'`

- [ ] **Step 3: Add helper to rendering.py**

Add immediately above `def render_envelope` (around line 89):

```python
def _inbox_attrs(env: dict) -> list[str]:
    """Build the `<inbox>` framework attribute list for an envelope.

    Framework attrs: kind, from, urgency, envelope_id, optional in_reply_to.
    Mode flags (preview, render='fallback', batch) are appended by callers
    after this helper returns.
    """
    kind = env.get("kind", "Unknown")
    env_id = env.get("id", "")
    from_ = env.get("from", "")
    urgency = env.get("urgency", "green")
    in_reply_to = env.get("in_reply_to")

    attrs = [
        f"kind='{kind}'",
        f"from='{from_}'",
        f"urgency='{urgency}'",
        f"envelope_id='{env_id}'",
    ]
    if in_reply_to:
        attrs.append(f"in_reply_to='{in_reply_to}'")
    return attrs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest packages/agent-core-channel/tests/test_rendering.py -k inbox_attrs -v`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/rendering.py packages/agent-core-channel/tests/test_rendering.py
git commit -m "feat(channel): _inbox_attrs helper with framework attrs (#83)"
```

### Task 2: Add discord namespace block to helper (TDD)

**Files:**
- Modify: `packages/agent-core-channel/src/agent_core_channel/rendering.py`
- Test: `packages/agent-core-channel/tests/test_rendering.py`

- [ ] **Step 1: Write failing tests**

Append to `test_rendering.py`:

```python
def test_inbox_attrs_emits_channel_id_and_name_when_both_present():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green",
        "metadata": {"discord": {
            "channel_id": "1491445346570866812",
            "channel_name": "#pepper-upgrade",
        }},
    }
    attrs = _inbox_attrs(env)
    assert any('channel_id="1491445346570866812"' in a for a in attrs)
    assert any('channel_name="#pepper-upgrade"' in a for a in attrs)


def test_inbox_attrs_emits_channel_id_alone_when_name_missing():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green",
        "metadata": {"discord": {"channel_id": "X"}},
    }
    attrs = _inbox_attrs(env)
    assert any('channel_id="X"' in a for a in attrs)
    assert not any("channel_name" in a for a in attrs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest packages/agent-core-channel/tests/test_rendering.py -k "channel_id" -v`

Expected: 2 failed (helper doesn't read discord namespace yet).

- [ ] **Step 3: Add quoteattr import and discord block to helper**

In `rendering.py`, add at the top of the imports section (after `import html`):

```python
from xml.sax.saxutils import quoteattr
```

Modify `_inbox_attrs` — append after `if in_reply_to:` block, before `return attrs`:

```python
    # Per-namespace preview surfacing. Add cases as namespaces earn them
    # via documented agent symptoms (rule-of-three before any registry).
    discord = (env.get("metadata") or {}).get("discord") or {}
    if discord:
        cid = discord.get("channel_id")
        if cid:
            attrs.append(f"channel_id={quoteattr(str(cid))}")
            cname = discord.get("channel_name")
            if cname:
                attrs.append(f"channel_name={quoteattr(str(cname))}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest packages/agent-core-channel/tests/test_rendering.py -k "channel_id" -v`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/rendering.py packages/agent-core-channel/tests/test_rendering.py
git commit -m "feat(channel): surface discord channel_id/channel_name on <inbox> preview (#83)"
```

### Task 3: Lock the escape and degradation contracts (TDD)

**Files:**
- Test: `packages/agent-core-channel/tests/test_rendering.py`

- [ ] **Step 1: Write the escape + degradation tests**

```python
def test_inbox_attrs_escapes_special_chars_in_channel_name():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green",
        "metadata": {"discord": {
            "channel_id": "1", "channel_name": "pepper's <chat> & co",
        }},
    }
    attrs = _inbox_attrs(env)
    # quoteattr handles all five XML entities.
    name_attr = next(a for a in attrs if a.startswith("channel_name="))
    assert "&apos;" in name_attr or "'" not in name_attr.split("=", 1)[1].strip('"').strip("'")
    assert "&lt;" in name_attr
    assert "&gt;" in name_attr
    assert "&amp;" in name_attr


def test_inbox_attrs_omits_both_when_channel_id_missing_even_if_name_present():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green",
        "metadata": {"discord": {"channel_name": "#x"}},
    }
    attrs = _inbox_attrs(env)
    assert not any("channel" in a for a in attrs)


def test_inbox_attrs_omits_both_on_empty_strings():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green",
        "metadata": {"discord": {"channel_id": "", "channel_name": ""}},
    }
    attrs = _inbox_attrs(env)
    assert not any("channel" in a for a in attrs)


def test_inbox_attrs_omits_both_on_none_values():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green",
        "metadata": {"discord": {"channel_id": None, "channel_name": None}},
    }
    attrs = _inbox_attrs(env)
    assert not any("channel" in a for a in attrs)


def test_inbox_attrs_handles_empty_discord_dict():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green",
        "metadata": {"discord": {}},
    }
    attrs = _inbox_attrs(env)
    assert not any("channel" in a for a in attrs)


def test_inbox_attrs_handles_missing_metadata():
    env = {"id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green"}
    attrs = _inbox_attrs(env)
    assert len(attrs) == 4  # framework only


def test_inbox_attrs_handles_non_dict_metadata_defensively():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green",
        "metadata": "oops",
    }
    # Should not crash; behavior: returns framework attrs only.
    attrs = _inbox_attrs(env)
    assert len(attrs) == 4
```

- [ ] **Step 2: Run tests**

Run: `uv run --no-sync pytest packages/agent-core-channel/tests/test_rendering.py -k inbox_attrs -v`

Expected: all 10 pass (helper already handles these cases via defensive null-safety).

NOTE: `test_inbox_attrs_handles_non_dict_metadata_defensively` may fail if `(env.get("metadata") or {}).get("discord")` is called on a string. The expression evaluates as: `"oops".get(...)` → AttributeError. If so, harden the helper:

```python
metadata = env.get("metadata") or {}
if not isinstance(metadata, dict):
    metadata = {}
discord = metadata.get("discord") or {}
if not isinstance(discord, dict):
    discord = {}
```

Apply the hardening only if the test fails; otherwise the existing defensive shape is enough.

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-channel/tests/test_rendering.py packages/agent-core-channel/src/agent_core_channel/rendering.py
git commit -m "test(channel): lock _inbox_attrs escape and degradation contracts (#83)"
```

### Task 4: Wire helper into render_envelope (TDD)

**Files:**
- Modify: `packages/agent-core-channel/src/agent_core_channel/rendering.py`
- Test: `packages/agent-core-channel/tests/test_rendering.py`

- [ ] **Step 1: Write the failing integration test**

```python
from agent_core_channel.rendering import render_envelope


def test_render_envelope_includes_channel_attrs_for_discord_inbound():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green",
        "payload": {"text": "hi"},
        "metadata": {"discord": {
            "channel_id": "1491", "channel_name": "#pepper-upgrade",
        }},
    }
    block = render_envelope(env)
    assert 'channel_id="1491"' in block
    assert 'channel_name="#pepper-upgrade"' in block
    assert "<inbox " in block
    assert "</inbox>" in block
```

- [ ] **Step 2: Run test to verify it fails**

Expected: assertion failure — `render_envelope` still constructs attrs locally; doesn't use helper.

- [ ] **Step 3: Wire helper into `render_envelope`**

In `rendering.py`, replace the attrs construction block inside `render_envelope` (lines ~115-122) with:

```python
    attrs = _inbox_attrs(env)
    if is_fallback:
        attrs.append("render='fallback'")
```

Remove the locally-constructed `attrs = [...]` block and the `if in_reply_to:` append that the helper now handles.

- [ ] **Step 4: Run all rendering tests to verify nothing regressed**

Run: `uv run --no-sync pytest packages/agent-core-channel/tests/test_rendering.py -v`

Expected: all tests pass (existing ones still pass, new integration test passes).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/rendering.py packages/agent-core-channel/tests/test_rendering.py
git commit -m "feat(channel): wire _inbox_attrs into render_envelope (#83)"
```

### Task 5: Wire helper into `_render_preview` and `_render_with_truncation` (TDD)

**Files:**
- Modify: `packages/agent-core-channel/src/agent_core_channel/rendering.py`
- Test: `packages/agent-core-channel/tests/test_rendering.py`

- [ ] **Step 1: Write failing tests**

```python
from agent_core_channel.rendering import _render_preview, _render_with_truncation, truncation_marker


def test_render_preview_includes_channel_attrs_for_discord_inbound():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green", "payload": {"text": "hi"},
        "metadata": {"discord": {"channel_id": "1491", "channel_name": "#x"}},
    }
    block = _render_preview(env)
    assert 'channel_id="1491"' in block
    assert "preview='true'" in block


def test_render_with_truncation_includes_channel_attrs_for_discord_inbound():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green", "payload": {"text": "hi"},
        "metadata": {"discord": {"channel_id": "1491"}},
    }
    block = _render_with_truncation(env, body=truncation_marker("abc"), fallback=False)
    assert 'channel_id="1491"' in block
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: failures — these functions still construct attrs locally.

- [ ] **Step 3: Wire helper into both functions**

In `_render_with_truncation` (lines ~246-255), replace the attrs construction with:

```python
    attrs = _inbox_attrs(env)
    if fallback:
        attrs.append("render='fallback'")
```

In `_render_preview` (lines ~278-286), replace the attrs construction with:

```python
    attrs = _inbox_attrs(env)
    attrs.append("preview='true'")
```

- [ ] **Step 4: Run rendering tests + run all channel tests**

Run: `uv run --no-sync pytest packages/agent-core-channel/tests/ -v`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/rendering.py packages/agent-core-channel/tests/test_rendering.py
git commit -m "feat(channel): wire _inbox_attrs into _render_preview and _render_with_truncation (#83)"
```

### Task 6: Verify batch and fallback markers coexist with new attrs (TDD)

**Files:**
- Test: `packages/agent-core-channel/tests/test_rendering.py`

- [ ] **Step 1: Write tests**

```python
from agent_core_channel.rendering import render_item


def test_render_item_batch_preserves_channel_attrs_alongside_batch_attr():
    env1 = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green", "payload": {"text": "a"},
        "metadata": {"discord": {"channel_id": "1491"}},
    }
    env2 = {**env1, "id": "def", "payload": {"text": "b"}}
    item = {"type": "batch", "envelopes": [env1, env2]}
    blocks = render_item(item)
    assert len(blocks) == 2
    for block in blocks:
        assert 'channel_id="1491"' in block
        assert "batch=" in block


def test_render_envelope_fallback_emits_channel_attrs_with_render_fallback():
    # Build an envelope with a kind that triggers _render_fallback_body.
    env = {
        "id": "abc", "kind": "UnknownKind", "from": "discord-pepper",
        "urgency": "green", "payload": {"x": 1},
        "metadata": {"discord": {"channel_id": "1491"}},
    }
    block = render_envelope(env)
    assert 'channel_id="1491"' in block
    assert "render='fallback'" in block
```

- [ ] **Step 2: Run tests; expected pass (helper is already wired)**

Run: `uv run --no-sync pytest packages/agent-core-channel/tests/test_rendering.py -k "batch or fallback" -v`

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-channel/tests/test_rendering.py
git commit -m "test(channel): verify _inbox_attrs coexists with batch and fallback markers (#83)"
```

---

## Phase 2 — Cache infrastructure

### Task 7: Add `_recent_inbounds` cache field + `_record_inbound` method (TDD)

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Test: `packages/agent-core-discord/tests/test_recent_inbounds.py` (create)

- [ ] **Step 1: Create new test file with the recording test**

Create `packages/agent-core-discord/tests/test_recent_inbounds.py`:

```python
"""Tests for DiscordEndpoint._recent_inbounds cache (auto-echo for #83)."""

from __future__ import annotations

import pytest
from agent_core_discord.endpoint import DiscordEndpoint

from agent_core_discord.testing.fakes import FakeChannel, FakeDiscordClient, FakeMessage, FakeUser


async def _make_endpoint(monkeypatch, **kwargs):
    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        _client_factory=lambda **kw: fake,
        **kwargs,
    )
    return ep, fake


@pytest.mark.asyncio
async def test_recent_inbounds_records_inbound_on_publish(monkeypatch):
    """_record_inbound(env) adds env to the cache keyed by env.id."""
    ep, _fake = await _make_endpoint(monkeypatch)
    from agent_core.bus.envelope import Envelope, TextMessagePayload
    from datetime import UTC, datetime

    env = Envelope(
        id="abc",
        correlation_id="c1",
        from_="discord-test",
        to="agent-test",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        metadata={"discord": {"channel_id": "1491"}},
        created_at=datetime.now(UTC),
    )
    ep._record_inbound(env)
    cached = ep._recent_inbounds.get("abc")
    assert cached is not None
    assert cached.id == "abc"
    assert (cached.metadata or {}).get("discord", {}).get("channel_id") == "1491"
```

- [ ] **Step 2: Run test to verify it fails**

Expected: `AttributeError: 'DiscordEndpoint' object has no attribute '_recent_inbounds'`.

- [ ] **Step 3: Add cache field + method to `DiscordEndpoint`**

In `endpoint.py`, find `__init__` and add (mirroring `_pending_acks` style):

```python
    # Cache of recently-published inbounds keyed by envelope_id, for the
    # auto-echo path in _resolve_channel_id (#83). Mirrors the
    # claude_code_mcp.py:_recent_inbounds pattern (N=2 of this shape;
    # extract to shared utility when N=3).
    self._recent_inbounds: "OrderedDict[str, Envelope]" = OrderedDict()
    self._recent_inbounds_max = recent_inbounds_max
    self._recent_inbounds_ttl_seconds = recent_inbounds_ttl_seconds
    self._recent_inbounds_timestamps: dict[str, float] = {}
```

Add parameters to `__init__`:

```python
    recent_inbounds_max: int = 5000,
    recent_inbounds_ttl_seconds: float = 3600.0,
```

Add `_record_inbound` method on `DiscordEndpoint`:

```python
def _record_inbound(self, envelope: Envelope) -> None:
    """Cache an inbound envelope for auto-echo lookups (#83).

    Copies are not strictly needed (envelopes are pydantic models;
    mutation after publish is uncommon), but the cache holds the full
    envelope so _resolve_channel_id can read metadata.discord.channel_id.
    """
    import time
    self._recent_inbounds[envelope.id] = envelope
    self._recent_inbounds.move_to_end(envelope.id)
    self._recent_inbounds_timestamps[envelope.id] = time.monotonic()
```

Imports at top of file (if not present):

```python
from collections import OrderedDict
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_recent_inbounds.py
git commit -m "feat(discord): _recent_inbounds cache field and recorder (#83)"
```

### Task 8: Add LRU eviction (TDD)

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Test: `packages/agent-core-discord/tests/test_recent_inbounds.py`

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_recent_inbounds_lru_eviction_caps_at_max(monkeypatch):
    ep, _fake = await _make_endpoint(monkeypatch, recent_inbounds_max=3)
    from agent_core.bus.envelope import Envelope, TextMessagePayload
    from datetime import UTC, datetime

    def _env(eid: str) -> Envelope:
        return Envelope(
            id=eid, correlation_id="c", from_="x", to="y", kind="TextMessage",
            payload=TextMessagePayload(text=""),
            metadata={"discord": {"channel_id": "1"}},
            created_at=datetime.now(UTC),
        )

    for eid in ("a", "b", "c"):
        ep._record_inbound(_env(eid))
    assert list(ep._recent_inbounds.keys()) == ["a", "b", "c"]
    ep._record_inbound(_env("d"))
    # Oldest (a) evicted.
    assert list(ep._recent_inbounds.keys()) == ["b", "c", "d"]
    assert len(ep._recent_inbounds) == 3
    assert "a" not in ep._recent_inbounds_timestamps
```

- [ ] **Step 2: Run; expected fail (no eviction yet)**

- [ ] **Step 3: Add LRU eviction to `_record_inbound`**

Append to the method:

```python
    while len(self._recent_inbounds) > self._recent_inbounds_max:
        oldest_id, _ = self._recent_inbounds.popitem(last=False)
        self._recent_inbounds_timestamps.pop(oldest_id, None)
```

- [ ] **Step 4: Run test; verify pass**

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_recent_inbounds.py
git commit -m "feat(discord): _recent_inbounds LRU eviction at max (#83)"
```

### Task 9: Add TTL sweep (TDD)

- [ ] **Step 1: Write failing tests**

```python
import time

@pytest.mark.asyncio
async def test_recent_inbounds_ttl_sweep_removes_stale(monkeypatch):
    ep, _fake = await _make_endpoint(monkeypatch, recent_inbounds_ttl_seconds=10.0)
    from agent_core.bus.envelope import Envelope, TextMessagePayload
    from datetime import UTC, datetime

    fresh = Envelope(
        id="fresh", correlation_id="c", from_="x", to="y", kind="TextMessage",
        payload=TextMessagePayload(text=""),
        metadata={"discord": {"channel_id": "1"}},
        created_at=datetime.now(UTC),
    )
    stale = Envelope(
        id="stale", correlation_id="c", from_="x", to="y", kind="TextMessage",
        payload=TextMessagePayload(text=""),
        metadata={"discord": {"channel_id": "1"}},
        created_at=datetime.now(UTC),
    )
    ep._record_inbound(fresh)
    # Insert stale with old timestamp.
    ep._recent_inbounds[stale.id] = stale
    ep._recent_inbounds_timestamps[stale.id] = time.monotonic() - 999.0

    evicted = ep._sweep_recent_inbounds_once()
    assert evicted == 1
    assert "stale" not in ep._recent_inbounds
    assert "fresh" in ep._recent_inbounds
```

- [ ] **Step 2: Run; expected fail**

- [ ] **Step 3: Add `_sweep_recent_inbounds_once`**

```python
def _sweep_recent_inbounds_once(self) -> int:
    """Evict entries older than TTL; return count evicted.

    Walks oldest-first by insertion order; breaks at first non-stale entry
    (same shape as _pending_acks sweep).
    """
    import time
    now = time.monotonic()
    ttl = self._recent_inbounds_ttl_seconds
    evicted = 0
    while self._recent_inbounds:
        oldest_id = next(iter(self._recent_inbounds))
        if now - self._recent_inbounds_timestamps.get(oldest_id, now) <= ttl:
            break
        self._recent_inbounds.popitem(last=False)
        self._recent_inbounds_timestamps.pop(oldest_id, None)
        evicted += 1
    return evicted
```

- [ ] **Step 4: Run tests; verify pass**

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_recent_inbounds.py
git commit -m "feat(discord): _recent_inbounds TTL sweep (#83)"
```

### Task 10: Wire `_record_inbound` into inbound publish paths (TDD)

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Test: `packages/agent-core-discord/tests/test_endpoint_inbound.py` (append)

- [ ] **Step 1: Write failing test**

Append to `test_endpoint_inbound.py`:

```python
@pytest.mark.asyncio
async def test_on_message_records_inbound_in_recent_inbounds_cache(monkeypatch):
    """After on_message publishes, the envelope is recorded for auto-echo."""
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="200"))
    msg = _msg(id="m-cache", content="hi")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        env = handle.published[0]
        # Cache contains the envelope keyed by its id.
        cached = ep._recent_inbounds.get(env.id)
        assert cached is not None
        assert (cached.metadata or {}).get("discord", {}).get("channel_id") == "200"
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run; expected fail (no recording in on_message)**

- [ ] **Step 3: Wire `_record_inbound` into the publish path**

In `endpoint.py`, find the `on_message` handler (around line ~720-800) and after `await self._handle.publish(env)` add:

```python
            self._record_inbound(env)
```

Repeat for `on_reaction_add` and all `on_raw_*` paths that publish envelopes. Grep for `handle.publish(` and `self._handle.publish(` to find all sites.

- [ ] **Step 4: Run test; verify pass**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_inbound.py -k records_inbound -v`

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_inbound.py
git commit -m "feat(discord): record inbounds in _recent_inbounds on publish (#83)"
```

---

## Phase 3 — C: channel resolution chain

### Task 11: Add `_resolve_channel_id` resolver (TDD, combined raises+caplog)

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Test: `packages/agent-core-discord/tests/test_resolve_channel_id.py` (create)

- [ ] **Step 1: Create test file with precedence + hard-error tests**

```python
"""Tests for DiscordEndpoint._resolve_channel_id (auto-echo chain for #83)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from agent_core_discord.endpoint import DiscordEndpoint, _ToolError

from agent_core.bus.envelope import Envelope, TextMessagePayload, ToolInvocationPayload
from agent_core_discord.testing.fakes import FakeDiscordClient


async def _make_endpoint(monkeypatch):
    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="d", target="t", token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    return ep


def _outbound(*, channel_id=None, in_reply_to=None, eid="out-1"):
    md = {}
    if channel_id is not None:
        md["discord"] = {"channel_id": channel_id}
    return Envelope(
        id=eid, correlation_id="c", from_="agent", to="d",
        kind="TextMessage", payload=TextMessagePayload(text=""),
        in_reply_to=in_reply_to, metadata=md,
        created_at=datetime.now(UTC),
    )


def _inbound(*, eid="in-1", channel_id="1491"):
    return Envelope(
        id=eid, correlation_id="c", from_="d", to="agent",
        kind="TextMessage", payload=TextMessagePayload(text="hi"),
        metadata={"discord": {"channel_id": channel_id}},
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_resolve_explicit_channel_id_wins_over_cache_hit(monkeypatch):
    """Topic-override invariant: explicit always wins, in_reply_to ignored."""
    ep = await _make_endpoint(monkeypatch)
    ep._record_inbound(_inbound(eid="in-1", channel_id="from-cache"))
    out = _outbound(channel_id="explicit-channel", in_reply_to="in-1")
    assert ep._resolve_channel_id(out) == "explicit-channel"


@pytest.mark.asyncio
async def test_resolve_returns_cached_channel_id_on_cache_hit_when_explicit_missing(monkeypatch):
    ep = await _make_endpoint(monkeypatch)
    ep._record_inbound(_inbound(eid="in-1", channel_id="from-cache"))
    out = _outbound(in_reply_to="in-1")
    assert ep._resolve_channel_id(out) == "from-cache"


@pytest.mark.asyncio
async def test_resolve_raises_and_logs_when_neither_explicit_nor_in_reply_to_set(monkeypatch, caplog):
    ep = await _make_endpoint(monkeypatch)
    out = _outbound()  # no channel, no in_reply_to
    with caplog.at_level(logging.WARNING):
        with pytest.raises(_ToolError, match="cannot determine channel"):
            ep._resolve_channel_id(out)
    assert "no_explicit_no_in_reply_to" in caplog.text


@pytest.mark.asyncio
async def test_resolve_raises_and_logs_on_cache_miss_never_recorded(monkeypatch, caplog):
    ep = await _make_endpoint(monkeypatch)
    out = _outbound(in_reply_to="never-was")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(_ToolError):
            ep._resolve_channel_id(out)
    assert "cache_miss" in caplog.text


@pytest.mark.asyncio
async def test_resolve_raises_and_logs_when_cached_inbound_has_no_channel_id(monkeypatch, caplog):
    ep = await _make_endpoint(monkeypatch)
    # Cached inbound with empty channel_id (defensive).
    bad = Envelope(
        id="in-1", correlation_id="c", from_="d", to="agent", kind="TextMessage",
        payload=TextMessagePayload(text=""),
        metadata={"discord": {"channel_id": ""}},
        created_at=datetime.now(UTC),
    )
    ep._record_inbound(bad)
    out = _outbound(in_reply_to="in-1")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(_ToolError):
            ep._resolve_channel_id(out)
    assert "cached_inbound_missing_channel_id" in caplog.text


@pytest.mark.asyncio
async def test_resolve_error_message_is_unified_across_sub_causes(monkeypatch):
    """Same _ToolError message regardless of which sub-cause triggered it."""
    ep = await _make_endpoint(monkeypatch)
    # no_explicit case
    with pytest.raises(_ToolError) as exc_neither:
        ep._resolve_channel_id(_outbound())
    # cache_miss case
    with pytest.raises(_ToolError) as exc_miss:
        ep._resolve_channel_id(_outbound(in_reply_to="ghost"))
    assert str(exc_neither.value) == str(exc_miss.value)
```

- [ ] **Step 2: Run; expected ImportError (method doesn't exist)**

- [ ] **Step 3: Add `_resolve_channel_id` to DiscordEndpoint**

```python
def _resolve_channel_id(self, outbound: Envelope) -> str:
    """Resolve channel_id with precedence:
    1. Explicit metadata.discord.channel_id (preserves current behavior).
    2. Fallback: in_reply_to -> _recent_inbounds lookup (auto-echo).
    3. Hard error -- refuse to guess.

    Sub-causes for the failure path are logged at WARNING; the
    agent-facing _ToolError message is unified.
    """
    # 1. Explicit always wins.
    discord_meta = (outbound.metadata or {}).get("discord") or {}
    if explicit := discord_meta.get("channel_id"):
        return explicit

    # 2. Auto-echo via in_reply_to cache lookup.
    if outbound.in_reply_to:
        inbound = self._recent_inbounds.get(outbound.in_reply_to)
        if inbound:
            inbound_discord = (inbound.metadata or {}).get("discord") or {}
            if cid := inbound_discord.get("channel_id"):
                return cid
            logger.warning(
                "channel_id resolution failed: cached_inbound_missing_channel_id, "
                "in_reply_to=%s", outbound.in_reply_to,
            )
        else:
            logger.warning(
                "channel_id resolution failed: cache_miss, in_reply_to=%s",
                outbound.in_reply_to,
            )
    else:
        logger.warning(
            "channel_id resolution failed: no_explicit_no_in_reply_to, "
            "outbound_id=%s", outbound.id,
        )

    raise _ToolError(
        "cannot determine channel — set metadata.discord.channel_id "
        "explicitly, or set in_reply_to so auto-echo can resolve."
    )
```

Ensure `logger` is defined at module top: `logger = logging.getLogger(__name__)` and `import logging` are present.

- [ ] **Step 4: Run all resolver tests; verify pass**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_resolve_channel_id.py -v`

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_resolve_channel_id.py
git commit -m "feat(discord): _resolve_channel_id chain with sub-cause logging (#83)"
```

### Task 12: Add eviction-sub-cause resolve tests (TDD)

Verify TTL-evicted and LRU-evicted paths go through the same cache_miss log.

- [ ] **Step 1: Write tests**

```python
@pytest.mark.asyncio
async def test_resolve_raises_and_logs_on_cache_miss_after_ttl_eviction(monkeypatch, caplog):
    ep = await _make_endpoint(monkeypatch)
    ep._recent_inbounds_ttl_seconds = 10.0
    ep._record_inbound(_inbound(eid="aged", channel_id="X"))
    import time
    ep._recent_inbounds_timestamps["aged"] = time.monotonic() - 999.0
    ep._sweep_recent_inbounds_once()
    out = _outbound(in_reply_to="aged")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(_ToolError):
            ep._resolve_channel_id(out)
    assert "cache_miss" in caplog.text


@pytest.mark.asyncio
async def test_resolve_raises_and_logs_on_cache_miss_after_lru_eviction(monkeypatch, caplog):
    ep = await _make_endpoint(monkeypatch)
    ep._recent_inbounds_max = 1
    ep._record_inbound(_inbound(eid="first", channel_id="X"))
    ep._record_inbound(_inbound(eid="second", channel_id="Y"))  # evicts first
    out = _outbound(in_reply_to="first")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(_ToolError):
            ep._resolve_channel_id(out)
    assert "cache_miss" in caplog.text


@pytest.mark.asyncio
async def test_resolve_raises_and_logs_on_cache_miss_after_daemon_restart(monkeypatch, caplog):
    """Fresh endpoint instance simulates cold start; cache empty."""
    ep = await _make_endpoint(monkeypatch)
    out = _outbound(in_reply_to="pre-restart")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(_ToolError):
            ep._resolve_channel_id(out)
    assert "cache_miss" in caplog.text
```

- [ ] **Step 2: Run; expected pass (logic already handles these via the unified cache_miss path)**

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-discord/tests/test_resolve_channel_id.py
git commit -m "test(discord): cover TTL, LRU, cold-start cache_miss paths in resolver (#83)"
```

### Task 13: Migrate verb call sites to `_resolve_channel_id` (TDD per verb)

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Test: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append parameterized verb-coverage test)

- [ ] **Step 1: Identify call sites**

Run: `grep -n 'metadata.*discord.*channel_id\|metadata\["discord"\]\["channel_id"\]\|\.discord\.get."channel_id"' packages/agent-core-discord/src/agent_core_discord/endpoint.py`

Lock the list of sites — expected ~5-7 in `_send`, `_send_briefing`, `_edit`, `_react`, `_send_typing`, plus the TextMessage envelope handler.

- [ ] **Step 2: Write the verb-coverage parameterized test (append to test_endpoint_outbound.py)**

```python
import pytest


@pytest.mark.parametrize("verb_name,args_extra", [
    ("send", {"text": "hi"}),
    ("edit", {"message_id": "m-edit", "text": "new"}),
    ("react", {"message_id": "m-react", "emoji": "👍"}),
    ("send_typing", {"duration_seconds": 0.5}),
    ("send_briefing", {
        "date_line": "test", "focus": "A", "calendar": "B",
        "critical_items": [], "warning_items": [],
    }),
])
@pytest.mark.asyncio
async def test_auto_echo_resolves_channel_via_in_reply_to_across_verbs(
    monkeypatch, verb_name, args_extra
):
    """Every verb that needs a channel routes via _resolve_channel_id."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    # Seed cache with an inbound pointing at channel 200.
    from agent_core.bus.envelope import Envelope, TextMessagePayload
    from datetime import UTC, datetime
    inbound = Envelope(
        id="inbound-1", correlation_id="c", from_="discord-test", to="agent-test",
        kind="TextMessage", payload=TextMessagePayload(text="hi"),
        metadata={"discord": {"channel_id": "200"}},
        created_at=datetime.now(UTC),
    )
    ep._record_inbound(inbound)
    # Pre-create message for verbs that need one.
    if verb_name in ("edit", "react"):
        ch._messages[args_extra["message_id"]] = FakeMessage(
            id=args_extra["message_id"], channel_id="200",
        )
    try:
        env = _envelope(
            "e", "agent-test", "discord-test",
            _toolcall(verb_name, {**args_extra}),  # NO channel_id
        )
        env.in_reply_to = "inbound-1"
        await ep.deliver(env)
        # The verb routed to channel 200 via the cache hit (no error ack).
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][-1]
        assert not ack.payload.note.lower().startswith("error:"), \
            f"verb={verb_name} should have resolved channel via in_reply_to"
    finally:
        await ep.stop()
```

- [ ] **Step 3: Run; expected mixed fail (verbs not yet migrated)**

- [ ] **Step 4: Migrate each call site one at a time**

For each call site identified in Step 1:

  a. Locate the line reading `metadata.discord.channel_id` (or pydantic args.channel_id with fallback).
  b. Replace with call to `self._resolve_channel_id(env)`.
  c. Wrap in `try/except _ToolError as e:` if not already; publish yellow Ack with `note=f"error: {e}"`.
  d. Run the parameterized test after each migration; verify one more verb passes per migration.
  e. Commit per verb migrated:

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py
git commit -m "refactor(discord): _send routes channel via _resolve_channel_id (#83)"
```

Repeat for each verb. Estimated 5-7 commits.

- [ ] **Step 5: Run full discord test suite to verify no regressions**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests -v`

Expected: all passing including the new parameterized verb test (5 parametrized cases).

- [ ] **Step 6: Final commit for verb-coverage test**

```bash
git add packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "test(discord): parameterized auto-echo coverage across verbs (#83)"
```

---

## Phase 4 — Integration

### Task 14: End-to-end composition test (TDD)

**Files:**
- Test: `packages/agent-core-discord/tests/test_endpoint_inbound.py` (append)

- [ ] **Step 1: Write the E2E test**

```python
@pytest.mark.asyncio
async def test_inbound_to_outbound_routes_correctly_via_in_reply_to_only(monkeypatch):
    """
    Issue #83 named-symptom regression lock:
    1. Inbound arrives in channel A.
    2. Wake preview surfaces channel_id (A).
    3. Agent calls send(in_reply_to=<inbound>) without explicit channel_id.
    4. Outbound posts to channel A via _resolve_channel_id cache lookup (C).
    """
    ep, handle, fake = await _start_endpoint(monkeypatch)
    ch_a = FakeChannel(id="200", name="pepper-upgrade", guild_id="g1")
    fake.add_channel(ch_a)
    inbound_msg = _msg(id="m-in", channel_id="200", content="hi from #pepper-upgrade")
    inbound_msg.channel = fake.get_channel("200")
    try:
        # 1. Inbound arrives.
        await fake.fire("on_message", inbound_msg)
        env = handle.published[0]
        assert (env.metadata or {}).get("discord", {}).get("channel_id") == "200"
        # 2. (A) Preview-side surfacing covered by Phase 1 tests.
        # 3+4. Agent send with in_reply_to only.
        from agent_core.bus.envelope import Envelope, TextMessagePayload
        from datetime import UTC, datetime
        out = Envelope(
            id="agent-reply", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage", payload=TextMessagePayload(text="reply"),
            in_reply_to=env.id, metadata={},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(out)
        # 4. Verify outbound posted to channel 200.
        assert len(ch_a.sent) == 1
        assert ch_a.sent[0]["content"] == "reply"
        # No error ack.
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert all(not a.payload.note.lower().startswith("error:") for a in acks)
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run; verify pass (full pipeline now wired)**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_inbound.py::test_inbound_to_outbound_routes_correctly_via_in_reply_to_only -v`

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-discord/tests/test_endpoint_inbound.py
git commit -m "test(discord): E2E composition lock for #83 named-symptom"
```

---

## Phase 5 — Regression locks

### Task 15: Verify pre-#83 behavior unchanged

**Files:**
- Test: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append)

- [ ] **Step 1: Write regression tests**

```python
@pytest.mark.asyncio
async def test_explicit_channel_id_still_routes_correctly_unchanged(monkeypatch):
    """The pre-#83 explicit-channel_id path keeps working."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="999")
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e", "agent-test", "discord-test",
            _toolcall("send", {"channel_id": "999", "text": "explicit"}),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        assert ch.sent[0]["content"] == "explicit"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_reply_tool_inheritance_path_unchanged(monkeypatch):
    """reply()-style outbounds (channel_id pre-set via reply inheritance) work."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="888")
    fake.add_channel(ch)
    from agent_core.bus.envelope import Envelope, TextMessagePayload
    from datetime import UTC, datetime
    try:
        out = Envelope(
            id="reply-1", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage", payload=TextMessagePayload(text="via reply"),
            metadata={"discord": {"channel_id": "888"}},  # pre-set by reply()
            created_at=datetime.now(UTC),
        )
        await ep.deliver(out)
        assert len(ch.sent) == 1
        assert ch.sent[0]["content"] == "via reply"
    finally:
        await ep.stop()
```

The existing `test_textmessage_without_channel_returns_error` already locks the third regression case; do NOT modify it.

- [ ] **Step 2: Run; verify pass**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -k "regression or unchanged or returns_error" -v`

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "test(discord): regression locks for pre-#83 routing behavior"
```

---

## Phase 6 — Ship

### Task 16: Full gate + push + PR

- [ ] **Step 1: Run the full quality gate**

Run: `just check`

Expected: lint clean, typecheck clean, contracts clean, all tests pass.

If any failures: fix and recommit before proceeding. Do not push red.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feat/issue-83-inline-channel-id
```

- [ ] **Step 3: Open the PR**

```bash
gh pr create --title "feat(#83): inline channel_id preview + auto-echo on discord-pepper" --body "$(cat <<'EOF'
## Summary

- Surface `channel_id` and `channel_name` on `<inbox>` wake previews via a per-namespace if-block in a new `_inbox_attrs` helper (rendering.py).
- Add channel resolution chain on `discord-pepper` outbound: explicit `metadata.discord.channel_id` → `in_reply_to` → `_recent_inbounds` cache → hard error. Explicit always wins; refuse-to-guess preserved as floor.
- Bounded LRU+TTL `_recent_inbounds` cache mirroring `claude_code_mcp.py` pattern at N=2.

Closes #83.

## Spec

`docs/superpowers/specs/2026-05-12-issue-83-inline-channel-id-design.md`

## Test plan

- [x] `_inbox_attrs` helper unit tests cover framework attrs, discord namespace, special-char escape, defensive degradation, partial metadata.
- [x] `_resolve_channel_id` unit tests cover precedence, all hard-error sub-causes (combined raises + caplog), unified error message.
- [x] `_recent_inbounds` cache lifecycle tests (record, LRU, TTL).
- [x] Verb coverage parameterized test across `send`, `edit`, `react`, `send_typing`, `send_briefing`.
- [x] E2E composition test for the 2026-05-12 named-symptom regression.
- [x] Regression locks for pre-#83 explicit-channel routing and reply-inheritance paths.
- [x] `just check` green: lint, typecheck, contracts, full test suite.

## Followups (separate tickets, out of scope)

- Retrofit `quoteattr` escape on existing framework `<inbox>` attrs (defensive).
- Make `in_reply_to` prominent on `mcp__agent-core__send` tool surface.
- Extract `RecentInboundsCache` shared utility when a third endpoint needs the pattern (currently N=2: `claude_code_mcp` + `discord-pepper`).
- Structured detail on the error Ack if unified message proves insufficient.
EOF
)"
```

- [ ] **Step 4: Confirm CI passes on the PR**

If CI is configured, wait for it; if green, the PR is ready for merge. Merge call is Jeff's, per project convention.

---

## Self-Review

**Spec coverage:** Every section of the spec has at least one task that implements it.
- Architecture → Tasks 1, 7, 11 (helper, cache, resolver).
- Components → Tasks 1-6 (rendering), 7-10 (cache), 11-13 (resolver + verbs).
- Data flow scenarios → Tasks 4, 14 (preview surfacing, E2E composition).
- Error handling → Tasks 11, 12 (hard-error sub-causes + logging).
- Testing groups 5.1-5.6 → Tasks 1-6, 11-12, 7-9, 13, 14, 15.

**Placeholder scan:** No TBD/TODO/fill-in references. Every step has concrete code or a concrete command.

**Type consistency:** `_inbox_attrs` signature is consistent (env: dict → list[str]). `_resolve_channel_id` signature is consistent (outbound: Envelope → str). `_record_inbound` consistent (envelope: Envelope → None).

**Bite-sized check:** Each step is one action; tasks group 4-6 steps. Total: 16 tasks across 6 phases.

## Execution Handoff

This plan is ready to execute. Two execution paths per the writing-plans skill:

1. **Subagent-Driven (recommended):** Dispatch a fresh subagent per task, two-stage review (spec compliance + code quality) between each.
2. **Inline Execution:** Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Subagent-driven is the right shape here — Phase 1, 2, 3 are reasonably independent and the discrete TDD-shaped tasks suit fresh-subagent dispatch.
