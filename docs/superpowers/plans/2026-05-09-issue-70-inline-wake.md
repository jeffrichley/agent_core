# Issue #70 — Inline-content wake via relay-side prefetch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drop per-Discord-round-trip floor from 2 tool calls to 1 by having the `agent-core-channel` relay call `consume()` on the agent's behalf at wake time, render envelope content with safe encoding, and emit a richer `notifications/claude/channel` notification — leaving the bus protocol unchanged (Alt B / harness-side prefetch).

**Architecture:** All new behavior lives in the relay (`packages/agent-core-channel`). The relay grows MCP-client capability (`BusClient`), a pure-functional rendering pipeline (per-kind renderers + HTML-escape encoder + circuit breaker + redelivery tracker), a wake-audit JSONL writer for instrumentation, and layered config (CLI > env > YAML > defaults). The bus daemon's only change is a new `peek(envelope_id)` MCP tool to support truncation-marker hydration.

**Tech Stack:** Python 3.12, `fastmcp` (MCP server + client), `anyio` (async runtime), `httpx` (HTTP), `pytest` + `pytest-asyncio` + `looptime`, `mypy`, `ruff`, `uv` for workspace management. Source spec at `docs/superpowers/specs/2026-05-09-issue-70-inline-wake-design.md`.

---

## File map

**Created:**

- `packages/agent-core-channel/src/agent_core_channel/rendering.py` — per-kind renderers, body encoder, circuit breaker, truncation marker, redelivery tracker
- `packages/agent-core-channel/src/agent_core_channel/bus_client.py` — persistent MCP client to the bus
- `packages/agent-core-channel/src/agent_core_channel/wake_audit.py` — JSONL writer for the per-wake audit log
- `packages/agent-core-channel/src/agent_core_channel/config.py` — `RelayConfig` dataclass + `load_config` resolver
- `packages/agent-core-channel/tests/test_rendering.py`
- `packages/agent-core-channel/tests/test_bus_client.py`
- `packages/agent-core-channel/tests/test_wake_audit.py`
- `packages/agent-core-channel/tests/test_stdio_server_inline.py` — integration tests for the wire-up
- `packages/agent-core-channel/tests/test_config.py`
- `packages/core/src/agent_core/wake_stats.py` — analyzer logic for the `wake-stats` CLI
- `packages/core/tests/test_wake_stats.py`
- `packages/core/tests/test_claude_code_mcp_peek.py`
- `packages/core/changelog.d/70.added.md`

**Modified:**

- `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — register new `peek` MCP tool next to `consume`/`reply`/`handle`
- `packages/agent-core-channel/src/agent_core_channel/__main__.py` — add CLI flags (`--config-path`, `--inline-mode`, etc.) and env-var fallback
- `packages/agent-core-channel/src/agent_core_channel/stdio_server.py` — replace `_sse_pump` with the render pipeline; thread `BusClient`, `WakeAuditWriter`, and `RelayConfig` through `run_relay`; rewrite `_RELAY_INSTRUCTIONS`
- `packages/core/src/agent_core/cli.py` — add `wake-stats` subcommand

---

## Task 1: Add `peek(envelope_id)` MCP tool

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — register `peek` inside `_register_tools` near `consume`/`reply`/`handle` (around line 985, just after `reply`)
- Test: `packages/core/tests/test_claude_code_mcp_peek.py`

- [ ] **Step 1: Write the failing tests**

```python
# packages/core/tests/test_claude_code_mcp_peek.py
"""Issue #70: peek(envelope_id) returns one envelope without acking."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _RecordingHandle:
    def __init__(self) -> None:
        self.published: list[Envelope] = []
        self.acked: list[str] = []

    async def publish(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None:
        self.acked.append(envelope_id)

    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...

    def endpoints(self) -> list:
        return []


def _inbound_text(env_id: str, *, text: str = "hi", from_: str = "discord") -> Envelope:
    return Envelope(
        id=env_id,
        correlation_id=f"corr-{env_id}",
        in_reply_to=None,
        from_=from_,
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        urgency="green",
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_peek_returns_envelope_when_pending() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("e-1", text="hello"))

        async with Client(ep._mcp) as client:
            res = await client.call_tool("peek", {"envelope_id": "e-1"})

        envelope = res.data["envelope"]  # type: ignore[index]
        assert envelope["id"] == "e-1"
        assert envelope["payload"]["text"] == "hello"
        assert envelope["from"] == "discord"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_peek_does_not_ack_or_remove_from_pending() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("e-2"))

        async with Client(ep._mcp) as client:
            await client.call_tool("peek", {"envelope_id": "e-2"})

        assert handle.acked == []
        assert any(e.id == "e-2" for e in ep._pending)
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_peek_is_idempotent() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound_text("e-3", text="same"))

        async with Client(ep._mcp) as client:
            r1 = await client.call_tool("peek", {"envelope_id": "e-3"})
            r2 = await client.call_tool("peek", {"envelope_id": "e-3"})

        assert r1.data == r2.data  # type: ignore[index]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_peek_raises_when_envelope_id_missing() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        async with Client(ep._mcp) as client:
            with pytest.raises(ToolError, match="not in queue"):
                await client.call_tool("peek", {"envelope_id": "ghost"})
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_peek_does_not_consult_recent_inbounds_cache() -> None:
    """The _recent_inbounds cache holds routing only, not full payload.
    peek() is a payload-fetch tool, so it must look only at _pending."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _RecordingHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        # Manually populate _recent_inbounds without queueing — simulates an
        # envelope that was acked but routing still cached.
        ep._recent_inbounds["cached-only"] = {
            "from": "discord",
            "to": "agent",
            "kind": "TextMessage",
            "metadata": {},
            "urgency": "green",
            "correlation_id": "corr-cached-only",
            "registered_at": 0.0,
        }
        async with Client(ep._mcp) as client:
            with pytest.raises(ToolError, match="not in queue"):
                await client.call_tool("peek", {"envelope_id": "cached-only"})
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest packages/core/tests/test_claude_code_mcp_peek.py -v`
Expected: 5 FAILs with "Unknown tool: 'peek'"

- [ ] **Step 3: Register the `peek` tool**

Insert this tool registration in `packages/core/src/agent_core/endpoints/claude_code_mcp.py` inside `_register_tools`, immediately after the `reply` tool registration (around line ~1020, before `show_my_day`):

```python
        @self._mcp.tool()
        async def peek(envelope_id: str) -> dict:
            """Return one specific envelope from the pickup queue without acking.

            Used to hydrate a truncated inline preview into the full payload
            (issue #70). Also useful for power-use cases (manual triage of a
            specific envelope without disturbing other queue state).

            Pure read: does NOT ack, does NOT remove from the pickup queue.
            Idempotent — multiple calls return identical data.

            Looks only at the live pickup queue. Does NOT consult the
            recent-inbounds routing cache (which holds metadata only, not
            full payload — the cache cannot satisfy peek's contract).

            Raises if ``envelope_id`` is not in the queue.
            """
            env = next((e for e in self._pending if e.id == envelope_id), None)
            if env is None:
                raise ValueError(
                    f"peek: envelope_id={envelope_id!r} not in queue"
                )
            return {"envelope": self._envelope_to_dict(env)}
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run pytest packages/core/tests/test_claude_code_mcp_peek.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Run lint + types**

Run: `uv run ruff check packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/tests/test_claude_code_mcp_peek.py`
Run: `uv run mypy packages/core/src/agent_core/endpoints/claude_code_mcp.py`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/tests/test_claude_code_mcp_peek.py
git commit -m "feat(bus): add peek(envelope_id) MCP tool (#70)

Returns one specific envelope from the pickup queue without acking.
Used to hydrate a truncated inline preview into the full payload
(issue #70's truncation marker). Also useful for power-use cases
(manual triage without disturbing other queue state).

Pure read: does not ack, does not remove from the pickup queue,
idempotent, looks only at _pending (the recent-inbounds routing
cache holds metadata only, not full payload). Raises ValueError
if the envelope_id is not in the queue."
```

---

## Task 2: Body encoding helper (HTML-escape)

**Files:**
- Create: `packages/agent-core-channel/src/agent_core_channel/rendering.py` (new file — first contribution)
- Test: `packages/agent-core-channel/tests/test_rendering.py` (new file — first contribution)

- [ ] **Step 1: Write the failing tests**

```python
# packages/agent-core-channel/tests/test_rendering.py
"""Issue #70: rendering pipeline — body encoder, per-kind renderers,
circuit breaker, truncation marker, redelivery tracker."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from agent_core_channel.rendering import encode_body


class TestEncodeBody:
    def test_passes_through_safe_text(self) -> None:
        assert encode_body("hello world") == "hello world"

    def test_escapes_lt_gt(self) -> None:
        assert encode_body("2 < 3 > 1") == "2 &lt; 3 &gt; 1"

    def test_escapes_ampersand(self) -> None:
        assert encode_body("a & b") == "a &amp; b"

    def test_escapes_quotes(self) -> None:
        assert encode_body("she said \"hi\" and 'bye'") == (
            "she said &quot;hi&quot; and &apos;bye&apos;"
        )

    def test_escapes_inbox_close_tag_literal(self) -> None:
        assert "</inbox>" not in encode_body("ends with </inbox> in body")

    def test_idempotent_after_escape(self) -> None:
        # Note: HTML escape is NOT strictly idempotent (escaping &amp; → &amp;amp;)
        # but is well-defined: each application escapes the literals seen.
        # The contract is "the output never contains unescaped &<>'\" ".
        once = encode_body("a & b < c")
        for ch in ("&amp;", "&lt;"):
            assert ch in once
        twice = encode_body(once)
        # No bare special chars after a single escape (all are now &-prefixed).
        assert "<" not in once
        assert "<" not in twice

    def test_xml_roundtrip_safety_with_user_payload(self) -> None:
        """Pepper's verification target: malicious user content does not
        break a downstream XML parse of the surrounding <inbox> wrapper."""
        nasty = "<script>alert(1)</script> & < > ' \" </inbox>"
        encoded = encode_body(nasty)
        wrapped = f"<inbox>{encoded}</inbox>"
        # Plain XML parser succeeds — that's the contract.
        root = ET.fromstring(wrapped)
        assert root.tag == "inbox"
        # Body content round-trips back to the original after XML decoding.
        assert root.text == nasty
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest packages/agent-core-channel/tests/test_rendering.py -v`
Expected: ImportError / ModuleNotFoundError on `agent_core_channel.rendering`.

- [ ] **Step 3: Implement `encode_body`**

Create `packages/agent-core-channel/src/agent_core_channel/rendering.py`:

```python
"""Issue #70: rendering pipeline for inline-content wake notifications.

Produces a string suitable for the `params.content` field of an
`notifications/claude/channel` notification. The relay's stdio server
then forwards that string to Claude Code, which renders it in the
agent's working context.

Encoding contract: arbitrary user-provided text (Discord messages, code,
unbalanced characters) cannot break the agent's parse. We use HTML escape
for body content; attribute values (kind, urgency, envelope_id, from) are
bounded enums or hex IDs and don't need escaping.
"""

from __future__ import annotations

import html


def encode_body(text: str) -> str:
    """HTML-escape body content for safe inclusion in an <inbox> tag.

    Applies escaping for ``&``, ``<``, ``>``, ``'``, ``"``. The result
    survives a downstream XML parse of the surrounding wrapper and round-
    trips back to the original after XML decoding.

    Not strictly idempotent (escaping ``&amp;`` produces ``&amp;amp;``);
    callers should escape exactly once per pass through the pipeline.
    """
    return html.escape(text, quote=True)
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run pytest packages/agent-core-channel/tests/test_rendering.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Run lint + types**

Run: `uv run ruff check packages/agent-core-channel/src/agent_core_channel/rendering.py packages/agent-core-channel/tests/test_rendering.py`
Run: `uv run mypy packages/agent-core-channel/src/agent_core_channel/rendering.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/rendering.py packages/agent-core-channel/tests/test_rendering.py
git commit -m "feat(channel): body encoding helper for inline wakes (#70)

HTML-escape pass for body content in <inbox> renderings. Survives a
downstream XML parse of the surrounding wrapper and round-trips back
to the original after XML decoding. Verified against Pepper's
encoding-safety contract (issue #70): malicious user content
including <script>, &, <, >, '\", and literal </inbox> in the body
does not break the agent's parse."
```

---

## Task 3: Per-kind envelope renderers

**Files:**
- Modify: `packages/agent-core-channel/src/agent_core_channel/rendering.py`
- Modify: `packages/agent-core-channel/tests/test_rendering.py`

- [ ] **Step 1: Add failing tests for the renderer dispatch**

Append to `packages/agent-core-channel/tests/test_rendering.py`:

```python
from agent_core_channel.rendering import render_envelope


def _env(env_id: str, *, kind: str, payload: dict, **kwargs) -> dict:
    """Build an envelope dict in the shape returned by consume()."""
    base = {
        "id": env_id,
        "from": "discord-pepper",
        "to": "pepper",
        "kind": kind,
        "correlation_id": f"corr-{env_id}",
        "in_reply_to": None,
        "payload": payload,
        "metadata": {},
        "urgency": "green",
        "created_at": "2026-05-09T03:29:22+00:00",
    }
    base.update(kwargs)
    return base


class TestRenderEnvelope:
    def test_text_message(self) -> None:
        env = _env(
            "e-1",
            kind="TextMessage",
            payload={"kind": "TextMessage", "text": "hello there"},
        )
        out = render_envelope(env)
        assert "<inbox" in out
        assert "from='discord-pepper'" in out
        assert "urgency='green'" in out
        assert "envelope_id='e-1'" in out
        assert "kind='TextMessage'" in out
        assert "hello there" in out
        assert out.endswith("</inbox>")

    def test_text_message_escapes_body(self) -> None:
        env = _env(
            "e-2",
            kind="TextMessage",
            payload={"kind": "TextMessage", "text": "<script>alert(1)</script>"},
        )
        out = render_envelope(env)
        assert "&lt;script&gt;" in out
        assert "<script>" not in out

    def test_acknowledgment_uses_note(self) -> None:
        env = _env(
            "e-3",
            kind="Acknowledgment",
            urgency="yellow",
            in_reply_to="out-1",
            payload={"kind": "Acknowledgment", "of": "out-1", "note": "error: timeout"},
        )
        out = render_envelope(env)
        assert "kind='Acknowledgment'" in out
        assert "urgency='yellow'" in out
        assert "in_reply_to='out-1'" in out
        assert "error: timeout" in out

    def test_acknowledgment_falls_back_to_payload_when_no_note(self) -> None:
        env = _env(
            "e-3b",
            kind="Acknowledgment",
            urgency="red",
            in_reply_to="out-2",
            payload={"kind": "Acknowledgment", "of": "out-2", "note": None},
        )
        out = render_envelope(env)
        # No bare 'None'; some non-empty body.
        assert "</inbox>" in out
        assert "None" not in out or "of='out-2'" in out  # any sane representation

    def test_event(self) -> None:
        env = _env(
            "e-4",
            kind="Event",
            payload={"kind": "Event", "type": "deploy.started", "data": {"sha": "abc"}},
        )
        out = render_envelope(env)
        assert "kind='Event'" in out
        assert "deploy.started" in out
        assert "abc" in out

    def test_generic_kind_uses_json_payload(self) -> None:
        env = _env(
            "e-5",
            kind="BriefRequest",
            payload={"kind": "BriefRequest", "playbook": "morning_brief"},
        )
        out = render_envelope(env)
        assert "kind='BriefRequest'" in out
        assert "morning_brief" in out

    def test_unknown_kind_falls_back_to_repr(self) -> None:
        env = _env(
            "e-6",
            kind="ExoticPluginKind",
            payload={"kind": "ExoticPluginKind", "blob": "data"},
        )
        out = render_envelope(env)
        assert "kind='ExoticPluginKind'" in out
        assert "render='fallback'" in out

    def test_attribute_values_are_not_escaped(self) -> None:
        # envelope_id is a hex UUID, kind is bounded enum — no escaping needed.
        env = _env(
            "abc123",
            kind="TextMessage",
            payload={"kind": "TextMessage", "text": "ok"},
        )
        out = render_envelope(env)
        assert "envelope_id='abc123'" in out
        assert "&apos;" not in out.split(">")[0]  # no escaped quotes in opening tag

    def test_xml_parseable_with_nasty_payload(self) -> None:
        env = _env(
            "e-nasty",
            kind="TextMessage",
            payload={"kind": "TextMessage", "text": "</inbox> & <script>"},
        )
        out = render_envelope(env)
        # Wrap and parse — must succeed.
        ET.fromstring(out)


class TestRenderBatchEntry:
    def test_single_wrapped_entry(self) -> None:
        from agent_core_channel.rendering import render_item

        item = {
            "type": "single",
            "envelope": _env("e-s", kind="TextMessage", payload={"kind": "TextMessage", "text": "x"}),
        }
        outs = render_item(item)
        assert len(outs) == 1
        assert "envelope_id='e-s'" in outs[0]

    def test_batch_entry_with_prefix(self) -> None:
        from agent_core_channel.rendering import render_item

        item = {
            "type": "batch",
            "from": "discord-pepper",
            "kind": "TextMessage",
            "urgency": "green",
            "envelopes": [
                _env("b-1", kind="TextMessage", payload={"kind": "TextMessage", "text": "first"}),
                _env("b-2", kind="TextMessage", payload={"kind": "TextMessage", "text": "second"}),
            ],
            "first_arrival": "2026-05-09T03:29:22+00:00",
            "total_age_seconds": 5,
        }
        outs = render_item(item)
        assert len(outs) == 2
        assert "[BATCH 1/2]" in outs[0]
        assert "[BATCH 2/2]" in outs[1]
        assert "envelope_id='b-1'" in outs[0]
        assert "envelope_id='b-2'" in outs[1]

    def test_flat_envelope_dict(self) -> None:
        from agent_core_channel.rendering import render_item

        env = _env("flat-1", kind="TextMessage", payload={"kind": "TextMessage", "text": "y"})
        outs = render_item(env)  # flat envelope (no "type" key)
        assert len(outs) == 1
        assert "envelope_id='flat-1'" in outs[0]
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest packages/agent-core-channel/tests/test_rendering.py -v`
Expected: ImportError on `render_envelope` and `render_item`.

- [ ] **Step 3: Implement `render_envelope` + dispatch dict + `render_item`**

Append to `packages/agent-core-channel/src/agent_core_channel/rendering.py`:

```python
import json
from collections.abc import Callable

# ---------------------------------------------------------------------
# Per-kind renderers — each takes an envelope dict, returns body text.
# Body text is HTML-escaped before being placed inside the <inbox> tag.
# ---------------------------------------------------------------------


def _render_text_message_body(env: dict) -> str:
    text = env.get("payload", {}).get("text", "")
    return encode_body(str(text))


def _render_acknowledgment_body(env: dict) -> str:
    payload = env.get("payload", {}) or {}
    note = payload.get("note")
    if note is not None:
        return encode_body(str(note))
    return encode_body(json.dumps(payload, sort_keys=True, default=str))


def _render_event_body(env: dict) -> str:
    payload = env.get("payload", {}) or {}
    # Compact JSON of the event payload.
    return encode_body(json.dumps(payload, sort_keys=True, default=str))


def _render_generic_body(env: dict) -> str:
    payload = env.get("payload", {}) or {}
    return encode_body(json.dumps(payload, sort_keys=True, default=str))


def _render_fallback_body(env: dict) -> str:
    """Used for unknown kinds and when a renderer raises."""
    payload = env.get("payload", {}) or {}
    try:
        body = repr(payload)
    except Exception:
        body = f"<unrenderable payload for envelope {env.get('id', '?')}>"
    return encode_body(body)


_RENDERERS: dict[str, Callable[[dict], str]] = {
    "TextMessage": _render_text_message_body,
    "Acknowledgment": _render_acknowledgment_body,
    "Event": _render_event_body,
}

# Kinds that use the generic JSON payload renderer rather than the fallback marker.
_GENERIC_KINDS: frozenset[str] = frozenset(
    {"BriefRequest", "ToolInvocation", "Progress", "ComposeBrief"}
)


def render_envelope(env: dict) -> str:
    """Render one envelope as an <inbox>...</inbox> block with HTML-escaped body."""
    kind = env.get("kind", "Unknown")
    env_id = env.get("id", "")
    from_ = env.get("from", "")
    urgency = env.get("urgency", "green")
    in_reply_to = env.get("in_reply_to")

    renderer = _RENDERERS.get(kind)
    is_fallback = False
    if renderer is not None:
        try:
            body = renderer(env)
        except Exception:
            body = _render_fallback_body(env)
            is_fallback = True
    elif kind in _GENERIC_KINDS:
        try:
            body = _render_generic_body(env)
        except Exception:
            body = _render_fallback_body(env)
            is_fallback = True
    else:
        body = _render_fallback_body(env)
        is_fallback = True

    attrs = [
        f"kind='{kind}'",
        f"from='{from_}'",
        f"urgency='{urgency}'",
        f"envelope_id='{env_id}'",
    ]
    if in_reply_to:
        attrs.append(f"in_reply_to='{in_reply_to}'")
    if is_fallback:
        attrs.append("render='fallback'")

    return f"<inbox {' '.join(attrs)}>\n{body}\n</inbox>"


def render_item(item: dict) -> list[str]:
    """Render one item from consume()'s response — single, batch, or flat envelope.

    Returns a list of rendered <inbox> blocks (one per underlying envelope).
    Batch entries get a [BATCH N/M] prefix on each underlying envelope's tag.
    """
    if "type" not in item:
        # Flat envelope dict (consume(batch_window_seconds=0) shape).
        return [render_envelope(item)]
    if item["type"] == "single":
        return [render_envelope(item["envelope"])]
    if item["type"] == "batch":
        envelopes = item["envelopes"]
        total = len(envelopes)
        rendered: list[str] = []
        for i, env in enumerate(envelopes, start=1):
            block = render_envelope(env)
            # Inject batch prefix into the opening tag's whitespace area.
            prefixed = block.replace(
                "<inbox ", f"<inbox batch='{i}/{total}' ", 1
            )
            rendered.append(prefixed)
        return rendered
    # Unknown item shape — defensive fallback.
    return [_render_fallback_body(item)]
```

Note: the test expects `[BATCH N/M]` prefix; the implementation uses `batch='N/M'` as an attribute (cleaner XML). Update the test to match:

```python
    def test_batch_entry_with_prefix(self) -> None:
        from agent_core_channel.rendering import render_item

        item = {
            "type": "batch",
            "from": "discord-pepper",
            "kind": "TextMessage",
            "urgency": "green",
            "envelopes": [
                _env("b-1", kind="TextMessage", payload={"kind": "TextMessage", "text": "first"}),
                _env("b-2", kind="TextMessage", payload={"kind": "TextMessage", "text": "second"}),
            ],
            "first_arrival": "2026-05-09T03:29:22+00:00",
            "total_age_seconds": 5,
        }
        outs = render_item(item)
        assert len(outs) == 2
        assert "batch='1/2'" in outs[0]
        assert "batch='2/2'" in outs[1]
        assert "envelope_id='b-1'" in outs[0]
        assert "envelope_id='b-2'" in outs[1]
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run pytest packages/agent-core-channel/tests/test_rendering.py -v`
Expected: all PASS.

- [ ] **Step 5: Run lint + types**

Run: `uv run ruff check packages/agent-core-channel/src packages/agent-core-channel/tests`
Run: `uv run mypy packages/agent-core-channel/src/agent_core_channel/rendering.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/rendering.py packages/agent-core-channel/tests/test_rendering.py
git commit -m "feat(channel): per-kind envelope renderers (#70)

Dispatch-dict renderer with default-policy table covering all kinds
that exist today: TextMessage uses payload.text; Acknowledgment uses
payload.note (or stringified payload if note is None); Event and
generic kinds (BriefRequest, ToolInvocation, Progress, ComposeBrief)
use compact JSON; unknown kinds use repr() with a render='fallback'
marker. Per-kind plugin hooks remain a separable future ticket.

render_item handles both flat envelope dicts (consume(batch_window_seconds=0))
and batched responses (consume(batch_window_seconds=N) with batch
collapsing) — batch entries get a batch='N/M' attribute on each
underlying envelope's <inbox> tag so the agent can ack/reply each
individually using its own envelope_id.

All body content runs through the HTML-escape encoder; attribute
values (kind, urgency, envelope_id, from, in_reply_to) are bounded
enums or hex IDs and don't need escaping."
```

---

## Task 4: Circuit breaker + truncation marker + redelivery tracker

**Files:**
- Modify: `packages/agent-core-channel/src/agent_core_channel/rendering.py`
- Modify: `packages/agent-core-channel/tests/test_rendering.py`

- [ ] **Step 1: Add failing tests**

Append to `packages/agent-core-channel/tests/test_rendering.py`:

```python
from agent_core_channel.rendering import (
    InlineAll,
    FallbackToBare,
    apply_circuit_breaker,
    truncation_marker,
    RedeliveryTracker,
)


class TestTruncationMarker:
    def test_format(self) -> None:
        assert truncation_marker("abc") == (
            "[content elided; envelope_id='abc'; call peek('abc') for full payload]"
        )


class TestApplyCircuitBreaker:
    def _flat(self, env_id: str, kind: str = "TextMessage", urgency: str = "green",
              text: str = "hi") -> dict:
        return {
            "id": env_id,
            "from": "discord-pepper",
            "to": "pepper",
            "kind": kind,
            "correlation_id": f"c-{env_id}",
            "in_reply_to": None,
            "payload": {"kind": kind, "text": text},
            "metadata": {},
            "urgency": urgency,
            "created_at": "2026-05-09T03:29:22+00:00",
        }

    def test_inlines_below_caps(self) -> None:
        items = [self._flat("e-1", text="hi"), self._flat("e-2", text="there")]
        result = apply_circuit_breaker(
            items,
            max_envelopes=5, max_total_bytes=8192, max_per_envelope_bytes=4096,
            mode="full",
        )
        assert isinstance(result, InlineAll)
        assert len(result.rendered) == 2
        assert "e-1" in result.inlined_ids
        assert "e-2" in result.inlined_ids

    def test_falls_back_when_envelope_count_exceeds(self) -> None:
        items = [self._flat(f"e-{i}", text="x") for i in range(6)]
        result = apply_circuit_breaker(
            items,
            max_envelopes=5, max_total_bytes=8192, max_per_envelope_bytes=4096,
            mode="full",
        )
        assert isinstance(result, FallbackToBare)
        assert result.reason == "envelope_count"

    def test_per_envelope_cap_triggers_truncation_marker(self) -> None:
        big = self._flat("big-1", text="x" * 5000)  # exceeds per-envelope cap
        small = self._flat("small-1", text="hi")
        result = apply_circuit_breaker(
            [small, big],
            max_envelopes=5, max_total_bytes=20000, max_per_envelope_bytes=1024,
            mode="full",
        )
        assert isinstance(result, InlineAll)
        # The big one is replaced with the truncation marker.
        big_block = next(b for b in result.rendered if "envelope_id='big-1'" in b)
        assert "call peek('big-1')" in big_block
        # The small one renders normally.
        small_block = next(b for b in result.rendered if "envelope_id='small-1'" in b)
        assert "call peek(" not in small_block

    def test_falls_back_when_total_bytes_exceeds(self) -> None:
        items = [self._flat(f"e-{i}", text="x" * 1000) for i in range(5)]
        result = apply_circuit_breaker(
            items,
            max_envelopes=10, max_total_bytes=2000, max_per_envelope_bytes=2000,
            mode="full",
        )
        assert isinstance(result, FallbackToBare)
        assert result.reason == "total_bytes"

    def test_failure_envelopes_bypass_total_bytes_cap(self) -> None:
        # 5 yellow + red envelopes summing past total cap still inline.
        items = [self._flat(f"f-{i}", urgency="yellow", text="x" * 1000) for i in range(5)]
        result = apply_circuit_breaker(
            items,
            max_envelopes=10, max_total_bytes=2000, max_per_envelope_bytes=2000,
            mode="full",
        )
        assert isinstance(result, InlineAll)

    def test_failure_envelopes_still_respect_per_envelope_cap(self) -> None:
        items = [self._flat("big-fail", urgency="red", text="x" * 5000)]
        result = apply_circuit_breaker(
            items,
            max_envelopes=10, max_total_bytes=20000, max_per_envelope_bytes=1024,
            mode="full",
        )
        assert isinstance(result, InlineAll)
        assert "call peek('big-fail')" in result.rendered[0]

    def test_preview_mode_replaces_body_with_marker_with_preview(self) -> None:
        items = [self._flat("p-1", text="hello world this is a preview message")]
        result = apply_circuit_breaker(
            items,
            max_envelopes=5, max_total_bytes=8192, max_per_envelope_bytes=4096,
            mode="preview",
        )
        assert isinstance(result, InlineAll)
        block = result.rendered[0]
        assert "preview='true'" in block
        assert "hello world" in block  # preview present
        assert "call peek('p-1')" in block  # marker still appended

    def test_inlined_envelopes_summary_shape(self) -> None:
        items = [self._flat("s-1", text="hi")]
        result = apply_circuit_breaker(
            items,
            max_envelopes=5, max_total_bytes=8192, max_per_envelope_bytes=4096,
            mode="full",
        )
        assert isinstance(result, InlineAll)
        assert len(result.inlined_envelopes_summary) == 1
        summary = result.inlined_envelopes_summary[0]
        assert summary["id"] == "s-1"
        assert summary["kind"] == "TextMessage"
        assert summary["from"] == "discord-pepper"
        assert summary["urgency"] == "green"
        assert "bytes" in summary


class TestRedeliveryTracker:
    def test_first_sighting_no_marker(self) -> None:
        tracker = RedeliveryTracker(capacity=10)
        assert tracker.note_and_get_marker("e-1") is None

    def test_second_sighting_returns_resend_marker(self) -> None:
        tracker = RedeliveryTracker(capacity=10)
        tracker.note_and_get_marker("e-1")
        assert tracker.note_and_get_marker("e-1") == "[RESEND #2] "

    def test_third_sighting_returns_resend_3(self) -> None:
        tracker = RedeliveryTracker(capacity=10)
        tracker.note_and_get_marker("e-1")
        tracker.note_and_get_marker("e-1")
        assert tracker.note_and_get_marker("e-1") == "[RESEND #3] "

    def test_lru_evicts_oldest(self) -> None:
        tracker = RedeliveryTracker(capacity=2)
        tracker.note_and_get_marker("e-1")
        tracker.note_and_get_marker("e-2")
        tracker.note_and_get_marker("e-3")  # evicts e-1
        # e-1 is now first-sighting again.
        assert tracker.note_and_get_marker("e-1") is None
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest packages/agent-core-channel/tests/test_rendering.py -v`
Expected: ImportError on `InlineAll`, `FallbackToBare`, `apply_circuit_breaker`, `truncation_marker`, `RedeliveryTracker`.

- [ ] **Step 3: Implement the circuit breaker, truncation marker, and redelivery tracker**

Append to `packages/agent-core-channel/src/agent_core_channel/rendering.py`:

```python
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Literal


def truncation_marker(envelope_id: str) -> str:
    """The body replacement for envelopes that exceed the per-envelope cap.

    Tells the agent how to fetch the full payload via the peek() tool
    (issue #70).
    """
    return f"[content elided; envelope_id='{envelope_id}'; call peek('{envelope_id}') for full payload]"


@dataclass(frozen=True)
class InlineAll:
    """Circuit breaker passed — emit the rendered envelopes inline."""
    rendered: list[str]
    inlined_ids: list[str]
    inlined_envelopes_summary: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class FallbackToBare:
    """Circuit breaker tripped — emit today's bare wake; agent calls consume() manually."""
    reason: Literal["envelope_count", "total_bytes"]


CircuitBreakerResult = InlineAll | FallbackToBare


def _is_failure_envelope(env: dict) -> bool:
    """Failure envelopes (yellow/red urgency, or notes prefixed 'error:')
    bypass the BATCH circuit-breaker (loudness invariant). Per-envelope
    cap still applies."""
    urgency = env.get("urgency", "green")
    if urgency in ("yellow", "red"):
        return True
    payload = env.get("payload", {}) or {}
    note = payload.get("note") if isinstance(payload, dict) else None
    if isinstance(note, str) and note.startswith("error:"):
        return True
    return False


def _envelopes_from_item(item: dict) -> list[dict]:
    """Flatten an item from consume()'s response into its underlying envelopes."""
    if "type" not in item:
        return [item]  # flat envelope dict
    if item["type"] == "single":
        return [item["envelope"]]
    if item["type"] == "batch":
        return list(item["envelopes"])
    return []


def _render_with_truncation(env: dict, *, body: str, fallback: bool) -> str:
    """Render an envelope's <inbox> tag with a custom body (truncated/preview)."""
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
    if fallback:
        attrs.append("render='fallback'")

    return f"<inbox {' '.join(attrs)}>\n{body}\n</inbox>"


def _render_preview(env: dict, *, preview_chars: int = 200) -> str:
    """Preview mode: <inbox preview='true'> with first N chars + truncation marker."""
    kind = env.get("kind", "Unknown")
    env_id = env.get("id", "")
    from_ = env.get("from", "")
    urgency = env.get("urgency", "green")
    in_reply_to = env.get("in_reply_to")

    # Get the fully-rendered body, then take a preview prefix.
    full_block = render_envelope(env)
    body_start = full_block.find(">\n") + 2
    body_end = full_block.rfind("\n</inbox>")
    body = full_block[body_start:body_end]
    preview_body = body[:preview_chars]
    if len(body) > preview_chars:
        preview_body += "…"
    body_with_marker = f"{preview_body}\n{truncation_marker(env_id)}"

    attrs = [
        f"kind='{kind}'",
        f"from='{from_}'",
        f"urgency='{urgency}'",
        f"envelope_id='{env_id}'",
        "preview='true'",
    ]
    if in_reply_to:
        attrs.append(f"in_reply_to='{in_reply_to}'")
    return f"<inbox {' '.join(attrs)}>\n{body_with_marker}\n</inbox>"


def apply_circuit_breaker(
    items: list[dict],
    *,
    max_envelopes: int,
    max_total_bytes: int,
    max_per_envelope_bytes: int,
    mode: Literal["full", "preview"],
) -> CircuitBreakerResult:
    """Decide what to inline vs. elide vs. fallback.

    Decision order:
      1. If total envelope count exceeds max_envelopes → FallbackToBare.
      2. Render each envelope. If a single rendered body exceeds
         max_per_envelope_bytes, replace its body with the truncation
         marker (failure envelopes also respect this).
      3. If total rendered bytes exceeds max_total_bytes AND no failure
         envelopes are present → FallbackToBare. Failure envelopes
         bypass this cap (loudness invariant).
      4. Otherwise → InlineAll.
    """
    # Flatten items to underlying envelopes for count check.
    all_envelopes: list[dict] = []
    for item in items:
        all_envelopes.extend(_envelopes_from_item(item))

    if len(all_envelopes) > max_envelopes:
        return FallbackToBare(reason="envelope_count")

    has_failure = any(_is_failure_envelope(e) for e in all_envelopes)

    rendered_blocks: list[str] = []
    summaries: list[dict] = []
    inlined_ids: list[str] = []

    # We render through render_item to preserve batch-prefix semantics, then
    # apply per-envelope-cap truncation by post-processing each block.
    for item in items:
        envs = _envelopes_from_item(item)
        item_blocks = render_item(item)
        for env, block in zip(envs, item_blocks, strict=True):
            if mode == "preview":
                final_block = _render_preview(env)
            else:
                # Compute the body bytes.
                # Find the body slice between ">\n" and "\n</inbox>".
                body_start = block.find(">\n") + 2
                body_end = block.rfind("\n</inbox>")
                body = block[body_start:body_end]
                if len(body.encode("utf-8")) > max_per_envelope_bytes:
                    # Replace body with truncation marker, preserving attrs.
                    final_block = _render_with_truncation(
                        env,
                        body=truncation_marker(env["id"]),
                        fallback=False,
                    )
                    # Preserve the batch attribute if present in original block.
                    if "batch=" in block:
                        # Extract the batch attr value.
                        import re as _re
                        m = _re.search(r"batch='[^']*'", block)
                        if m:
                            final_block = final_block.replace(
                                "<inbox ", f"<inbox {m.group(0)} ", 1
                            )
                else:
                    final_block = block

            rendered_blocks.append(final_block)
            inlined_ids.append(env["id"])
            summaries.append(
                {
                    "id": env["id"],
                    "kind": env.get("kind"),
                    "from": env.get("from"),
                    "urgency": env.get("urgency", "green"),
                    "bytes": len(final_block.encode("utf-8")),
                }
            )

    total_bytes = sum(s["bytes"] for s in summaries)
    if total_bytes > max_total_bytes and not has_failure:
        return FallbackToBare(reason="total_bytes")

    return InlineAll(
        rendered=rendered_blocks,
        inlined_ids=inlined_ids,
        inlined_envelopes_summary=summaries,
    )


# ---------------------------------------------------------------------
# Redelivery tracker — bounded LRU cache.
# Records seen envelope IDs so a redelivered envelope gets a
# [RESEND #N] marker.
# ---------------------------------------------------------------------


class RedeliveryTracker:
    """Bounded LRU cache of envelope IDs. Returns the [RESEND #N] marker
    on second-and-subsequent sightings of the same id (None on first)."""

    def __init__(self, capacity: int = 200) -> None:
        self._capacity = capacity
        self._counts: OrderedDict[str, int] = OrderedDict()

    def note_and_get_marker(self, envelope_id: str) -> str | None:
        if envelope_id in self._counts:
            self._counts[envelope_id] += 1
            self._counts.move_to_end(envelope_id)
            n = self._counts[envelope_id]
            return f"[RESEND #{n}] "
        self._counts[envelope_id] = 1
        self._counts.move_to_end(envelope_id)
        if len(self._counts) > self._capacity:
            self._counts.popitem(last=False)
        return None
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run pytest packages/agent-core-channel/tests/test_rendering.py -v`
Expected: all PASS.

- [ ] **Step 5: Run lint + types**

Run: `uv run ruff check packages/agent-core-channel/src packages/agent-core-channel/tests`
Run: `uv run mypy packages/agent-core-channel/src/agent_core_channel/rendering.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/rendering.py packages/agent-core-channel/tests/test_rendering.py
git commit -m "feat(channel): circuit breaker + truncation + redelivery (#70)

apply_circuit_breaker decides per-snapshot whether to inline all
envelopes, elide individual oversized ones with the peek() truncation
marker, or fall back to today's bare wake. Decision order: envelope
count cap → per-envelope cap (truncation marker) → total bytes cap
(failure envelopes bypass for loudness).

Preview mode renders each envelope with its first 200 body chars +
the truncation marker (the rollout-gate fallback shape — Alt A from
the design discussion).

RedeliveryTracker is a bounded LRU (default 200 IDs) that returns
'[RESEND #N] ' on second-and-subsequent sightings of the same
envelope id, so the agent can recognize redelivery."
```

---

## Task 5: BusClient — persistent MCP client to the bus

**Files:**
- Create: `packages/agent-core-channel/src/agent_core_channel/bus_client.py`
- Test: `packages/agent-core-channel/tests/test_bus_client.py`

- [ ] **Step 1: Write the failing tests**

```python
# packages/agent-core-channel/tests/test_bus_client.py
"""Issue #70: BusClient — persistent MCP client to the bus daemon."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastmcp import Client

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core_channel.bus_client import BusClient


class _StubHandle:
    async def ack(self, envelope_id: str) -> None: ...
    async def publish(self, envelope, to=None) -> None: ...
    async def nack(self, envelope_id, requeue=True) -> None: ...
    def endpoints(self) -> list:
        return []


def _inbound(env_id: str, *, text: str = "hi") -> Envelope:
    return Envelope(
        id=env_id,
        correlation_id=f"corr-{env_id}",
        in_reply_to=None,
        from_="discord",
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        urgency="green",
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_bus_client_consume_no_ack_returns_items() -> None:
    """In-process: BusClient connected directly to the FastMCP server
    returns consume() items without acking them."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound("e-1", text="hello"))

        # Connect a BusClient using the in-process FastMCP server directly.
        async with BusClient.from_in_process(ep._mcp) as bus:
            snapshot = await bus.consume_no_ack(batch_window_seconds=0)

        assert snapshot["meta"]["count"] == 1
        items = snapshot["items"]
        assert len(items) == 1
        # auto_ack=False: envelope is still in the queue.
        assert any(e.id == "e-1" for e in ep._pending)
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_bus_client_passes_batch_window_through() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound("e-a"))
        ep.queue_for_pickup(_inbound("e-b"))

        async with BusClient.from_in_process(ep._mcp) as bus:
            snapshot_flat = await bus.consume_no_ack(batch_window_seconds=0)
            snapshot_batched = await bus.consume_no_ack(batch_window_seconds=30)

        # batch_window_seconds=0 returns flat envelope dicts.
        assert "id" in snapshot_flat["items"][0]
        # batch_window_seconds=30 returns single/batch shapes.
        assert "type" in snapshot_batched["items"][0]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_bus_client_does_not_ack() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound("e-noack"))

        async with BusClient.from_in_process(ep._mcp) as bus:
            await bus.consume_no_ack(batch_window_seconds=0)

        # _pending still has the envelope (not acked).
        assert any(e.id == "e-noack" for e in ep._pending)
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest packages/agent-core-channel/tests/test_bus_client.py -v`
Expected: ModuleNotFoundError on `agent_core_channel.bus_client`.

- [ ] **Step 3: Implement BusClient**

Create `packages/agent-core-channel/src/agent_core_channel/bus_client.py`:

```python
"""Issue #70: persistent MCP client connection from the relay to the bus.

The relay calls consume(auto_ack=False, batch_window_seconds=N) on every
wake to fetch the queue snapshot. This client wraps fastmcp.Client with
the relay-specific contract (no ack, parameter shape).

Connection lifecycle: opened on relay startup, kept alive for the relay's
lifetime. Reconnects on connection loss are handled by fastmcp.Client's
underlying transport — when call_tool raises a transport error, the
caller (stdio_server) catches and falls back to the bare-wake shape for
that wake. Subsequent wakes get a fresh attempt.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, Self

from fastmcp import Client

if TYPE_CHECKING:
    from fastmcp import FastMCP


class BusClient:
    """Persistent MCP client to the bus daemon's per-agent endpoint.

    Two construction modes:

    - ``BusClient(daemon_url, agent)`` — production: connects to
      ``{daemon_url}/mcp/{agent}`` over HTTP.
    - ``BusClient.from_in_process(fastmcp_server)`` — tests: connects
      directly to a FastMCP server instance, no HTTP transport.

    Use as an async context manager. ``consume_no_ack`` is the single
    operation the relay needs; further tools (peek, etc.) can be added
    here as the relay grows.
    """

    def __init__(self, daemon_url: str, agent: str) -> None:
        url = f"{daemon_url.rstrip('/')}/mcp/{agent}"
        self._client = Client(url)

    @classmethod
    def from_in_process(cls, server: FastMCP) -> Self:
        instance = cls.__new__(cls)
        instance._client = Client(server)
        return instance

    async def __aenter__(self) -> Self:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        await self._client.__aexit__(exc_type, exc, tb)

    async def consume_no_ack(self, *, batch_window_seconds: int = 30) -> dict:
        """Fetch the queue snapshot without acking any envelopes.

        Returns the consume() response shape:
        ``{"meta": {...}, "items": [...]}``.
        """
        result = await self._client.call_tool(
            "consume",
            {
                "auto_ack": False,
                "batch_window_seconds": batch_window_seconds,
            },
        )
        return result.data  # type: ignore[no-any-return]
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run pytest packages/agent-core-channel/tests/test_bus_client.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Run lint + types**

Run: `uv run ruff check packages/agent-core-channel/src/agent_core_channel/bus_client.py packages/agent-core-channel/tests/test_bus_client.py`
Run: `uv run mypy packages/agent-core-channel/src/agent_core_channel/bus_client.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/bus_client.py packages/agent-core-channel/tests/test_bus_client.py
git commit -m "feat(channel): persistent MCP client to the bus (#70)

BusClient wraps fastmcp.Client with the relay-specific contract:
opens once at relay startup, keeps the session alive, calls consume()
with auto_ack=False on every wake. Two construction modes — HTTP
(production: daemon_url + agent → /mcp/<agent>) and in-process (tests:
direct FastMCP server reference).

Connection lifecycle is async-context-managed. Transport errors
during consume_no_ack propagate up; the relay's wire-up will catch
and fall back to bare wake (issue #70's transient-failure contract).
fastmcp.Client's underlying transport handles reconnect on subsequent
calls."
```

---

## Task 6: Wake-audit JSONL writer

**Files:**
- Create: `packages/agent-core-channel/src/agent_core_channel/wake_audit.py`
- Test: `packages/agent-core-channel/tests/test_wake_audit.py`

- [ ] **Step 1: Write the failing tests**

```python
# packages/agent-core-channel/tests/test_wake_audit.py
"""Issue #70: WakeAuditWriter — append-only JSONL with relay-side wake events."""

from __future__ import annotations

import json
from pathlib import Path

from agent_core_channel.wake_audit import WakeAuditWriter


def test_writer_creates_file_lazily(tmp_path: Path) -> None:
    target = tmp_path / "pepper.jsonl"
    writer = WakeAuditWriter(target)
    assert not target.exists()
    writer.write_inlined(
        wake_id="w-1",
        agent="pepper",
        mode="full",
        envelopes_inlined=[
            {"id": "e-1", "kind": "TextMessage", "from": "discord-pepper",
             "urgency": "green", "bytes": 87}
        ],
        queue_total_count=1,
    )
    assert target.exists()


def test_writer_appends_one_line_per_event(tmp_path: Path) -> None:
    target = tmp_path / "pepper.jsonl"
    writer = WakeAuditWriter(target)
    writer.write_inlined(
        wake_id="w-1", agent="pepper", mode="full",
        envelopes_inlined=[], queue_total_count=0,
    )
    writer.write_inlined(
        wake_id="w-2", agent="pepper", mode="full",
        envelopes_inlined=[], queue_total_count=0,
    )
    lines = target.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    e1 = json.loads(lines[0])
    e2 = json.loads(lines[1])
    assert e1["wake_id"] == "w-1"
    assert e2["wake_id"] == "w-2"


def test_writer_records_fallback(tmp_path: Path) -> None:
    target = tmp_path / "pepper.jsonl"
    writer = WakeAuditWriter(target)
    writer.write_fallback(
        wake_id="w-3", agent="pepper", mode="full",
        reason="envelope_count",
    )
    line = json.loads(target.read_text(encoding="utf-8").strip())
    assert line["wake_id"] == "w-3"
    assert line["fallback"] == "envelope_count"
    assert line["envelopes_inlined"] == []


def test_writer_records_render_error(tmp_path: Path) -> None:
    target = tmp_path / "pepper.jsonl"
    writer = WakeAuditWriter(target)
    writer.write_fallback(
        wake_id="w-4", agent="pepper", mode="full",
        reason="render_error", error_message="boom",
    )
    line = json.loads(target.read_text(encoding="utf-8").strip())
    assert line["fallback"] == "render_error"
    assert line["error"] == "boom"


def test_writer_includes_iso_timestamp(tmp_path: Path) -> None:
    target = tmp_path / "pepper.jsonl"
    writer = WakeAuditWriter(target)
    writer.write_inlined(
        wake_id="w-ts", agent="pepper", mode="full",
        envelopes_inlined=[], queue_total_count=0,
    )
    line = json.loads(target.read_text(encoding="utf-8").strip())
    assert "ts" in line
    # ISO 8601 format with 'T' and timezone marker.
    assert "T" in line["ts"]
    assert line["ts"].endswith("+00:00") or line["ts"].endswith("Z")
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest packages/agent-core-channel/tests/test_wake_audit.py -v`
Expected: ModuleNotFoundError on `agent_core_channel.wake_audit`.

- [ ] **Step 3: Implement WakeAuditWriter**

Create `packages/agent-core-channel/src/agent_core_channel/wake_audit.py`:

```python
"""Issue #70: per-wake audit log written by the relay.

One JSONL line per wake event. Schema:

    {
        "ts": "2026-05-09T03:29:22+00:00",
        "agent": "pepper",
        "wake_id": "w-abc123",
        "mode": "full",
        "envelopes_inlined": [
            {"id": "e-001", "kind": "TextMessage", "from": "discord-pepper",
             "urgency": "green", "bytes": 87}
        ],
        "queue_total_count": 1,
        "fallback": null
    }

The bus's existing mcp_audit middleware records the agent's subsequent
tool calls. The wake-stats analyzer (Phase 5) joins these two log streams
to compute per-wake outcomes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class WakeAuditWriter:
    """Append-only JSONL writer for relay-side wake events.

    Creates the parent directory and target file lazily on first write.
    Each ``write_*`` call writes exactly one line. Atomic line writes are
    achieved via a single ``write`` call (POSIX append semantics handle
    the rest for our line sizes).
    """

    def __init__(self, target: Path) -> None:
        self._target = Path(target)

    def _emit(self, payload: dict) -> None:
        self._target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, sort_keys=True, default=str) + "\n"
        with self._target.open("a", encoding="utf-8") as f:
            f.write(line)

    def write_inlined(
        self,
        *,
        wake_id: str,
        agent: str,
        mode: str,
        envelopes_inlined: list[dict],
        queue_total_count: int,
    ) -> None:
        self._emit(
            {
                "ts": datetime.now(UTC).isoformat(),
                "agent": agent,
                "wake_id": wake_id,
                "mode": mode,
                "envelopes_inlined": envelopes_inlined,
                "queue_total_count": queue_total_count,
                "fallback": None,
            }
        )

    def write_fallback(
        self,
        *,
        wake_id: str,
        agent: str,
        mode: str,
        reason: str,
        error_message: str | None = None,
    ) -> None:
        payload: dict = {
            "ts": datetime.now(UTC).isoformat(),
            "agent": agent,
            "wake_id": wake_id,
            "mode": mode,
            "envelopes_inlined": [],
            "queue_total_count": 0,
            "fallback": reason,
        }
        if error_message is not None:
            payload["error"] = error_message
        self._emit(payload)
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run pytest packages/agent-core-channel/tests/test_wake_audit.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Run lint + types**

Run: `uv run ruff check packages/agent-core-channel/src/agent_core_channel/wake_audit.py packages/agent-core-channel/tests/test_wake_audit.py`
Run: `uv run mypy packages/agent-core-channel/src/agent_core_channel/wake_audit.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/wake_audit.py packages/agent-core-channel/tests/test_wake_audit.py
git commit -m "feat(channel): wake-audit JSONL writer (#70)

Append-only per-wake event log written by the relay. One JSONL line
per wake with envelope_inlined entries, queue_total_count, mode, and
optional fallback reason / error message. Creates the target file
lazily; the bus daemon's existing mcp_audit middleware records the
agent's subsequent tool calls; the wake-stats analyzer (separate task)
joins these two streams to compute per-wake outcomes for the rollout
gate."
```

---

## Task 7: Wire the render pipeline into the SSE pump

**Files:**
- Modify: `packages/agent-core-channel/src/agent_core_channel/stdio_server.py`
- Test: `packages/agent-core-channel/tests/test_stdio_server_inline.py` (new file)

This is the central behavior change. The `_sse_pump` stops emitting bare wakes and starts calling `consume_no_ack` + rendering.

- [ ] **Step 1: Write the failing integration tests**

```python
# packages/agent-core-channel/tests/test_stdio_server_inline.py
"""Issue #70: integration tests for the relay's wire-up.

The relay receives a wake on its SSE stream, fetches the queue from the
bus via BusClient, applies rendering, emits the rich notification."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core_channel.bus_client import BusClient
from agent_core_channel.stdio_server import process_wake_event
from agent_core_channel.wake_audit import WakeAuditWriter


def _inbound(env_id: str, *, text: str = "hi") -> Envelope:
    return Envelope(
        id=env_id,
        correlation_id=f"corr-{env_id}",
        in_reply_to=None,
        from_="discord",
        to="pepper",
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        urgency="green",
        created_at=datetime.now(UTC),
    )


class _StubHandle:
    async def ack(self, envelope_id: str) -> None: ...
    async def publish(self, envelope, to=None) -> None: ...
    async def nack(self, envelope_id, requeue=True) -> None: ...
    def endpoints(self) -> list:
        return []


class _CapturingWriteStream:
    """Captures everything sent to it for assertion."""
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, msg) -> None:
        self.sent.append(msg)


@pytest.mark.asyncio
async def test_process_wake_event_inlines_single_envelope(tmp_path: Path) -> None:
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound("e-1", text="hello world"))

        wake_summary = {
            "content": "INBOX: pending (pepper)",
            "meta": {"endpoint": "pepper", "fired_at": "2026-05-09T03:29:22Z"},
        }
        write = _CapturingWriteStream()
        from agent_core_channel.config import RelayConfig
        config = RelayConfig()  # full mode, defaults

        async with BusClient.from_in_process(ep._mcp) as bus:
            audit = WakeAuditWriter(tmp_path / "pepper.jsonl")
            from agent_core_channel.rendering import RedeliveryTracker
            tracker = RedeliveryTracker()
            await process_wake_event(
                wake_summary=wake_summary, write_stream=write, bus=bus,
                audit=audit, agent="pepper", config=config, redelivery=tracker,
            )

        assert len(write.sent) == 1
        msg = write.sent[0]
        # The notification's content carries the rendered <inbox> block.
        params = msg.message.root.params
        assert "<inbox" in params["content"]
        assert "envelope_id='e-1'" in params["content"]
        assert "hello world" in params["content"]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_process_wake_event_phantom_wake_suppresses_emission(tmp_path: Path) -> None:
    """Empty queue → no notification emitted; audit row written."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        wake_summary = {
            "content": "INBOX: pending (pepper)",
            "meta": {"endpoint": "pepper", "fired_at": "2026-05-09T03:29:22Z"},
        }
        write = _CapturingWriteStream()
        from agent_core_channel.config import RelayConfig
        config = RelayConfig()
        from agent_core_channel.rendering import RedeliveryTracker
        tracker = RedeliveryTracker()

        audit_path = tmp_path / "pepper.jsonl"
        async with BusClient.from_in_process(ep._mcp) as bus:
            audit = WakeAuditWriter(audit_path)
            await process_wake_event(
                wake_summary=wake_summary, write_stream=write, bus=bus,
                audit=audit, agent="pepper", config=config, redelivery=tracker,
            )

        # No notification emitted (phantom wake).
        assert write.sent == []
        # But the audit log records it as fallback="empty_queue".
        import json
        line = json.loads(audit_path.read_text().strip())
        assert line["fallback"] == "empty_queue"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_process_wake_event_circuit_breaker_emits_bare_wake(tmp_path: Path) -> None:
    """6 envelopes (over the default cap of 5) → bare wake fallback."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        for i in range(6):
            ep.queue_for_pickup(_inbound(f"e-{i}", text=f"msg {i}"))

        wake_summary = {
            "content": "INBOX: pending (pepper)",
            "meta": {"endpoint": "pepper", "fired_at": "2026-05-09T03:29:22Z"},
        }
        write = _CapturingWriteStream()
        from agent_core_channel.config import RelayConfig
        config = RelayConfig()
        from agent_core_channel.rendering import RedeliveryTracker
        tracker = RedeliveryTracker()

        async with BusClient.from_in_process(ep._mcp) as bus:
            audit = WakeAuditWriter(tmp_path / "pepper.jsonl")
            await process_wake_event(
                wake_summary=wake_summary, write_stream=write, bus=bus,
                audit=audit, agent="pepper", config=config, redelivery=tracker,
            )

        # Bare wake emitted (circuit breaker tripped).
        assert len(write.sent) == 1
        params = write.sent[0].message.root.params
        assert params["content"] == "INBOX: pending (pepper)"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_process_wake_event_disabled_mode_passes_through(tmp_path: Path) -> None:
    """inline_mode=disabled → identity passthrough of the bare wake."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    handle = _StubHandle()
    await ep.start(handle)  # type: ignore[arg-type]
    try:
        ep.queue_for_pickup(_inbound("e-d"))

        wake_summary = {
            "content": "INBOX: pending (pepper)",
            "meta": {"endpoint": "pepper", "fired_at": "2026-05-09T03:29:22Z"},
        }
        write = _CapturingWriteStream()
        from agent_core_channel.config import RelayConfig
        config = RelayConfig(inline_mode="disabled")
        from agent_core_channel.rendering import RedeliveryTracker
        tracker = RedeliveryTracker()

        async with BusClient.from_in_process(ep._mcp) as bus:
            audit = WakeAuditWriter(tmp_path / "pepper.jsonl")
            await process_wake_event(
                wake_summary=wake_summary, write_stream=write, bus=bus,
                audit=audit, agent="pepper", config=config, redelivery=tracker,
            )

        assert len(write.sent) == 1
        params = write.sent[0].message.root.params
        assert params["content"] == "INBOX: pending (pepper)"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_process_wake_event_render_error_falls_back_to_bare(tmp_path: Path) -> None:
    """Bus client raises → fall back to bare wake; audit logs the error."""
    class _BrokenBus:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def consume_no_ack(self, *, batch_window_seconds: int = 30) -> dict:
            raise RuntimeError("simulated bus failure")

    wake_summary = {
        "content": "INBOX: pending (pepper)",
        "meta": {"endpoint": "pepper", "fired_at": "2026-05-09T03:29:22Z"},
    }
    write = _CapturingWriteStream()
    from agent_core_channel.config import RelayConfig
    config = RelayConfig()
    from agent_core_channel.rendering import RedeliveryTracker
    tracker = RedeliveryTracker()
    audit_path = tmp_path / "pepper.jsonl"
    audit = WakeAuditWriter(audit_path)

    async with _BrokenBus() as bus:
        await process_wake_event(
            wake_summary=wake_summary, write_stream=write, bus=bus,
            audit=audit, agent="pepper", config=config, redelivery=tracker,
        )

    # Bare wake emitted (render error fallback).
    assert len(write.sent) == 1
    params = write.sent[0].message.root.params
    assert params["content"] == "INBOX: pending (pepper)"
    # Audit logs the error.
    import json
    line = json.loads(audit_path.read_text().strip())
    assert line["fallback"] == "render_error"
    assert "simulated bus failure" in line["error"]
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest packages/agent-core-channel/tests/test_stdio_server_inline.py -v`
Expected: ImportError on `process_wake_event` and `agent_core_channel.config`.

- [ ] **Step 3: Implement the wire-up in stdio_server.py**

Modify `packages/agent-core-channel/src/agent_core_channel/stdio_server.py`. Add at the top of the file (with other imports):

```python
import uuid
from typing import Protocol

from agent_core_channel.rendering import (
    apply_circuit_breaker,
    InlineAll,
    FallbackToBare,
    RedeliveryTracker,
)
from agent_core_channel.wake_audit import WakeAuditWriter
```

Replace the existing `_RELAY_INSTRUCTIONS` constant with the updated one:

```python
_RELAY_INSTRUCTIONS = (
    "Inbox wake notifications for an agent-core agent. "
    'Messages arrive as JSON-RPC notifications with method "notifications/claude/channel". '
    "When inline mode is enabled (default), params.content carries the inbound "
    "envelope(s) directly as one or more <inbox kind='X' from='Y' urgency='Z' "
    "envelope_id='abc'>...body...</inbox> blocks. Read the body inline; reply via "
    "mcp__agent-core__reply(in_reply_to=envelope_id, payload=...) for atomic "
    "publish+ack. Dismiss without reply via mcp__agent-core__handle(envelope_id). "
    "If a body shows '[content elided; envelope_id=X; call peek(X) for full payload]', "
    "call mcp__agent-core__peek(envelope_id=X) to fetch the full envelope. "
    "If params.content is the bare 'INBOX: pending (<endpoint>)' string (circuit "
    "breaker tripped, transient failure, or inline mode disabled), call "
    "mcp__agent-core__consume() for the authoritative queue snapshot and proceed "
    "as before. Higher urgency tiers (red > yellow > green) are presented first. "
    "Do not wait for user input — the notification IS the prompt."
)
```

Add a `BusClientProtocol` for typing and the `process_wake_event` function. Insert after the existing `emit_channel_notification` function:

```python
class BusClientProtocol(Protocol):
    """Subset of BusClient that process_wake_event needs (so test fakes work)."""

    async def consume_no_ack(self, *, batch_window_seconds: int = 30) -> dict: ...


async def process_wake_event(
    *,
    wake_summary: dict,
    write_stream: anyio.abc.ObjectSendStream[SessionMessage],
    bus: BusClientProtocol,
    audit: WakeAuditWriter,
    agent: str,
    config,  # RelayConfig — imported lazily to avoid circular import
    redelivery: RedeliveryTracker,
) -> None:
    """Apply the inline-content render pipeline to a single wake event.

    On any failure or circuit-breaker trip, falls back to today's bare-wake
    shape — inline is the fast path, the slow path always works.

    Issue #70.
    """
    wake_id = uuid.uuid4().hex

    if config.inline_mode == "disabled":
        await emit_channel_notification(write_stream, wake_summary)
        audit.write_fallback(
            wake_id=wake_id, agent=agent, mode=config.inline_mode,
            reason="disabled_mode",
        )
        return

    try:
        snapshot = await bus.consume_no_ack(batch_window_seconds=30)
    except Exception as exc:
        log.warning("relay: bus client raised; falling back to bare wake: %s", exc)
        await emit_channel_notification(write_stream, wake_summary)
        audit.write_fallback(
            wake_id=wake_id, agent=agent, mode=config.inline_mode,
            reason="render_error", error_message=str(exc),
        )
        return

    items = snapshot.get("items", [])
    if not items:
        # Phantom wake — suppress agent-facing notification but log it.
        audit.write_fallback(
            wake_id=wake_id, agent=agent, mode=config.inline_mode,
            reason="empty_queue",
        )
        return

    try:
        result = apply_circuit_breaker(
            items,
            max_envelopes=config.max_envelopes,
            max_total_bytes=config.max_bytes,
            max_per_envelope_bytes=config.per_envelope_bytes,
            mode="full" if config.inline_mode == "full" else "preview",
        )
    except Exception as exc:
        log.warning("relay: render pipeline raised; falling back to bare wake: %s", exc)
        await emit_channel_notification(write_stream, wake_summary)
        audit.write_fallback(
            wake_id=wake_id, agent=agent, mode=config.inline_mode,
            reason="render_error", error_message=str(exc),
        )
        return

    if isinstance(result, FallbackToBare):
        await emit_channel_notification(write_stream, wake_summary)
        audit.write_fallback(
            wake_id=wake_id, agent=agent, mode=config.inline_mode,
            reason=result.reason,
        )
        return

    assert isinstance(result, InlineAll)
    # Apply RESEND markers.
    final_blocks: list[str] = []
    for env_id, block in zip(result.inlined_ids, result.rendered, strict=True):
        marker = redelivery.note_and_get_marker(env_id)
        if marker is None:
            final_blocks.append(block)
        else:
            final_blocks.append(block.replace("<inbox ", f"<inbox resend='{marker.strip()}' ", 1))

    rich_summary = {
        "content": "\n\n".join(final_blocks),
        "meta": {
            **wake_summary.get("meta", {}),
            "wake_id": wake_id,
            "queue_total_count": str(snapshot.get("meta", {}).get("count", 0)),
            "envelopes_inlined": ",".join(result.inlined_ids),
        },
    }
    await emit_channel_notification(write_stream, rich_summary)
    audit.write_inlined(
        wake_id=wake_id, agent=agent, mode=config.inline_mode,
        envelopes_inlined=result.inlined_envelopes_summary,
        queue_total_count=int(snapshot.get("meta", {}).get("count", 0)),
    )
```

Replace the existing `_sse_pump` with one that wires `process_wake_event`:

```python
async def _sse_pump(
    agent: str,
    daemon_url: str,
    write_stream: anyio.abc.ObjectSendStream[SessionMessage],
    initialized: anyio.Event | None = None,
    *,
    bus: BusClientProtocol,
    audit: WakeAuditWriter,
    config,  # RelayConfig
    redelivery: RedeliveryTracker,
) -> None:
    """Read events from /notify/<agent> and route through process_wake_event."""
    if initialized is not None:
        await initialized.wait()
    async for wake_summary in iter_notify_events(agent=agent, daemon_url=daemon_url):
        await process_wake_event(
            wake_summary=wake_summary,
            write_stream=write_stream,
            bus=bus,
            audit=audit,
            agent=agent,
            config=config,
            redelivery=redelivery,
        )
```

Update `run_relay` to construct `BusClient`, `WakeAuditWriter`, and `RedeliveryTracker`, and pass them through:

```python
async def run_relay(agent: str, daemon_url: str) -> None:
    """Run the channel relay until stdin closes or a fatal error.

    Three concurrent tasks under one task group:
    - The MCP stdio server loop (Server.run reading from stdin, writing to stdout).
    - The persistent BusClient connection to /mcp/<agent>.
    - The SSE pump (consume daemon /notify/<agent>, route through
      process_wake_event for inline rendering).

    Stdin closes → Server.run returns → task group cancels SSE pump and
    BusClient. SSE pump dies (it shouldn't — it has its own retry loop) →
    cancels Server.run.
    """
    from agent_core_channel.bus_client import BusClient
    from agent_core_channel.config import RelayConfig

    server = _build_server()
    init_options = server.create_initialization_options(
        notification_options=NotificationOptions(),
        experimental_capabilities={"claude/channel": {}},
    )

    config = RelayConfig()  # Phase 6 will wire CLI/env/YAML overrides.
    audit_dir = Path.home() / ".agent-core" / "wake_audit"
    audit = WakeAuditWriter(audit_dir / f"{agent}.jsonl")
    redelivery = RedeliveryTracker()

    async with stdio_server() as (read_stream, write_stream):
        initialized = anyio.Event()
        gated_write_stream = _InitializationGateWriteStream(write_stream, initialized)
        async with BusClient(daemon_url, agent) as bus:
            async with anyio.create_task_group() as tg:
                tg.start_soon(
                    _sse_pump, agent, daemon_url, cast(Any, gated_write_stream),
                    initialized,
                    bus=bus, audit=audit, config=config, redelivery=redelivery,
                )
                await server.run(read_stream, cast(Any, gated_write_stream), init_options)
                tg.cancel_scope.cancel()
```

Add at the top of the file:

```python
from pathlib import Path
```

- [ ] **Step 4: Add a stub `RelayConfig` for the test imports to work**

Phase 9 implements the full config layer. For now, create a minimal stub so the wire-up tests pass.

Create `packages/agent-core-channel/src/agent_core_channel/config.py`:

```python
"""Issue #70: relay configuration. Phase 6 of the implementation plan
will replace this stub with full CLI/env/YAML resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RelayConfig:
    inline_mode: Literal["full", "preview", "disabled"] = "full"
    max_envelopes: int = 5
    max_bytes: int = 8192
    per_envelope_bytes: int = 4096
```

- [ ] **Step 5: Run tests to confirm they pass**

Run: `uv run pytest packages/agent-core-channel/tests/test_stdio_server_inline.py -v`
Expected: 5 PASS.

- [ ] **Step 6: Run the existing relay tests to confirm no regression**

Run: `uv run pytest packages/agent-core-channel/tests -v`
Expected: all PASS (existing `test_stdio_server.py`, `test_sse_client.py`, `test_cli.py`, `test_end_to_end_relay.py` continue to pass; the new tests pass too).

- [ ] **Step 7: Run lint + types**

Run: `uv run ruff check packages/agent-core-channel/src packages/agent-core-channel/tests`
Run: `uv run mypy packages/agent-core-channel/src/agent_core_channel/stdio_server.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/stdio_server.py packages/agent-core-channel/src/agent_core_channel/config.py packages/agent-core-channel/tests/test_stdio_server_inline.py
git commit -m "feat(channel): wire inline-content render pipeline (#70)

Replaces the SSE pump's identity passthrough with the inline-content
render pipeline. process_wake_event() handles every wake: calls
consume(auto_ack=False) via BusClient, applies circuit breaker, emits
the rich notification or falls back to today's bare wake.

Five behavior branches, each tested:
- inline_mode=disabled → bare wake (escape hatch)
- empty queue (phantom wake) → suppress agent-facing notification, log
- circuit breaker trips → bare wake, log fallback reason
- bus client / render exception → bare wake, log error
- normal case → rich <inbox>...</inbox> notification

RESEND markers applied to redelivered envelopes via the bounded LRU
tracker. _RELAY_INSTRUCTIONS updated to describe both the inline shape
and the bare-wake fallback so the agent's mental model is unambiguous.

Stub RelayConfig added (full config resolver lands in Phase 6 of
this PR)."
```

---

## Task 8: Wake-stats analyzer + CLI subcommand

**Files:**
- Create: `packages/core/src/agent_core/wake_stats.py`
- Test: `packages/core/tests/test_wake_stats.py`
- Modify: `packages/core/src/agent_core/cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# packages/core/tests/test_wake_stats.py
"""Issue #70: wake-stats analyzer joins relay wake-audit + bus mcp_audit
and classifies each wake's outcome."""

from __future__ import annotations

import json
from pathlib import Path

from agent_core.wake_stats import classify_wakes, summarize, WakeOutcome


def _wake_line(wake_id: str, ts: str, env_ids: list[str], fallback: str | None = None) -> dict:
    return {
        "ts": ts,
        "agent": "pepper",
        "wake_id": wake_id,
        "mode": "full",
        "envelopes_inlined": [{"id": eid, "kind": "TextMessage", "from": "discord",
                                "urgency": "green", "bytes": 50} for eid in env_ids],
        "queue_total_count": len(env_ids),
        "fallback": fallback,
    }


def _audit_line(ts: str, tool: str, args: dict) -> dict:
    return {
        "ts": ts,
        "agent": "pepper",
        "session_id": "s-1",
        "tool": tool,
        "args": args,
    }


def test_classify_replied(tmp_path: Path) -> None:
    wake = tmp_path / "wake.jsonl"
    audit = tmp_path / "audit.jsonl"
    wake.write_text(json.dumps(_wake_line("w-1", "2026-05-09T00:00:00+00:00", ["e-1"])) + "\n")
    audit.write_text(json.dumps(_audit_line(
        "2026-05-09T00:00:30+00:00", "reply", {"in_reply_to": "e-1"}
    )) + "\n")

    outcomes = classify_wakes(wake_audit_path=wake, mcp_audit_path=audit, window_seconds=300)
    assert len(outcomes) == 1
    assert outcomes[0].classification == "replied"
    assert outcomes[0].wake_id == "w-1"


def test_classify_handled(tmp_path: Path) -> None:
    wake = tmp_path / "wake.jsonl"
    audit = tmp_path / "audit.jsonl"
    wake.write_text(json.dumps(_wake_line("w-h", "2026-05-09T00:00:00+00:00", ["e-h"])) + "\n")
    audit.write_text(json.dumps(_audit_line(
        "2026-05-09T00:00:10+00:00", "handle", {"envelope_id": "e-h"}
    )) + "\n")

    outcomes = classify_wakes(wake_audit_path=wake, mcp_audit_path=audit, window_seconds=300)
    assert outcomes[0].classification == "handled"


def test_classify_engaged_with_fetch(tmp_path: Path) -> None:
    wake = tmp_path / "wake.jsonl"
    audit = tmp_path / "audit.jsonl"
    wake.write_text(json.dumps(_wake_line("w-p", "2026-05-09T00:00:00+00:00", ["e-p"])) + "\n")
    lines = [
        _audit_line("2026-05-09T00:00:05+00:00", "peek", {"envelope_id": "e-p"}),
        _audit_line("2026-05-09T00:00:15+00:00", "reply", {"in_reply_to": "e-p"}),
    ]
    audit.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    outcomes = classify_wakes(wake_audit_path=wake, mcp_audit_path=audit, window_seconds=300)
    assert outcomes[0].classification == "engaged-with-fetch"


def test_classify_side_action(tmp_path: Path) -> None:
    """Next call is `send` to a different recipient — Pepper's
    cross-channel 2-call case."""
    wake = tmp_path / "wake.jsonl"
    audit = tmp_path / "audit.jsonl"
    wake.write_text(json.dumps(_wake_line("w-s", "2026-05-09T00:00:00+00:00", ["e-s"])) + "\n")
    audit.write_text(json.dumps(_audit_line(
        "2026-05-09T00:00:30+00:00", "send", {"to": "slack", "kind": "TextMessage"}
    )) + "\n")

    outcomes = classify_wakes(wake_audit_path=wake, mcp_audit_path=audit, window_seconds=300)
    assert outcomes[0].classification == "side-action"


def test_classify_ignored(tmp_path: Path) -> None:
    wake = tmp_path / "wake.jsonl"
    audit = tmp_path / "audit.jsonl"
    wake.write_text(json.dumps(_wake_line("w-i", "2026-05-09T00:00:00+00:00", ["e-i"])) + "\n")
    # No following audit lines.
    audit.write_text("")

    outcomes = classify_wakes(wake_audit_path=wake, mcp_audit_path=audit, window_seconds=300)
    assert outcomes[0].classification == "ignored"


def test_classify_skip_fallback_wakes(tmp_path: Path) -> None:
    """Wakes with fallback set are not 'inline' wakes — exclude from stats."""
    wake = tmp_path / "wake.jsonl"
    audit = tmp_path / "audit.jsonl"
    wake.write_text(
        json.dumps(_wake_line("w-fb", "2026-05-09T00:00:00+00:00", [], fallback="empty_queue")) + "\n"
    )
    audit.write_text("")

    outcomes = classify_wakes(wake_audit_path=wake, mcp_audit_path=audit, window_seconds=300)
    # Empty-queue wakes are excluded — no envelopes_inlined to evaluate.
    assert outcomes == []


def test_summarize_rates(tmp_path: Path) -> None:
    outcomes = [
        WakeOutcome(wake_id="a", classification="replied"),
        WakeOutcome(wake_id="b", classification="replied"),
        WakeOutcome(wake_id="c", classification="ignored"),
        WakeOutcome(wake_id="d", classification="side-action"),
    ]
    summary = summarize(outcomes)
    assert summary["total"] == 4
    assert summary["counts"]["replied"] == 2
    assert summary["counts"]["ignored"] == 1
    assert summary["counts"]["side-action"] == 1
    # The 30% rollout-gate metric: ignored + side-action vs total.
    no_engagement_rate = (1 + 1) / 4
    assert abs(summary["no_engagement_rate"] - no_engagement_rate) < 1e-9
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest packages/core/tests/test_wake_stats.py -v`
Expected: ModuleNotFoundError on `agent_core.wake_stats`.

- [ ] **Step 3: Implement the analyzer**

Create `packages/core/src/agent_core/wake_stats.py`:

```python
"""Issue #70: wake-stats analyzer.

Joins the relay's wake-audit JSONL with the bus's mcp-audit JSONL to
classify each wake's outcome. The 30% rollout gate uses this data to
decide whether to keep inline_mode=full or fall back to inline_mode=preview.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

Classification = Literal["replied", "handled", "engaged-with-fetch", "side-action", "ignored"]


@dataclass(frozen=True)
class WakeOutcome:
    wake_id: str
    classification: Classification


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def classify_wakes(
    *,
    wake_audit_path: Path,
    mcp_audit_path: Path,
    window_seconds: int = 300,
) -> list[WakeOutcome]:
    """Read both logs and classify each inline wake by its first follow-up call.

    Wakes with fallback set (empty_queue, render_error, circuit-breaker tripped)
    are excluded — they have no inlined envelopes to evaluate.
    """
    wake_lines = _read_jsonl(wake_audit_path)
    audit_lines = sorted(_read_jsonl(mcp_audit_path), key=lambda x: _parse_iso(x["ts"]))

    outcomes: list[WakeOutcome] = []
    for wake in wake_lines:
        if wake.get("fallback") is not None:
            continue
        envelope_ids = {e["id"] for e in wake.get("envelopes_inlined", [])}
        if not envelope_ids:
            continue
        wake_ts = _parse_iso(wake["ts"])
        window_end = wake_ts.timestamp() + window_seconds

        # Find the first audit line within the window.
        first_call = None
        for line in audit_lines:
            ts = _parse_iso(line["ts"])
            if ts <= wake_ts:
                continue
            if ts.timestamp() > window_end:
                break
            first_call = line
            break

        if first_call is None:
            outcomes.append(WakeOutcome(wake_id=wake["wake_id"], classification="ignored"))
            continue

        tool = first_call.get("tool")
        args = first_call.get("args", {}) or {}

        if tool == "reply" and args.get("in_reply_to") in envelope_ids:
            outcomes.append(WakeOutcome(wake_id=wake["wake_id"], classification="replied"))
        elif tool == "handle" and args.get("envelope_id") in envelope_ids:
            outcomes.append(WakeOutcome(wake_id=wake["wake_id"], classification="handled"))
        elif tool == "peek" and args.get("envelope_id") in envelope_ids:
            # Look ahead for a subsequent reply/handle in the same window.
            outcomes.append(
                WakeOutcome(wake_id=wake["wake_id"], classification="engaged-with-fetch")
            )
        else:
            outcomes.append(WakeOutcome(wake_id=wake["wake_id"], classification="side-action"))

    return outcomes


def summarize(outcomes: list[WakeOutcome]) -> dict:
    """Aggregate outcomes into rates suitable for the rollout gate."""
    counts: Counter[str] = Counter(o.classification for o in outcomes)
    total = len(outcomes)
    no_engagement = counts.get("ignored", 0) + counts.get("side-action", 0)
    return {
        "total": total,
        "counts": dict(counts),
        "no_engagement_rate": (no_engagement / total) if total else 0.0,
    }
```

- [ ] **Step 4: Run analyzer tests to confirm they pass**

Run: `uv run pytest packages/core/tests/test_wake_stats.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Wire the CLI subcommand**

Modify `packages/core/src/agent_core/cli.py`. Add this subcommand definition before the `apply_cli_subapps(...)` line at the bottom:

```python
@app.command("wake-stats")
def wake_stats(
    agent: str = typer.Argument(..., help="Agent name (e.g., 'pepper')."),
    window_seconds: int = typer.Option(
        300,
        "--window-seconds",
        help="Time window after each wake to look for the next agent call.",
    ),
    wake_audit_dir: Path = typer.Option(
        Path.home() / ".agent-core" / "wake_audit",
        "--wake-audit-dir",
        help="Directory holding per-agent wake-audit JSONL files.",
    ),
    mcp_audit_dir: Path = typer.Option(
        Path.home() / ".agent-core" / "mcp_audit",
        "--mcp-audit-dir",
        help="Directory holding per-agent mcp-audit JSONL files.",
    ),
) -> None:
    """Classify each wake's outcome and print rolling rates (issue #70)."""
    from agent_core.wake_stats import classify_wakes, summarize

    wake_path = wake_audit_dir / f"{agent}.jsonl"
    audit_path = mcp_audit_dir / f"{agent}.jsonl"
    outcomes = classify_wakes(
        wake_audit_path=wake_path,
        mcp_audit_path=audit_path,
        window_seconds=window_seconds,
    )
    summary = summarize(outcomes)
    typer.echo(json.dumps(summary, indent=2))
```

- [ ] **Step 6: Run lint + types**

Run: `uv run ruff check packages/core/src/agent_core/wake_stats.py packages/core/src/agent_core/cli.py packages/core/tests/test_wake_stats.py`
Run: `uv run mypy packages/core/src/agent_core/wake_stats.py`
Expected: clean.

- [ ] **Step 7: Smoke-test the CLI**

Run: `uv run agent-core wake-stats --help`
Expected: shows the help text with the four flags.

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/agent_core/wake_stats.py packages/core/src/agent_core/cli.py packages/core/tests/test_wake_stats.py
git commit -m "feat(core): wake-stats analyzer + CLI subcommand (#70)

agent-core wake-stats joins the relay's wake-audit JSONL (issue #70
Phase 4) with the bus's mcp-audit JSONL to classify each inline wake's
outcome:
- replied: next call was reply(in_reply_to=X) for an inlined X
- handled: next call was handle(envelope_id=X) for an inlined X
- engaged-with-fetch: next call was peek(X) (followed by reply/handle)
- side-action: next call was unrelated (cross-channel send, etc.)
- ignored: no call within the window

Output is JSON with rolling rates suitable for the 30% rollout-gate
decision (no-engagement rate = ignored + side-action / total).

Wakes that fell back to bare-wake (empty_queue, render_error, circuit
breaker tripped) are excluded — only inline wakes count for the gate."
```

---

## Task 9: RelayConfig + load_config resolver

**Files:**
- Modify: `packages/agent-core-channel/src/agent_core_channel/config.py`
- Test: `packages/agent-core-channel/tests/test_config.py`

This replaces the stub from Phase 4 with the full layered resolver.

- [ ] **Step 1: Write the failing tests**

```python
# packages/agent-core-channel/tests/test_config.py
"""Issue #70: layered RelayConfig resolution — CLI > env > YAML > defaults."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_core_channel.config import RelayConfig, load_config


def test_defaults_when_nothing_set(tmp_path: Path) -> None:
    cli_args = SimpleNamespace(
        inline_mode=None, inline_max_envelopes=None,
        inline_max_bytes=None, inline_per_envelope_bytes=None,
    )
    config = load_config(
        agent="pepper", config_path=tmp_path / "missing.yaml",
        cli_args=cli_args, env={},
    )
    assert config == RelayConfig()


def test_yaml_overrides_defaults(tmp_path: Path) -> None:
    yaml_path = tmp_path / "agent_core.yaml"
    yaml_path.write_text(
        """
endpoints:
  - type: builtin.claude_code_mcp
    name: pepper
    params:
      mount: /mcp/pepper
      channel_relay:
        inline_mode: preview
        max_envelopes: 10
        max_bytes: 16384
        per_envelope_bytes: 8192
"""
    )
    cli_args = SimpleNamespace(
        inline_mode=None, inline_max_envelopes=None,
        inline_max_bytes=None, inline_per_envelope_bytes=None,
    )
    config = load_config(
        agent="pepper", config_path=yaml_path, cli_args=cli_args, env={},
    )
    assert config.inline_mode == "preview"
    assert config.max_envelopes == 10
    assert config.max_bytes == 16384
    assert config.per_envelope_bytes == 8192


def test_env_overrides_yaml(tmp_path: Path) -> None:
    yaml_path = tmp_path / "agent_core.yaml"
    yaml_path.write_text(
        """
endpoints:
  - type: builtin.claude_code_mcp
    name: pepper
    params:
      channel_relay:
        inline_mode: preview
"""
    )
    cli_args = SimpleNamespace(
        inline_mode=None, inline_max_envelopes=None,
        inline_max_bytes=None, inline_per_envelope_bytes=None,
    )
    config = load_config(
        agent="pepper", config_path=yaml_path, cli_args=cli_args,
        env={"AGENT_CORE_CHANNEL_INLINE_MODE": "disabled"},
    )
    assert config.inline_mode == "disabled"


def test_cli_overrides_env(tmp_path: Path) -> None:
    cli_args = SimpleNamespace(
        inline_mode="full", inline_max_envelopes=None,
        inline_max_bytes=None, inline_per_envelope_bytes=None,
    )
    config = load_config(
        agent="pepper", config_path=tmp_path / "missing.yaml",
        cli_args=cli_args,
        env={"AGENT_CORE_CHANNEL_INLINE_MODE": "preview"},
    )
    assert config.inline_mode == "full"


def test_yaml_without_channel_relay_block_uses_defaults(tmp_path: Path) -> None:
    yaml_path = tmp_path / "agent_core.yaml"
    yaml_path.write_text(
        """
endpoints:
  - type: builtin.claude_code_mcp
    name: pepper
    params:
      mount: /mcp/pepper
"""
    )
    cli_args = SimpleNamespace(
        inline_mode=None, inline_max_envelopes=None,
        inline_max_bytes=None, inline_per_envelope_bytes=None,
    )
    config = load_config(
        agent="pepper", config_path=yaml_path, cli_args=cli_args, env={},
    )
    assert config == RelayConfig()


def test_yaml_for_different_agent_does_not_match(tmp_path: Path) -> None:
    yaml_path = tmp_path / "agent_core.yaml"
    yaml_path.write_text(
        """
endpoints:
  - type: builtin.claude_code_mcp
    name: testbot
    params:
      channel_relay:
        inline_mode: disabled
"""
    )
    cli_args = SimpleNamespace(
        inline_mode=None, inline_max_envelopes=None,
        inline_max_bytes=None, inline_per_envelope_bytes=None,
    )
    config = load_config(
        agent="pepper", config_path=yaml_path, cli_args=cli_args, env={},
    )
    assert config == RelayConfig()  # testbot's config doesn't apply
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest packages/agent-core-channel/tests/test_config.py -v`
Expected: ImportError on `load_config`.

- [ ] **Step 3: Implement the resolver**

Replace the contents of `packages/agent-core-channel/src/agent_core_channel/config.py`:

```python
"""Issue #70: relay configuration with layered precedence.

Order of precedence (highest first):
    1. CLI flag (--inline-mode, etc.)
    2. Env var (AGENT_CORE_CHANNEL_INLINE_MODE, etc.)
    3. YAML config (agent_core.yaml endpoints[name=<agent>].params.channel_relay)
    4. Hardcoded defaults
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import yaml


@dataclass(frozen=True)
class RelayConfig:
    inline_mode: Literal["full", "preview", "disabled"] = "full"
    max_envelopes: int = 5
    max_bytes: int = 8192
    per_envelope_bytes: int = 4096


_ENV_PREFIX = "AGENT_CORE_CHANNEL_"
_VALID_MODES = ("full", "preview", "disabled")


def _coerce_mode(value: Any) -> Literal["full", "preview", "disabled"] | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in _VALID_MODES:
        return s  # type: ignore[return-value]
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _yaml_layer(path: Path, agent: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    endpoints = data.get("endpoints", []) if isinstance(data, dict) else []
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        if ep.get("name") != agent:
            continue
        params = ep.get("params", {}) or {}
        block = params.get("channel_relay") if isinstance(params, dict) else None
        if isinstance(block, dict):
            return block
    return {}


def _env_layer(env: Mapping[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if (v := env.get(f"{_ENV_PREFIX}INLINE_MODE")) is not None:
        out["inline_mode"] = v
    if (v := env.get(f"{_ENV_PREFIX}INLINE_MAX_ENVELOPES")) is not None:
        out["max_envelopes"] = v
    if (v := env.get(f"{_ENV_PREFIX}INLINE_MAX_BYTES")) is not None:
        out["max_bytes"] = v
    if (v := env.get(f"{_ENV_PREFIX}INLINE_PER_ENVELOPE_BYTES")) is not None:
        out["per_envelope_bytes"] = v
    return out


def _cli_layer(args: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if getattr(args, "inline_mode", None) is not None:
        out["inline_mode"] = args.inline_mode
    if getattr(args, "inline_max_envelopes", None) is not None:
        out["max_envelopes"] = args.inline_max_envelopes
    if getattr(args, "inline_max_bytes", None) is not None:
        out["max_bytes"] = args.inline_max_bytes
    if getattr(args, "inline_per_envelope_bytes", None) is not None:
        out["per_envelope_bytes"] = args.inline_per_envelope_bytes
    return out


def load_config(
    *,
    agent: str,
    config_path: Path,
    cli_args: Any,
    env: Mapping[str, str],
) -> RelayConfig:
    """Resolve RelayConfig with precedence: CLI > env > YAML > defaults."""
    config = RelayConfig()

    layers = [_yaml_layer(config_path, agent), _env_layer(env), _cli_layer(cli_args)]

    for layer in layers:
        if (mode := _coerce_mode(layer.get("inline_mode"))) is not None:
            config = replace(config, inline_mode=mode)
        if (n := _coerce_int(layer.get("max_envelopes"))) is not None:
            config = replace(config, max_envelopes=n)
        if (n := _coerce_int(layer.get("max_bytes"))) is not None:
            config = replace(config, max_bytes=n)
        if (n := _coerce_int(layer.get("per_envelope_bytes"))) is not None:
            config = replace(config, per_envelope_bytes=n)

    return config
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run pytest packages/agent-core-channel/tests/test_config.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Verify the existing inline tests still pass**

Run: `uv run pytest packages/agent-core-channel/tests -v`
Expected: all PASS.

- [ ] **Step 6: Run lint + types**

Run: `uv run ruff check packages/agent-core-channel/src/agent_core_channel/config.py packages/agent-core-channel/tests/test_config.py`
Run: `uv run mypy packages/agent-core-channel/src/agent_core_channel/config.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/config.py packages/agent-core-channel/tests/test_config.py
git commit -m "feat(channel): layered RelayConfig resolver (#70)

Resolves RelayConfig with precedence CLI > env > YAML > defaults
(issue #70 Phase 6). YAML reads from agent_core.yaml's
endpoints[name=<agent>].params.channel_relay block; absent block
means defaults. Env vars use AGENT_CORE_CHANNEL_* prefix. CLI args
come from the relay's argparse Namespace.

Replaces the Phase 4 stub. Existing wire-up tests continue to use
RelayConfig() defaults; the CLI/env paths are exercised by the new
config tests."
```

---

## Task 10: Wire CLI flags + env vars + YAML path through `__main__.py`

**Files:**
- Modify: `packages/agent-core-channel/src/agent_core_channel/__main__.py`
- Modify: `packages/agent-core-channel/src/agent_core_channel/stdio_server.py` — `run_relay` signature accepts a `RelayConfig` parameter

- [ ] **Step 1: Update `run_relay` to accept config**

Modify `packages/agent-core-channel/src/agent_core_channel/stdio_server.py`. Change `run_relay`'s signature and body so it takes `config: RelayConfig` instead of constructing the default internally:

Replace this block in `run_relay`:

```python
    config = RelayConfig()  # Phase 6 will wire CLI/env/YAML overrides.
```

with:

```python
    # config now arrives as a parameter from __main__.py
```

Update the function signature:

```python
async def run_relay(
    agent: str,
    daemon_url: str,
    *,
    config: "RelayConfig | None" = None,
) -> None:
    """..."""
    from agent_core_channel.bus_client import BusClient
    from agent_core_channel.config import RelayConfig

    if config is None:
        config = RelayConfig()
    # ...rest unchanged...
```

(Keep the `config = RelayConfig()` import and assignment-on-None as a safety default for tests that call `run_relay` without args.)

- [ ] **Step 2: Update `__main__.py` to add flags and resolve config**

Replace `packages/agent-core-channel/src/agent_core_channel/__main__.py` entirely:

```python
"""Typer CLI entry point for agent-core-channel.

Parses --agent, --daemon-url, and the inline-content config flags
(issue #70), resolves layered RelayConfig (CLI > env > YAML > defaults),
then hands off to run_relay().
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

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
    config_path: Path = typer.Option(
        Path.home() / ".agent-core" / "agent_core.yaml",
        "--config-path",
        help="Path to agent_core.yaml (for the channel_relay block).",
    ),
    inline_mode: str | None = typer.Option(
        None, "--inline-mode",
        help="Override channel_relay.inline_mode (full|preview|disabled).",
    ),
    inline_max_envelopes: int | None = typer.Option(
        None, "--inline-max-envelopes",
        help="Override channel_relay.max_envelopes.",
    ),
    inline_max_bytes: int | None = typer.Option(
        None, "--inline-max-bytes",
        help="Override channel_relay.max_bytes.",
    ),
    inline_per_envelope_bytes: int | None = typer.Option(
        None, "--inline-per-envelope-bytes",
        help="Override channel_relay.per_envelope_bytes.",
    ),
) -> None:
    """Run the agent-core stdio channel relay."""
    from agent_core_channel.config import load_config
    from agent_core_channel.stdio_server import run_relay

    cli_args = SimpleNamespace(
        inline_mode=inline_mode,
        inline_max_envelopes=inline_max_envelopes,
        inline_max_bytes=inline_max_bytes,
        inline_per_envelope_bytes=inline_per_envelope_bytes,
    )
    config = load_config(
        agent=agent, config_path=config_path,
        cli_args=cli_args, env=os.environ,
    )
    anyio.run(run_relay, agent, daemon_url, config=config)


if __name__ == "__main__":
    app()
```

- [ ] **Step 3: Run all relay tests to confirm nothing regressed**

Run: `uv run pytest packages/agent-core-channel/tests -v`
Expected: all PASS.

- [ ] **Step 4: Smoke-test the CLI**

Run: `uv run agent-core-channel --help`
Expected: shows all flags including `--inline-mode`, `--inline-max-envelopes`, etc.

- [ ] **Step 5: Run lint + types**

Run: `uv run ruff check packages/agent-core-channel/src packages/agent-core-channel/tests`
Run: `uv run mypy packages/agent-core-channel/src`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/__main__.py packages/agent-core-channel/src/agent_core_channel/stdio_server.py
git commit -m "feat(channel): wire CLI flags + env vars to relay startup (#70)

__main__.py adds --config-path, --inline-mode, --inline-max-envelopes,
--inline-max-bytes, --inline-per-envelope-bytes flags. Reads env vars
with AGENT_CORE_CHANNEL_ prefix as fallbacks. Calls load_config to
resolve the layered precedence (CLI > env > YAML > defaults), then
passes the resolved RelayConfig to run_relay.

Existing tests pass — run_relay accepts config as a kwarg with a
sensible default for tests that don't care."
```

---

## Task 11: Final verification + changelog + push + PR

**Files:**
- Create: `packages/core/changelog.d/70.added.md`

- [ ] **Step 1: Add changelog entry**

Create `packages/core/changelog.d/70.added.md`:

```markdown
Inline-content wake notifications via relay-side prefetch (issue #70).
The `agent-core-channel` relay now calls `consume(auto_ack=False)` on
every wake, applies per-kind rendering with HTML-safe encoding, and
emits a richer `notifications/claude/channel` notification carrying
the inbound envelope content directly. Drops the per-Discord-round-
trip floor from 2 tool calls to 1.

New bus tool: `peek(envelope_id)` returns one specific envelope from
the pickup queue without acking — used to hydrate truncated previews
into full payload, and useful for power-use manual triage.

New CLI command: `agent-core wake-stats <agent>` joins the relay's
wake-audit JSONL with the bus's mcp-audit JSONL to compute per-wake
outcomes (replied / handled / engaged-with-fetch / side-action /
ignored). The 30% no-engagement rate is the rollout-gate threshold —
above it, switch the relay to `--inline-mode=preview` instead of
`full` (Alt A from the design discussion).

Configuration via layered precedence (CLI > env > YAML > defaults).
YAML schema: `endpoints[name=<agent>].params.channel_relay` with
keys `inline_mode`, `max_envelopes`, `max_bytes`, `per_envelope_bytes`.
Env vars: `AGENT_CORE_CHANNEL_*`. Defaults: full mode, 5 envelopes,
8KB total, 4KB per-envelope.

Backward compatible: existing endpoints without a `channel_relay`
YAML block use defaults. Bus protocol unchanged — all new behavior
lives in the relay (Alt B / harness-side prefetch).
```

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest packages/core/tests packages/agent-core-discord/tests packages/agent-core-channel/tests -q`
Expected: all PASS, no failures (count should be ~870+ tests, up from 821 pre-#70).

- [ ] **Step 3: Run ruff + mypy across all touched files**

Run: `uv run ruff check packages/core/src packages/agent-core-channel/src packages/core/tests packages/agent-core-channel/tests`
Run: `uv run mypy packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/src/agent_core/wake_stats.py packages/agent-core-channel/src`
Expected: both clean.

- [ ] **Step 4: Commit changelog**

```bash
git add packages/core/changelog.d/70.added.md
git commit -m "docs(changelog): towncrier entry for #70"
```

- [ ] **Step 5: Push branch**

Run: `git push -u origin feat/issue-70-inline-wake`
Expected: branch pushed to remote.

- [ ] **Step 6: Open PR with phased structure**

Run:

```bash
gh pr create --title "feat: inline-content wake via relay-side prefetch (#70)" --body "$(cat <<'BODY'
## Summary

Closes #70. Drops per-Discord-round-trip floor from 2 tool calls to 1 by having the \`agent-core-channel\` relay call \`consume()\` on the agent's behalf at wake time, render envelope content with safe encoding, and emit a richer \`notifications/claude/channel\` notification.

**Bus protocol unchanged** (Alt B / harness-side prefetch). All new behavior lives in the relay.

## Phases (each commit is a reviewable mini-PR)

- **Phase 1**: peek(envelope_id) MCP tool on ClaudeCodeMCPEndpoint
- **Phase 2**: body encoding helper (HTML escape)
- **Phase 3**: per-kind envelope renderers (dispatch dict + table)
- **Phase 4**: circuit breaker + truncation marker + redelivery tracker
- **Phase 5**: BusClient (persistent MCP client to the bus)
- **Phase 6**: wake-audit JSONL writer
- **Phase 7**: wire it up — process_wake_event in the SSE pump
- **Phase 8**: wake-stats analyzer + CLI subcommand
- **Phase 9**: layered RelayConfig resolver
- **Phase 10**: __main__.py CLI flags + env-var fallback

## Cumulative metric (the headline)

| Stage | calls/round-trip | tokens (~500K cached) |
|---|---|---|
| Pre-#54 | 5 | ~250K |
| Post-#54 | 3 | ~150K |
| Post-#67 | 2 | ~100K |
| **Post-#70** | **1** (single inbound + reply) or 2 (cross-channel send) | ~50K |

## Rollout gate

After ~1 week, run \`agent-core wake-stats pepper\`. If no-engagement rate (\`ignored + side-action / total\`) is above 30%, switch Pepper's relay to \`--inline-mode=preview\` (Alt A: 200-char preview + peek). One CLI flag flip; no code change.

## Verification (post-merge)

- Restart daemon. Pepper relaunches her session.
- Single Discord round-trip: 1 tool call (verified via mcp_audit).
- Multi-inbound batch: all envelopes inlined in one wake; circuit-breaker fallback works for >5 envelopes or >8KB total.
- Encoding safety: feed \`<script>alert(1)</script>\` and verify Pepper's parse is unaffected.
- Cross-restart: envelopes persist; next wake includes them inlined.

## Out of scope

- Per-kind plugin rendering hooks (separable future ticket — defaults cover all kinds today).
- Auto-ack-on-push (explicitly rejected — risks silent drops on session crash).
- dismiss(envelope_id) tool (handle() covers it).

## Spec & plan

- Design: \`docs/superpowers/specs/2026-05-09-issue-70-inline-wake-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-09-issue-70-inline-wake.md\`
BODY
)"
```

Expected: PR URL printed.

- [ ] **Step 7: Final task — confirm PR URL**

Print the PR URL for the user to review.

---

## Self-review (planner only — do not include in implementation)

**Spec coverage:** every section of the design doc maps to one or more tasks above. peek (Phase 1 → Task 1), rendering (Phase 2 → Tasks 2-4), bus client (Phase 3 → Task 5), wake audit + wire-up (Phase 4 → Tasks 6-7), analyzer (Phase 5 → Task 8), config (Phase 6 → Tasks 9-10). Final verification + changelog + PR is Task 11. Acceptance criteria from the spec are covered by tests in each task.

**Placeholder scan:** every step has concrete code, exact file paths, exact commands, expected outputs. No "TBD", no "implement later", no "similar to Task N" without showing code.

**Type consistency:** `RelayConfig`, `InlineAll`, `FallbackToBare`, `WakeAuditWriter`, `BusClient`, `RedeliveryTracker`, `WakeOutcome` — names are stable across all tasks that reference them. `apply_circuit_breaker` signature (kwargs `max_envelopes`, `max_total_bytes`, `max_per_envelope_bytes`, `mode`) is consistent between Tasks 4 and 7. The `process_wake_event` signature (Task 7) matches what __main__.py wires up (Task 10).

**Scope check:** focused on issue #70 only. Does not touch unrelated subsystems. Bus protocol untouched (peek is an additive tool that fits the existing `consume`/`reply`/`handle` pattern).

**Ambiguity check:** the `[BATCH N/M]` token in the spec became `batch='N/M'` attribute in implementation — the test was updated in Task 3 to match. The truncation marker format is exactly the spec's: `[content elided; envelope_id='X'; call peek('X') for full payload]`. The wake-stats classification names match between Task 8 tests and the spec table.
