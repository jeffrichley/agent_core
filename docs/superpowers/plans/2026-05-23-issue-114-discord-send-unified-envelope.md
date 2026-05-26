# Issue #114 — Unified `discord_send` envelope shape Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Spec:** `docs/superpowers/specs/2026-05-23-issue-114-discord-send-unified-envelope-design.md` (committed at `eabe993`).
>
> **Branch:** `feat/issue-114-discord-send-unified-envelope` (worktree at `.worktrees/issue-114-discord-send/`).
>
> **Issue:** [#114](https://github.com/jeffrichley/agent_core/issues/114).

**Goal:** Add one canonical `tool=discord_send` to the Discord adapter and a strict-mode shape validator that publishes failed-delivery `Acknowledgment` envelopes (urgency=yellow) on unrecognized fields, closing the silent-drop class on the discord-send surface without touching the bus envelope schema.

**Architecture:** New pure-function `shape_validator` module owns a catalog of recognized envelope shapes and returns `Recognized(shape_name, deprecation_log_line_or_None)` or `Unrecognized(fields, canonical_equivalent)`. `DiscordEndpoint.deliver()` calls the validator at the top, gates dispatch on recognized, and publishes a failed-delivery `Acknowledgment` via the existing `_reply()` machinery on unrecognized. New `_DiscordSendArgs` Pydantic model with `extra="forbid"` is the second strict layer at dispatch time. Old tool names (`send`, `send_discord_message`) and the existing `TextMessage + metadata.discord.*` routes continue to deliver with one structured deprecation-log line per call.

**Tech Stack:** Python 3.12, pytest, pytest-asyncio, Pydantic v2, ruff, mypy, uv workspace.

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `packages/agent-core-discord/src/agent_core_discord/shape_validator.py` | NEW — `Recognized` / `Unrecognized` dataclasses, recognized-shape catalog, `validate()` entry point | Create |
| `packages/agent-core-discord/tests/test_shape_validator.py` | NEW — pure-function unit tests for validator (catalog hits, unrecognized detection, nested-path, multi-field) | Create |
| `packages/agent-core-discord/src/agent_core_discord/args.py` | Add `_DiscordSendArgs` Pydantic model with `extra="forbid"` | Modify |
| `packages/agent-core-discord/tests/test_discord_send_args.py` | NEW — Pydantic-validation tests for `_DiscordSendArgs` | Create |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | `_TOOL_ALIASES` add `discord_send`, `_dispatch` route it, `deliver()` call validator, `_send` extend empty-send message | Modify |
| `packages/agent-core-discord/tests/test_endpoint_outbound.py` | Load-bearing regression test, back-compat regression tests, multi-field-unrecognized / empty-send / validator-exception integration tests | Modify (append) |

---

## Phase 1 — Validator module

### Task 1: `shape_validator.py` skeleton with dataclasses and module structure

**Files:**
- Create: `packages/agent-core-discord/src/agent_core_discord/shape_validator.py`
- Test: `packages/agent-core-discord/tests/test_shape_validator.py`

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-discord/tests/test_shape_validator.py`:

```python
"""Unit tests for shape_validator — the pure-function recognized-shape catalog
for the Discord adapter's strict-mode envelope validation.

The validator is PURE: no I/O, no Discord client, no chrome.* / discord.*
imports. These tests construct envelopes directly and assert on the
returned ShapeValidation value.
"""

from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import (
    Envelope,
    TextMessagePayload,
    ToolInvocationPayload,
)
from agent_core_discord.shape_validator import (
    Recognized,
    Unrecognized,
    validate,
)


def _make_env(*, kind, payload, metadata=None, from_="test-sender"):
    """Helper: build an Envelope with sensible defaults for validator tests."""
    return Envelope(
        id="env-abc",
        correlation_id="corr-1",
        to="discord-test",
        kind=kind,
        payload=payload,
        metadata=metadata or {},
        created_at=datetime.now(UTC),
        from_=from_,
    )


def test_recognized_and_unrecognized_are_frozen_dataclasses():
    """Both ShapeValidation variants are frozen so test assertions can compare
    by value and Recognized/Unrecognized can be used as dict keys later."""
    r = Recognized(shape_name="x", deprecation_log_line=None)
    u = Unrecognized(fields=["a"], canonical_equivalent="b")
    with pytest.raises(Exception):
        r.shape_name = "y"  # frozen
    with pytest.raises(Exception):
        u.fields = ["c"]    # frozen
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_shape_validator.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_core_discord.shape_validator'`.

- [ ] **Step 3: Write minimal implementation**

Create `packages/agent-core-discord/src/agent_core_discord/shape_validator.py`:

```python
"""shape_validator — pure-function recognized-shape catalog for the Discord
adapter's strict-mode envelope validation.

Closes the silent-drop class on the discord-send surface (#114). The
adapter calls validate(envelope) at the top of deliver(): on
Unrecognized, a yellow failed-delivery Acknowledgment is published to
the sender with the canonical equivalent named; on Recognized + a
deprecation_log_line, a structured log fires and dispatch proceeds; on
Recognized + None (the canonical shape), dispatch proceeds silently.

PURE module: no I/O, no Discord client, no global state. Unit-testable
in isolation. See docs/superpowers/specs/2026-05-23-issue-114-discord-
send-unified-envelope-design.md for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_core.bus.envelope import Envelope


@dataclass(frozen=True)
class Recognized:
    """Validator outcome: the envelope matches a known shape.

    shape_name: stable identifier for the matched shape, used as the
        aggregation key in deprecation-readiness telemetry.
    deprecation_log_line: human-readable message for the structured
        deprecation log when the shape is legacy. None for the canonical
        shape (no log emitted for the happy path).
    """

    shape_name: str
    deprecation_log_line: str | None


@dataclass(frozen=True)
class Unrecognized:
    """Validator outcome: the envelope carries fields the adapter does
    not route.

    fields: the unrecognized field path(s). For nested-path inputs
        ('metadata.discord.foo.bar.baz' with 'foo' unknown), this is
        the first unknown prefix ('metadata.discord.foo'), not the
        leaves.
    canonical_equivalent: human-readable hint for the sender's failed-
        delivery Acknowledgment note, naming the canonical way to send
        the same intent.
    """

    fields: list[str]
    canonical_equivalent: str


ShapeValidation = Recognized | Unrecognized


def validate(envelope: Envelope) -> ShapeValidation:
    """Validate that the Discord adapter has routing for every field on
    envelope. Returns Recognized(shape_name, deprecation_log_line_or_None)
    or Unrecognized(fields, canonical_equivalent).

    Stub: real implementation lands in Tasks 2-4.
    """
    raise NotImplementedError("validate() stub — implementation in Tasks 2-4")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_shape_validator.py -v
```

Expected: PASS — `test_recognized_and_unrecognized_are_frozen_dataclasses PASSED`.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-114-discord-send
git add packages/agent-core-discord/src/agent_core_discord/shape_validator.py packages/agent-core-discord/tests/test_shape_validator.py
git commit -m "feat(#114): shape_validator skeleton + frozen dataclasses"
```

---

### Task 2: validate() — recognized ToolInvocation tool shapes (canonical, send, send_discord_message)

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/shape_validator.py`
- Test: `packages/agent-core-discord/tests/test_shape_validator.py`

- [ ] **Step 1: Write the failing test**

Append to `test_shape_validator.py`:

```python
def test_canonical_discord_send_returns_recognized_no_deprecation():
    """The canonical tool=discord_send + canonical args is the new happy
    path; no deprecation log fires."""
    env = _make_env(
        kind="ToolInvocation",
        payload=ToolInvocationPayload(
            tool="discord_send",
            args={"channel_id": "123", "text": "hi"},
        ),
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "canonical_discord_send"
    assert result.deprecation_log_line is None


def test_legacy_tool_send_returns_recognized_with_deprecation():
    """tool=send is the pre-#114 internal canonical; ships as a legacy
    alias after #114 with a deprecation-log line."""
    env = _make_env(
        kind="ToolInvocation",
        payload=ToolInvocationPayload(
            tool="send",
            args={"channel_id": "123", "text": "hi"},
        ),
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "legacy_tool_send"
    assert result.deprecation_log_line is not None
    assert "tool=discord_send" in result.deprecation_log_line


def test_legacy_tool_send_discord_message_returns_recognized_with_deprecation():
    """tool=send_discord_message is the existing public alias; ships as a
    legacy alias after #114 with a deprecation-log line."""
    env = _make_env(
        kind="ToolInvocation",
        payload=ToolInvocationPayload(
            tool="send_discord_message",
            args={"channel_id": "123", "text": "hi", "embeds": [{"title": "x"}]},
        ),
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "legacy_tool_send_discord_message"
    assert result.deprecation_log_line is not None
    assert "tool=discord_send" in result.deprecation_log_line


def test_other_tool_invocation_is_non_send_tool():
    """Non-send tools (edit, react, fetch, etc.) are outside the
    validator's strict-mode scope. They return Recognized with shape
    'non_send_tool' so deliver() does not gate on them."""
    env = _make_env(
        kind="ToolInvocation",
        payload=ToolInvocationPayload(
            tool="edit",
            args={"channel_id": "123", "message_id": "456", "text": "edited"},
        ),
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "non_send_tool"
    assert result.deprecation_log_line is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_shape_validator.py -v
```

Expected: 4 new tests FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement validate() for ToolInvocation**

Replace the stub `validate()` body in `shape_validator.py` and add the supporting `_KNOWN_*` sets + `_validate_tool_invocation` helper. Insert above `validate()`:

```python
# Recognized arg names for the new canonical _DiscordSendArgs model.
# Keep in lockstep with packages/agent-core-discord/src/agent_core_discord/
# args.py:_DiscordSendArgs field set. The test_canonical_keys_in_sync test
# (Task 5) asserts the lockstep.
_KNOWN_CANONICAL_SEND_ARGS: frozenset[str] = frozenset({
    "channel_id",
    "text",
    "embeds",
    "files",
    "reply_to",
    "allowed_mentions",
    "components",
    "cleanup_inbound_message_id",
})

# Recognized arg names for the existing _SendArgs (legacy `tool=send` /
# `tool=send_discord_message` routes). Must stay in lockstep with
# args.py:_SendArgs. allowed_mentions and components are NOT in this
# set — those are canonical-only future-proof slots, not exposed on the
# legacy routes.
_KNOWN_LEGACY_SEND_ARGS: frozenset[str] = frozenset({
    "channel_id",
    "text",
    "embeds",
    "reply_to",
    "files",
    "cleanup_inbound_message_id",
})


def _validate_tool_invocation(envelope: Envelope) -> ShapeValidation:
    payload = envelope.payload
    tool = getattr(payload, "tool", "") or ""
    args = getattr(payload, "args", {}) or {}

    if tool == "discord_send":
        unrecognized = _unrecognized_arg_keys(args, _KNOWN_CANONICAL_SEND_ARGS)
        if unrecognized:
            return Unrecognized(
                fields=[f"args.{k}" for k in unrecognized],
                canonical_equivalent=(
                    "tool=discord_send accepts "
                    f"{sorted(_KNOWN_CANONICAL_SEND_ARGS)}"
                ),
            )
        return Recognized("canonical_discord_send", None)

    if tool in ("send", "send_discord_message"):
        unrecognized = _unrecognized_arg_keys(args, _KNOWN_LEGACY_SEND_ARGS)
        if unrecognized:
            return Unrecognized(
                fields=[f"args.{k}" for k in unrecognized],
                canonical_equivalent=(
                    f"tool=discord_send with {', '.join(unrecognized)} in args"
                ),
            )
        return Recognized(
            f"legacy_tool_{tool}",
            f"deprecated_shape: tool={tool} — use tool=discord_send",
        )

    # All other tools (edit, react, fetch, list_channels, create_poll, etc.)
    # are outside the strict-mode scope. deliver() does not block on them.
    return Recognized("non_send_tool", None)


def _unrecognized_arg_keys(args: object, known: frozenset[str]) -> list[str]:
    if not isinstance(args, dict):
        return []
    return sorted(set(args.keys()) - known)
```

Replace the stub `validate()` body:

```python
def validate(envelope: Envelope) -> ShapeValidation:
    if envelope.kind == "ToolInvocation":
        return _validate_tool_invocation(envelope)
    if envelope.kind == "TextMessage":
        # TextMessage handling lands in Task 3.
        raise NotImplementedError("TextMessage validation — Task 3")
    # Kinds outside ToolInvocation / TextMessage (Event, Cancellation,
    # Progress, Acknowledgment) are not in the strict-mode scope; the
    # adapter's existing else-branch handles them with an unsupported-kind
    # warning Ack. The validator returns Recognized so deliver() does not
    # double-handle them.
    return Recognized("non_send_kind", None)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_shape_validator.py -v
```

Expected: 5 tests PASS (the original + 4 new).

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-114-discord-send
git add packages/agent-core-discord/src/agent_core_discord/shape_validator.py packages/agent-core-discord/tests/test_shape_validator.py
git commit -m "feat(#114): shape_validator handles ToolInvocation shapes"
```

---

### Task 3: validate() — recognized TextMessage shapes (4 catalog entries)

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/shape_validator.py`
- Test: `packages/agent-core-discord/tests/test_shape_validator.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_shape_validator.py`:

```python
def test_textmessage_plain_text_returns_recognized_with_deprecation():
    """The most-common legacy shape: TextMessage with channel_id only."""
    env = _make_env(
        kind="TextMessage",
        payload=TextMessagePayload(text="hello"),
        metadata={"discord": {"channel_id": "123"}},
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "legacy_textmessage_plain"
    assert result.deprecation_log_line is not None


def test_textmessage_with_embeds_returns_recognized_with_deprecation():
    """The poster-child shape from #114 — routed since a278c68 but
    still legacy. Embed presence determines the shape_name."""
    env = _make_env(
        kind="TextMessage",
        payload=TextMessagePayload(text=""),
        metadata={"discord": {"channel_id": "123", "embeds": [{"title": "x"}]}},
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "legacy_textmessage_embeds"
    assert result.deprecation_log_line is not None


def test_textmessage_with_reply_to_returns_recognized_with_deprecation():
    """reply_to legacy shape. message_id alias also tested via a fallback
    test below."""
    env = _make_env(
        kind="TextMessage",
        payload=TextMessagePayload(text="reply"),
        metadata={
            "discord": {"channel_id": "123", "reply_to": "456"},
        },
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "legacy_textmessage_reply"
    assert result.deprecation_log_line is not None


def test_textmessage_no_discord_metadata_is_non_discord():
    """TextMessage envelopes with no metadata.discord block at all are
    not discord-bound; validator returns Recognized so deliver() doesn't
    treat the absence of routing as a failure."""
    env = _make_env(
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        metadata={},
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "non_discord_text_message"
    assert result.deprecation_log_line is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_shape_validator.py -v
```

Expected: 4 new tests FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement TextMessage path in validate()**

Insert above `validate()` (after the existing helpers):

```python
# Recognized keys under metadata.discord on OUTBOUND TextMessage
# envelopes. INBOUND-only keys (message_id, guild_id, author_id,
# author_display_name, is_dm) are not in this set — they are set by
# the adapter when publishing inbound, and a sender that sets them on
# an outbound is doing something the adapter does not route. Flag as
# Unrecognized so the silent-drop class is closed (Task 4).
_KNOWN_DISCORD_META_OUTBOUND_KEYS: frozenset[str] = frozenset({
    "channel_id",
    "embeds",
    "reply_to",
})


def _validate_text_message(envelope: Envelope) -> ShapeValidation:
    metadata = envelope.metadata or {}
    discord_meta = metadata.get("discord")

    if discord_meta is None:
        # TextMessage without metadata.discord is not discord-bound.
        # Adapter's existing routing handles this via outbound_channel_id
        # fallback or _ToolError.
        return Recognized("non_discord_text_message", None)

    if not isinstance(discord_meta, dict):
        # metadata.discord is present but malformed (string, list, etc.).
        return Unrecognized(
            fields=["metadata.discord"],
            canonical_equivalent=(
                "tool=discord_send with channel_id in args (metadata.discord "
                "must be an object)"
            ),
        )

    # Unrecognized-key detection lands in Task 4. For now, recognize
    # legacy shapes by the most-discriminating field present.
    if "embeds" in discord_meta:
        return Recognized(
            "legacy_textmessage_embeds",
            "deprecated_shape: TextMessage + metadata.discord.embeds — "
            "use tool=discord_send with embeds in args",
        )
    if "reply_to" in discord_meta:
        return Recognized(
            "legacy_textmessage_reply",
            "deprecated_shape: TextMessage + metadata.discord.reply_to — "
            "use tool=discord_send with reply_to in args",
        )
    if "channel_id" in discord_meta:
        return Recognized(
            "legacy_textmessage_plain",
            "deprecated_shape: TextMessage + metadata.discord.channel_id — "
            "use tool=discord_send with text in args",
        )
    # Ambiguous discord_meta block (e.g., empty {} or only message_id).
    return Recognized(
        "legacy_textmessage_ambiguous",
        "deprecated_shape: TextMessage + metadata.discord without channel_id",
    )
```

Replace the `raise NotImplementedError("TextMessage validation — Task 3")` line in `validate()` with a call:

```python
def validate(envelope: Envelope) -> ShapeValidation:
    if envelope.kind == "ToolInvocation":
        return _validate_tool_invocation(envelope)
    if envelope.kind == "TextMessage":
        return _validate_text_message(envelope)
    return Recognized("non_send_kind", None)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_shape_validator.py -v
```

Expected: 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-114-discord-send
git add packages/agent-core-discord/src/agent_core_discord/shape_validator.py packages/agent-core-discord/tests/test_shape_validator.py
git commit -m "feat(#114): shape_validator handles TextMessage shapes"
```

---

### Task 4: validate() — unrecognized fields, multi-field, and nested-path handling

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/shape_validator.py`
- Test: `packages/agent-core-discord/tests/test_shape_validator.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_shape_validator.py`:

```python
def test_textmessage_with_unrecognized_metadata_field_returns_unrecognized():
    """The load-bearing case: an outbound carries a metadata.discord.*
    field the adapter does not route. Validator must flag it so the Ack
    can be published — silent-drop is what #114 closes."""
    env = _make_env(
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        metadata={"discord": {"channel_id": "123", "mystery_field": "X"}},
    )
    result = validate(env)
    assert isinstance(result, Unrecognized)
    assert result.fields == ["metadata.discord.mystery_field"]
    assert "discord_send" in result.canonical_equivalent
    assert "mystery_field" in result.canonical_equivalent


def test_textmessage_with_multiple_unrecognized_fields_returns_all():
    """Multi-field case enumerates all unrecognized fields in one Ack
    rather than fragmenting across N redeliveries."""
    env = _make_env(
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        metadata={"discord": {
            "channel_id": "123",
            "mystery_field": "X",
            "another_unknown": "Y",
        }},
    )
    result = validate(env)
    assert isinstance(result, Unrecognized)
    # Order is sorted for determinism in the Ack note.
    assert result.fields == [
        "metadata.discord.another_unknown",
        "metadata.discord.mystery_field",
    ]


def test_toolinvocation_canonical_with_unrecognized_arg_returns_unrecognized():
    """args-level strict-mode for the canonical tool. The Pydantic
    extra='forbid' on _DiscordSendArgs catches this at dispatch too;
    the validator catches it before dispatch."""
    env = _make_env(
        kind="ToolInvocation",
        payload=ToolInvocationPayload(
            tool="discord_send",
            args={"channel_id": "123", "text": "hi", "mystery_arg": "X"},
        ),
    )
    result = validate(env)
    assert isinstance(result, Unrecognized)
    assert result.fields == ["args.mystery_arg"]
    assert "discord_send" in result.canonical_equivalent


def test_toolinvocation_legacy_with_unrecognized_arg_returns_unrecognized():
    """Legacy tool with a new field the legacy args model never wired."""
    env = _make_env(
        kind="ToolInvocation",
        payload=ToolInvocationPayload(
            tool="send_discord_message",
            args={"channel_id": "123", "text": "hi", "allowed_mentions": {}},
        ),
    )
    result = validate(env)
    assert isinstance(result, Unrecognized)
    assert result.fields == ["args.allowed_mentions"]
    assert "discord_send" in result.canonical_equivalent
    assert "allowed_mentions" in result.canonical_equivalent
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_shape_validator.py -v
```

Expected: 4 new tests FAIL — the `_validate_text_message` does not yet check unrecognized metadata.discord.* keys; tests for legacy ToolInvocation with non-canonical args should already pass via the existing `_unrecognized_arg_keys` logic.

- [ ] **Step 3: Tighten _validate_text_message to detect unrecognized metadata.discord.* keys**

Modify `_validate_text_message` in `shape_validator.py` — insert the unrecognized-key check immediately after the `isinstance(discord_meta, dict)` guard and before the shape-naming branches:

```python
def _validate_text_message(envelope: Envelope) -> ShapeValidation:
    metadata = envelope.metadata or {}
    discord_meta = metadata.get("discord")

    if discord_meta is None:
        return Recognized("non_discord_text_message", None)

    if not isinstance(discord_meta, dict):
        return Unrecognized(
            fields=["metadata.discord"],
            canonical_equivalent=(
                "tool=discord_send with channel_id in args (metadata.discord "
                "must be an object)"
            ),
        )

    # Unrecognized-key detection. Senders that set metadata.discord.*
    # fields the adapter does not route get a failed-delivery Ack — this
    # is the load-bearing closure of the silent-drop class.
    unrecognized_keys = sorted(
        set(discord_meta.keys()) - _KNOWN_DISCORD_META_OUTBOUND_KEYS
    )
    if unrecognized_keys:
        return Unrecognized(
            fields=[f"metadata.discord.{k}" for k in unrecognized_keys],
            canonical_equivalent=(
                f"tool=discord_send with {', '.join(unrecognized_keys)} in args"
            ),
        )

    # Recognized legacy shape — name by most-discriminating field.
    if "embeds" in discord_meta:
        return Recognized(
            "legacy_textmessage_embeds",
            "deprecated_shape: TextMessage + metadata.discord.embeds — "
            "use tool=discord_send with embeds in args",
        )
    if "reply_to" in discord_meta:
        return Recognized(
            "legacy_textmessage_reply",
            "deprecated_shape: TextMessage + metadata.discord.reply_to — "
            "use tool=discord_send with reply_to in args",
        )
    if "channel_id" in discord_meta:
        return Recognized(
            "legacy_textmessage_plain",
            "deprecated_shape: TextMessage + metadata.discord.channel_id — "
            "use tool=discord_send with text in args",
        )
    return Recognized(
        "legacy_textmessage_ambiguous",
        "deprecated_shape: TextMessage + metadata.discord without channel_id",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_shape_validator.py -v
```

Expected: 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-114-discord-send
git add packages/agent-core-discord/src/agent_core_discord/shape_validator.py packages/agent-core-discord/tests/test_shape_validator.py
git commit -m "feat(#114): shape_validator detects unrecognized fields"
```

---

## Phase 2 — Args model

### Task 5: `_DiscordSendArgs` Pydantic model with extra="forbid"

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/args.py`
- Test: `packages/agent-core-discord/tests/test_discord_send_args.py` (Create)

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-discord/tests/test_discord_send_args.py`:

```python
"""Unit tests for _DiscordSendArgs — the strict canonical args model
for tool=discord_send (#114).

The model uses extra='forbid' as the second strict layer behind the
envelope-level shape_validator: a typo on the args side raises
ValidationError at dispatch, which translates to a yellow Ack via the
existing _ToolError path. No silent drop.
"""

import pytest
from pydantic import ValidationError

from agent_core_discord.args import _DiscordSendArgs


def test_canonical_minimal_text_send_accepted():
    """channel_id + text is the smallest valid canonical args."""
    args = _DiscordSendArgs(channel_id="123", text="hi")
    assert args.channel_id == "123"
    assert args.text == "hi"
    assert args.embeds is None
    assert args.files is None


def test_all_optional_fields_accepted_independently():
    """Each optional field is accepted on its own. Spot-check the field
    set rather than enumerating exhaustively."""
    _DiscordSendArgs(channel_id="1", embeds=[{"title": "x"}])
    _DiscordSendArgs(channel_id="1", files=["/tmp/a"])
    _DiscordSendArgs(channel_id="1", reply_to="456")
    _DiscordSendArgs(channel_id="1", allowed_mentions={"users": []})
    _DiscordSendArgs(channel_id="1", components=[{"type": 1}])
    _DiscordSendArgs(channel_id="1", cleanup_inbound_message_id="789")


def test_missing_channel_id_raises():
    with pytest.raises(ValidationError) as exc:
        _DiscordSendArgs(text="hi")
    assert "channel_id" in str(exc.value)


def test_empty_channel_id_raises():
    """channel_id is required and must be non-empty."""
    with pytest.raises(ValidationError):
        _DiscordSendArgs(channel_id="")


def test_extra_field_rejected_by_forbid():
    """extra='forbid' is the contract: any unknown field at the args
    level raises ValidationError. The shape_validator should catch this
    case BEFORE Pydantic, but this is the defense-in-depth layer."""
    with pytest.raises(ValidationError) as exc:
        _DiscordSendArgs(channel_id="123", text="hi", mystery_field="X")
    msg = str(exc.value)
    assert "mystery_field" in msg or "extra" in msg.lower()


def test_canonical_keys_in_sync_with_validator_catalog():
    """The canonical args field set must stay in lockstep with
    shape_validator._KNOWN_CANONICAL_SEND_ARGS. A drift between them
    would surface as a sender being rejected by one layer but accepted
    by the other."""
    from agent_core_discord.shape_validator import _KNOWN_CANONICAL_SEND_ARGS

    model_fields = set(_DiscordSendArgs.model_fields.keys())
    assert model_fields == _KNOWN_CANONICAL_SEND_ARGS
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_discord_send_args.py -v
```

Expected: 6 tests FAIL with `ImportError: cannot import name '_DiscordSendArgs' from 'agent_core_discord.args'`.

- [ ] **Step 3: Append _DiscordSendArgs to args.py**

Append to `packages/agent-core-discord/src/agent_core_discord/args.py`:

```python
class _DiscordSendArgs(BaseModel):
    """Canonical args for tool=discord_send (#114).

    Pydantic extra='forbid' is the second strict layer behind the
    envelope-level shape_validator. The validator catches unrecognized
    fields BEFORE dispatch (yellow Ack via _reply); ValidationError
    here catches them at dispatch (yellow Ack via _ToolError). Both
    paths surface to the sender — no silent drop.

    The canonical field set must stay in lockstep with
    shape_validator._KNOWN_CANONICAL_SEND_ARGS. The
    test_canonical_keys_in_sync_with_validator_catalog test asserts
    the invariant.

    allowed_mentions and components are future-proof slots: the args
    model accepts them, but the adapter's _send() does not yet pass
    them through to discord.py. A caller that sets them today gets a
    successful send with the option silently unapplied. A future
    ticket adds the wiring in _send when there is named demand; no
    args-model migration needed.
    """

    model_config = ConfigDict(extra="forbid")

    channel_id: str = Field(min_length=1)
    text: str | None = None
    embeds: list[dict[str, Any]] | None = None
    files: list[str] | None = None
    reply_to: str | None = None
    allowed_mentions: dict[str, Any] | None = None
    components: list[dict[str, Any]] | None = None
    cleanup_inbound_message_id: str | None = None
```

Required imports at the top of `args.py` (verify they are already present; add any missing):

```python
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_discord_send_args.py packages/agent-core-discord/tests/test_shape_validator.py -v
```

Expected: 6 args tests PASS + 13 validator tests still PASS. The lockstep test asserts the cross-module invariant.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-114-discord-send
git add packages/agent-core-discord/src/agent_core_discord/args.py packages/agent-core-discord/tests/test_discord_send_args.py
git commit -m "feat(#114): _DiscordSendArgs strict args model"
```

---

## Phase 3 — endpoint.py integration

### Task 6: Wire `discord_send` into _TOOL_ALIASES and _dispatch

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Test: `packages/agent-core-discord/tests/test_endpoint_outbound.py`

- [ ] **Step 1: Write the failing test**

Append to `test_endpoint_outbound.py` (locate existing fixtures and follow their pattern; the test uses the existing `make_endpoint` / fake-client harness):

```python
import pytest


@pytest.mark.asyncio
async def test_dispatch_routes_discord_send_to_internal_send(make_endpoint):
    """tool=discord_send must reach _send via _dispatch. Verifies the
    new entry in _TOOL_ALIASES and the _dispatch table."""
    endpoint, fake_client = make_endpoint()
    await endpoint.start(_FakeBus())

    args = {"channel_id": "123", "text": "hi"}
    env = _build_tool_invocation_envelope(tool="discord_send", args=args)
    await endpoint.deliver(env)
    assert fake_client.last_send_call is not None
    assert fake_client.last_send_call["channel"] == "123"
    assert fake_client.last_send_call["content"] == "hi"
```

(The `make_endpoint`, `_FakeBus`, `_build_tool_invocation_envelope` helpers should already exist in the test module from the existing parameterized verb tests added in #83 / #84. If a helper is missing, add a minimal one alongside the new test — but do NOT refactor existing helpers.)

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_dispatch_routes_discord_send_to_internal_send -v
```

Expected: FAIL with `_ToolError: unknown tool 'discord_send'`.

- [ ] **Step 3: Add the alias and dispatch case**

In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, modify the `_TOOL_ALIASES` dict (currently around line 67) — add the canonical passthrough:

```python
_TOOL_ALIASES: dict[str, str] = {
    "send_discord_message": "send",
    "discord_send": "discord_send",  # #114: canonical passthrough
    "edit_message": "edit",
    "add_reaction": "react",
    "fetch_messages": "fetch",
}
```

Then in `_dispatch` (currently around line 778), add the `discord_send` case immediately BEFORE the `send` case so the canonical name routes through the strict `_DiscordSendArgs` validation:

```python
        if tool == "discord_send":
            from agent_core_discord.args import _DiscordSendArgs
            return await self._send(
                _v(_DiscordSendArgs, _inject_channel_id(args))
            )
        if tool == "send":
            return await self._send(_v(_SendArgs, _inject_channel_id(args)))
```

(Top-of-file import of `_DiscordSendArgs` is preferred; the inline import shown here is acceptable if the import-grouping convention of the file already uses inline imports for newer optional types. Match what is already there.)

- [ ] **Step 4: Run test to verify it passes**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_dispatch_routes_discord_send_to_internal_send -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-114-discord-send
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "feat(#114): wire tool=discord_send through _dispatch"
```

---

### Task 7: Extend `_send` empty-send guard to include `files`

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Test: `packages/agent-core-discord/tests/test_endpoint_outbound.py`

- [ ] **Step 1: Write the failing test**

Append to `test_endpoint_outbound.py`:

```python
@pytest.mark.asyncio
async def test_discord_send_with_no_payload_raises_explicit_error(make_endpoint):
    """tool=discord_send with no text, no embeds, no files must produce
    a clear yellow Ack naming all three options, not just text/embeds."""
    endpoint, _fake_client = make_endpoint()
    await endpoint.start(_FakeBus())

    args = {"channel_id": "123"}
    env = _build_tool_invocation_envelope(tool="discord_send", args=args)
    bus = endpoint._handle  # the fake bus used for outbound Acks
    await endpoint.deliver(env)

    # The Acknowledgment fired with a note listing all three payload
    # options.
    last_ack = bus.last_published_envelope
    assert last_ack.kind == "Acknowledgment"
    assert last_ack.urgency == "yellow"
    assert "text" in last_ack.payload.note
    assert "embeds" in last_ack.payload.note
    assert "files" in last_ack.payload.note
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_discord_send_with_no_payload_raises_explicit_error -v
```

Expected: FAIL — current message says `"send: one of 'text' or 'embeds' is required"` and does not include `files`.

- [ ] **Step 3: Extend the guard message in `_send`**

In `endpoint.py`, find `_send` (currently around line 1386) and change the first line:

```python
    async def _send(self, args: _SendArgs) -> dict:
        if args.text is None and not args.embeds and not args.files:
            raise _ToolError(
                "send: one of 'text', 'embeds', or 'files' is required"
            )
        ch = await self._resolve_channel(args.channel_id)
        # ... (rest unchanged)
```

The single-line code change inside `_send` is: replace `if args.text is None and not args.embeds:` with `if args.text is None and not args.embeds and not args.files:` and update the error message to `"send: one of 'text', 'embeds', or 'files' is required"`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -v
```

Expected: new test PASSES; no existing test regresses (the existing `_send` callers all pass `text` or `embeds`).

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-114-discord-send
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "feat(#114): _send empty-send guard names 'files'"
```

---

### Task 8: Integrate validator into `deliver()` and route unrecognized to failed-delivery Ack

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Test: `packages/agent-core-discord/tests/test_endpoint_outbound.py`

- [ ] **Step 1: Write the failing test (load-bearing regression test from spec §Testing)**

Append to `test_endpoint_outbound.py`:

```python
@pytest.mark.asyncio
async def test_unrecognized_field_produces_failed_delivery_ack(make_endpoint):
    """LOAD-BEARING regression test (spec Testing §). The single test
    that proves the silent-drop class is closed for this surface.

    Construct a TextMessage envelope with metadata.discord.mystery_field,
    hand it to DiscordEndpoint.deliver(), assert:
      (a) no Discord API call was made;
      (b) a yellow Acknowledgment was published with the right
          in_reply_to and a note naming the unrecognized field;
      (c) the inbound was acked (so it does not redeliver);
      (d) _send was never reached.
    """
    endpoint, fake_client = make_endpoint()
    bus = _FakeBus()
    await endpoint.start(bus)

    env = _build_text_message_envelope(
        text="hi",
        metadata={"discord": {
            "channel_id": "123",
            "mystery_field": "X",
        }},
    )
    original_id = env.id

    await endpoint.deliver(env)

    # (a) No Discord API call.
    assert fake_client.last_send_call is None, (
        "validator failed to gate: dispatch reached the Discord client"
    )

    # (b) Yellow Ack with right in_reply_to and the unrecognized field
    # named in the note.
    ack = bus.last_published_envelope
    assert ack is not None, "no failed-delivery Ack was published"
    assert ack.kind == "Acknowledgment"
    assert ack.urgency == "yellow"
    assert ack.in_reply_to == original_id
    assert "metadata.discord.mystery_field" in ack.payload.note
    assert "discord_send" in ack.payload.note

    # (c) The inbound was acked.
    assert original_id in bus.acked_envelope_ids

    # (d) _send was never reached — fake_client.send_call_count is 0.
    assert fake_client.send_call_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_unrecognized_field_produces_failed_delivery_ack -v
```

Expected: FAIL — `deliver()` today does not call the validator; the TextMessage routes through `_deliver_text_message`, which does NOT see `mystery_field`, silently ignores it, and proceeds to deliver via `_send`.

- [ ] **Step 3: Wire the validator into `deliver()`**

Add the import near the top of `endpoint.py` (alongside existing `from agent_core_discord.*` imports):

```python
from agent_core_discord.shape_validator import (
    Recognized,
    Unrecognized,
    validate as validate_shape,
)
```

In `deliver()` (currently around line 655), prepend the validator step. The modified body:

```python
    async def deliver(self, envelope: Envelope) -> None:
        if self._handle is None:
            raise EndpointUnavailable(f"discord '{self.name}' not started")

        # #114: strict-mode validator. Only consulted for kinds the
        # adapter dispatches (TextMessage / ToolInvocation); other kinds
        # fall through to the existing else-branch unchanged.
        if envelope.kind in ("TextMessage", "ToolInvocation"):
            validation = validate_shape(envelope)
            if isinstance(validation, Unrecognized):
                log.warning(
                    "discord(%s): unrecognized_shape event",
                    self.name,
                    extra={
                        "event": "unrecognized_shape",
                        "envelope_kind": envelope.kind,
                        "unrecognized_fields": validation.fields,
                        "sender": envelope.from_,
                        "envelope_id": envelope.id,
                        "canonical_equivalent": validation.canonical_equivalent,
                    },
                )
                field_list = validation.fields
                if len(field_list) == 1:
                    note = (
                        f"Unrecognized field {field_list[0]!r} on "
                        f"{envelope.kind}. Canonical: "
                        f"{validation.canonical_equivalent}"
                    )
                else:
                    note = (
                        f"Unrecognized fields {field_list} on "
                        f"{envelope.kind}. Canonical: "
                        f"{validation.canonical_equivalent}"
                    )
                await self._reply(envelope, note, urgency="yellow")
                await self._handle.ack(envelope.id)
                return
            if validation.deprecation_log_line:
                log.warning(
                    "discord(%s): deprecated_shape event",
                    self.name,
                    extra={
                        "event": "deprecated_shape",
                        "shape_name": validation.shape_name,
                        "sender": envelope.from_,
                        "envelope_id": envelope.id,
                        "canonical_equivalent": "tool=discord_send",
                    },
                )

        # ... existing kind-branch unchanged below ...
        if envelope.kind == "TextMessage":
            # (existing body)
            ...
```

(Preserve the existing body of the kind-branches verbatim. The new code lands strictly ABOVE them; nothing inside the existing branches changes.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_unrecognized_field_produces_failed_delivery_ack -v
```

Expected: PASS.

```bash
uv run pytest packages/agent-core-discord/tests/ -v
```

Expected: all tests PASS (including the existing back-compat tests, which carry recognized shapes and so flow through the validator's `Recognized` path).

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-114-discord-send
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "feat(#114): deliver() routes unrecognized envelopes to failed-delivery Ack"
```

---

## Phase 4 — Back-compat regression coverage + remaining integration tests

### Task 9: Back-compat regression tests with deprecation-log assertions

**Files:**
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py`

- [ ] **Step 1: Write the failing tests (all four documented legacy shapes + log capture)**

Append to `test_endpoint_outbound.py`:

```python
@pytest.mark.asyncio
async def test_legacy_textmessage_plain_still_delivers_and_logs_deprecation(
    make_endpoint, caplog
):
    """Back-compat shape #1: TextMessage + metadata.discord.channel_id
    plain text. Must still deliver. Must emit a structured
    deprecation_shape log line keyed by shape_name."""
    endpoint, fake_client = make_endpoint()
    await endpoint.start(_FakeBus())

    env = _build_text_message_envelope(
        text="hi",
        metadata={"discord": {"channel_id": "123"}},
    )
    with caplog.at_level("WARNING"):
        await endpoint.deliver(env)

    assert fake_client.last_send_call is not None
    assert fake_client.last_send_call["content"] == "hi"
    deprecated_logs = [
        r for r in caplog.records
        if getattr(r, "event", None) == "deprecated_shape"
    ]
    assert len(deprecated_logs) == 1
    assert deprecated_logs[0].shape_name == "legacy_textmessage_plain"
    assert deprecated_logs[0].sender == env.from_
    assert deprecated_logs[0].envelope_id == env.id
    assert deprecated_logs[0].canonical_equivalent == "tool=discord_send"


@pytest.mark.asyncio
async def test_legacy_send_discord_message_with_embeds_still_delivers_and_logs(
    make_endpoint, caplog
):
    """Back-compat shape #2: ToolInvocation + tool=send_discord_message
    + args.{channel_id, text, embeds}."""
    endpoint, fake_client = make_endpoint()
    await endpoint.start(_FakeBus())

    env = _build_tool_invocation_envelope(
        tool="send_discord_message",
        args={"channel_id": "123", "text": "hi", "embeds": [{"title": "x"}]},
    )
    with caplog.at_level("WARNING"):
        await endpoint.deliver(env)

    assert fake_client.last_send_call is not None
    deprecated_logs = [
        r for r in caplog.records
        if getattr(r, "event", None) == "deprecated_shape"
    ]
    assert len(deprecated_logs) == 1
    assert deprecated_logs[0].shape_name == "legacy_tool_send_discord_message"


@pytest.mark.asyncio
async def test_legacy_send_discord_message_with_files_still_delivers_and_logs(
    make_endpoint, caplog, tmp_path
):
    """Back-compat shape #3: ToolInvocation + tool=send_discord_message
    + args.{channel_id, text, files} (verified 2026-05-11)."""
    endpoint, fake_client = make_endpoint()
    await endpoint.start(_FakeBus())

    fake_file = tmp_path / "qa.zip"
    fake_file.write_bytes(b"\x00")

    env = _build_tool_invocation_envelope(
        tool="send_discord_message",
        args={"channel_id": "123", "text": "ship it", "files": [str(fake_file)]},
    )
    with caplog.at_level("WARNING"):
        await endpoint.deliver(env)

    assert fake_client.last_send_call is not None
    deprecated_logs = [
        r for r in caplog.records
        if getattr(r, "event", None) == "deprecated_shape"
    ]
    assert len(deprecated_logs) == 1
    assert deprecated_logs[0].shape_name == "legacy_tool_send_discord_message"


@pytest.mark.asyncio
async def test_legacy_textmessage_with_embeds_still_delivers_and_logs(
    make_endpoint, caplog
):
    """Back-compat shape #4: the poster-child. TextMessage + metadata.
    discord.embeds was routed by commit a278c68; after #114 it still
    routes but the deprecation log fires."""
    endpoint, fake_client = make_endpoint()
    await endpoint.start(_FakeBus())

    env = _build_text_message_envelope(
        text="",
        metadata={"discord": {
            "channel_id": "123",
            "embeds": [{"title": "x"}],
        }},
    )
    with caplog.at_level("WARNING"):
        await endpoint.deliver(env)

    assert fake_client.last_send_call is not None
    deprecated_logs = [
        r for r in caplog.records
        if getattr(r, "event", None) == "deprecated_shape"
    ]
    assert len(deprecated_logs) == 1
    assert deprecated_logs[0].shape_name == "legacy_textmessage_embeds"
```

- [ ] **Step 2: Run tests to verify they fail OR pass**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -v -k legacy
```

Expected: 4 new tests should PASS if Task 8 wired the deprecation log correctly. If any fail because the log line is missing or wrong-shape, fix the structured-log emission in Task 8's `deliver()` body to match the assertions.

- [ ] **Step 3: (If any test failed) Fix the log emission**

The test contract: each `deprecated_shape` log record carries `event`, `shape_name`, `sender`, `envelope_id`, and `canonical_equivalent` as `extra=` keys. Cross-reference the Task 8 implementation; ensure the `extra=` dict matches the test assertions exactly.

- [ ] **Step 4: Re-run tests to verify all pass**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -v
```

Expected: all tests PASS, including the new 4 back-compat regressions.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-114-discord-send
git add packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "test(#114): back-compat regression coverage for the 4 documented legacy shapes"
```

---

### Task 10: Multi-field unrecognized + canonical-path + validator-internal-exception integration tests

**Files:**
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_endpoint_outbound.py`:

```python
@pytest.mark.asyncio
async def test_multi_field_unrecognized_produces_one_ack_listing_all_fields(
    make_endpoint,
):
    """When two or more unrecognized fields land on the same envelope,
    ONE failed-delivery Ack lists all of them. Senders see the full
    failure surface in one delivery, not in N redeliveries."""
    endpoint, fake_client = make_endpoint()
    bus = _FakeBus()
    await endpoint.start(bus)

    env = _build_text_message_envelope(
        text="hi",
        metadata={"discord": {
            "channel_id": "123",
            "mystery_one": "X",
            "mystery_two": "Y",
        }},
    )
    await endpoint.deliver(env)

    assert fake_client.last_send_call is None
    ack = bus.last_published_envelope
    assert ack.kind == "Acknowledgment"
    assert ack.urgency == "yellow"
    # Both fields named in the note.
    assert "metadata.discord.mystery_one" in ack.payload.note
    assert "metadata.discord.mystery_two" in ack.payload.note
    # Only ONE Ack published.
    assert bus.publish_count == 1


@pytest.mark.asyncio
async def test_canonical_discord_send_delivers_silently_no_deprecation_log(
    make_endpoint, caplog
):
    """The canonical path: tool=discord_send + canonical args delivers
    and emits NO deprecation log. The deprecation-readiness telemetry
    must see clean canonical sends as 'no event recorded'."""
    endpoint, fake_client = make_endpoint()
    await endpoint.start(_FakeBus())

    env = _build_tool_invocation_envelope(
        tool="discord_send",
        args={"channel_id": "123", "text": "hi"},
    )
    with caplog.at_level("WARNING"):
        await endpoint.deliver(env)

    assert fake_client.last_send_call is not None
    deprecated_logs = [
        r for r in caplog.records
        if getattr(r, "event", None) == "deprecated_shape"
    ]
    assert deprecated_logs == []


@pytest.mark.asyncio
async def test_validator_internal_exception_produces_yellow_ack(
    make_endpoint, monkeypatch
):
    """If the validator itself raises (catalog bug, malformed envelope
    from a test fixture), deliver()'s existing exception handler must
    catch it and produce a yellow Ack with the error in the note. The
    inbound must still be acked so no redelivery storm happens."""
    endpoint, fake_client = make_endpoint()
    bus = _FakeBus()
    await endpoint.start(bus)

    # Force validate() to raise.
    def _boom(_env):
        raise RuntimeError("catalog bug for test")

    monkeypatch.setattr(
        "agent_core_discord.endpoint.validate_shape", _boom
    )

    env = _build_tool_invocation_envelope(
        tool="discord_send",
        args={"channel_id": "123", "text": "hi"},
    )
    await endpoint.deliver(env)

    # _send was NOT reached.
    assert fake_client.send_call_count == 0
    # Yellow Ack with "validator failed" in the note.
    ack = bus.last_published_envelope
    assert ack.urgency == "yellow"
    assert "validator failed" in ack.payload.note.lower() or "error" in ack.payload.note.lower()
    # Inbound was acked.
    assert env.id in bus.acked_envelope_ids
```

- [ ] **Step 2: Run tests to verify they fail OR pass**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -v -k "multi_field or canonical_discord_send_delivers or validator_internal_exception"
```

Expected: multi_field and canonical tests should PASS off the back of Task 8. validator_internal_exception test may need a small adjustment in `deliver()`'s exception handling — the existing handler wraps `_dispatch` but the new validator call sits ABOVE the dispatch try/except. Wrap the validator call in its own try/except inside `deliver()`:

```python
        if envelope.kind in ("TextMessage", "ToolInvocation"):
            try:
                validation = validate_shape(envelope)
            except Exception as exc:
                log.exception("discord(%s): validator raised", self.name)
                await self._reply(
                    envelope,
                    f"validator failed: {exc!r}",
                    urgency="yellow",
                )
                await self._handle.ack(envelope.id)
                return
            # ... rest of the validator branch unchanged ...
```

- [ ] **Step 3: Apply the validator-exception wrap and re-run**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -v
```

Expected: all tests in the file PASS.

- [ ] **Step 4: Run the full discord adapter test suite to confirm no regressions**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/ -v
```

Expected: all discord adapter tests PASS (back-compat suite + new tests + existing inbound/resolver/recent-inbounds suites).

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-114-discord-send
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "feat(#114): validator exception wrap + multi-field / canonical / exception integration tests"
```

---

### Task 11: Final ruff + mypy + full package test sweep, then open PR

**Files:** No code changes — verification only.

- [ ] **Step 1: Run the full discord adapter test suite**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest packages/agent-core-discord/tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run ruff over the package**

```bash
cd .worktrees/issue-114-discord-send
uv run ruff check packages/agent-core-discord/
```

Expected: no errors. If errors, fix inline (do not commit ruff fixes mixed with feature commits — fix in a separate `style(#114): ruff`).

- [ ] **Step 3: Run mypy over the package**

```bash
cd .worktrees/issue-114-discord-send
uv run mypy packages/agent-core-discord/
```

Expected: no errors. If type errors, fix in a separate `chore(#114): mypy` commit.

- [ ] **Step 4: Run the full repo test suite for one cross-cutting safety check**

```bash
cd .worktrees/issue-114-discord-send
uv run pytest -q
```

Expected: all tests PASS. The bus envelope schema is unchanged, so cross-cutting damage is structurally unlikely; this is the belt-and-suspenders pass before opening the PR.

- [ ] **Step 5: Push the branch and open the PR**

```bash
cd .worktrees/issue-114-discord-send
git push -u origin feat/issue-114-discord-send-unified-envelope
gh pr create \
  --base main \
  --head feat/issue-114-discord-send-unified-envelope \
  --title "feat(#114): unified discord_send envelope shape + strict-mode validator" \
  --body "$(cat <<'EOF'
## Summary

Closes #114. Adds one canonical `tool=discord_send` to the Discord adapter and a strict-mode shape validator that publishes failed-delivery `Acknowledgment` envelopes (urgency=yellow) on unrecognized fields. Closes the silent-drop class on the discord-send surface without touching the bus envelope schema.

## Design

See `docs/superpowers/specs/2026-05-23-issue-114-discord-send-unified-envelope-design.md`.

## What ships

- New `tool=discord_send` with `_DiscordSendArgs(extra="forbid")`.
- New `shape_validator` pure-function module with recognized-shape catalog.
- `DiscordEndpoint.deliver()` calls the validator at the top; unrecognized envelopes get a yellow failed-delivery Ack with the canonical equivalent named in the note; recognized-but-legacy envelopes get a structured `deprecated_shape` log line and proceed to deliver.
- Old shapes (`tool=send`, `tool=send_discord_message`, `TextMessage + metadata.discord.{channel_id, embeds, reply_to}`) continue to deliver. Senders are NOT broken.

## Test plan

- [ ] `uv run pytest packages/agent-core-discord/tests/ -v` passes locally.
- [ ] Load-bearing test `test_unrecognized_field_produces_failed_delivery_ack` passes — proves the silent-drop class is closed.
- [ ] Back-compat regression suite passes for all four documented legacy shapes, with deprecation-log assertions.
- [ ] `uv run pytest -q` (full repo) passes.
- [ ] `uv run ruff check packages/agent-core-discord/` is clean.
- [ ] `uv run mypy packages/agent-core-discord/` is clean.

## Migration

Pepper will move her own send-paths plus the relevant memory entries to `tool=discord_send` after this lands, so the deprecation-log lines start dropping and the deprecation-readiness telemetry has data for the eventual removal ticket.

## Next-ticket triggers (deferred)

- Removal of deprecated shapes (gated on deprecation-readiness telemetry).
- Generalization to other surfaces (Slack, email) — rule-of-three.
- `allowed_mentions` / `components` wiring through `_send` — earned by named symptom.
EOF
)"
```

Expected: PR URL printed. Report the URL back to the controller.

- [ ] **Step 6: Final commit (only if any post-test fixups landed)**

If Steps 2 or 3 required a separate `style(#114): ruff` or `chore(#114): mypy` commit, push the updated branch:

```bash
cd .worktrees/issue-114-discord-send
git push origin feat/issue-114-discord-send-unified-envelope
```

Otherwise no commit needed — the PR has the full feature.
