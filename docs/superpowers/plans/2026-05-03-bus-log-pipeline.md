# Bus Log Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single bus-owned daily JSONL log + read-time projection library so each agent's reflection job (today: Pepper's 3 AM cron) can summarize its own day from one shared, fidelity-preserving source — and so agents themselves can introspect their day in-session via MCP.

**Architecture:** One `BusHook` writes every published envelope to `~/.agent-core/bus/raw/<date>.jsonl` in bus-native shape. A new `agent_core.bus_log` module exposes `iter_envelopes` (raw) and `iter_for_agent` (filter to one agent, project to Tool 3 rows via registered projectors). Three thin call surfaces wrap the library: `agent-core bus-log show` CLI for cron/operators, `show_my_day` MCP tool on `ClaudeCodeMCPEndpoint` for agent self-introspection (auto-scoped by `self.name`), and direct library import for the reflection job.

**Tech Stack:** Python 3.12, Pydantic 2 (envelope serialization), Typer (CLI), pluggy (projector entry-point registration), pytest + pytest-asyncio, MCP server (existing FastMCP integration).

**Spec:** [`docs/superpowers/specs/2026-05-03-bus-log-pipeline-design.md`](../specs/2026-05-03-bus-log-pipeline-design.md)
**Ticket:** [Cutover #04](../../requirements/pepper-cutover-04-daily-jsonl-pipeline.md)

---

## File structure

**New files:**
- `packages/core/src/agent_core/bus_log/__init__.py` — public re-exports.
- `packages/core/src/agent_core/bus_log/projectors.py` — `Projector` Protocol, registry, default projectors, fallback.
- `packages/core/src/agent_core/bus_log/reader.py` — `iter_envelopes`, `iter_for_agent`.
- `packages/core/src/agent_core/bus_log/writer.py` — `default_log_root`, `daily_path`, `append_envelope_jsonl`.
- `packages/core/src/agent_core/bus_log/cli.py` — Typer subapp for `agent-core bus-log`.
- `packages/core/src/agent_core/bus_hooks/daily_raw_jsonl.py` — the `BusHook`.
- `packages/core/tests/test_bus_log_projectors_registry.py`
- `packages/core/tests/test_bus_log_default_projectors.py`
- `packages/core/tests/test_bus_log_reader.py`
- `packages/core/tests/test_bus_log_iter_for_agent.py`
- `packages/core/tests/test_daily_raw_jsonl_hook.py`
- `packages/core/tests/test_bus_log_cli.py`
- `packages/core/tests/test_show_my_day_mcp_tool.py`
- `docs/cutover/test-playbooks/04-daily-jsonl-pipeline.md`

**Modified files:**
- `packages/core/src/agent_core/plugins/specs.py` — add `register_bus_log_projectors` hookspec.
- `packages/core/src/agent_core/plugins/manager.py` — add `get_bus_log_projectors`.
- `packages/core/src/agent_core/plugins/builtin_aliases.py` — `_BUS_HOOK_TYPES`, `register_bus_hook_types`, `register_bus_log_projectors`.
- `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — `bus_log_root` constructor param, `show_my_day` MCP tool.
- `packages/core/src/agent_core/cli.py` — register `bus_log_app` as `agent-core bus-log`.
- `docs/examples/pepper-agent-core.yaml` — add `bus_hooks.pre_publish` block.
- `packages/core/tests/test_pepper_example_yaml.py` — add tripwire for the new yaml block.
- `docs/cutover/test-playbooks/README.md` — add #04 row.
- `docs/requirements/pepper-cutover-04-daily-jsonl-pipeline.md` — Status → Implementation complete.
- `docs/requirements/pepper-cutover-agent-playbook.md` — flip #04 row.
- `docs/requirements/pepper-pre-cutover-must-haves.md` — flip #04 row.

---

### Task 1: Projector Protocol and registry

**Files:**
- Create: `packages/core/src/agent_core/bus_log/__init__.py`
- Create: `packages/core/src/agent_core/bus_log/projectors.py`
- Test: `packages/core/tests/test_bus_log_projectors_registry.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_bus_log_projectors_registry.py`:

```python
"""Projector registry: lookup, override, and isolation between tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
from agent_core.bus_log.projectors import (
    Projector,
    fallback_projector,
    get_projector,
    register_projector,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test starts with an empty registry; restore after."""
    reset_registry()
    yield
    reset_registry()


def _text_env(eid: str = "e1") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="src",
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime.now(UTC),
    )


def _event_env(eid: str, *, event_type: str) -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="src",
        to="agent",
        kind="Event",
        payload=EventPayload(type=event_type, data={}),
        created_at=datetime.now(UTC),
    )


class _Stub:
    def __init__(self, sentinel: dict | None) -> None:
        self.sentinel = sentinel
        self.calls: list[tuple[str, str, str]] = []

    def render(self, envelope, *, perspective, timezone):
        self.calls.append((envelope.id, perspective, timezone))
        return self.sentinel


def test_register_and_lookup_by_event_type():
    p = _Stub({"k": "v"})
    register_projector("MyEvent", p)
    found = get_projector(_event_env("e1", event_type="MyEvent"))
    assert found is p


def test_lookup_by_envelope_kind_when_not_event():
    p = _Stub({"k": "TM"})
    register_projector("TextMessage", p)
    found = get_projector(_text_env())
    assert found is p


def test_event_type_lookup_takes_priority_over_kind():
    by_type = _Stub({"src": "type"})
    by_kind = _Stub({"src": "kind"})
    register_projector("MyEvent", by_type)
    register_projector("Event", by_kind)
    found = get_projector(_event_env("e1", event_type="MyEvent"))
    assert found is by_type


def test_fallback_used_when_no_projector_registered():
    found = get_projector(_event_env("e1", event_type="UnknownEvent"))
    assert found is fallback_projector


def test_register_replaces_existing():
    first = _Stub({"a": 1})
    second = _Stub({"b": 2})
    register_projector("MyEvent", first)
    register_projector("MyEvent", second)
    found = get_projector(_event_env("e1", event_type="MyEvent"))
    assert found is second


def test_protocol_runtime_check():
    assert isinstance(_Stub(None), Projector)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_bus_log_projectors_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.bus_log'`.

- [ ] **Step 3: Implement the registry**

Create `packages/core/src/agent_core/bus_log/__init__.py`:

```python
"""Bus log pipeline — read, filter, project bus envelope JSONL streams.

Public API used by reflection jobs, the agent-core CLI, and the in-session
``show_my_day`` MCP tool. See docs/superpowers/specs/2026-05-03-bus-log-pipeline-design.md.
"""

from __future__ import annotations

from agent_core.bus_log.projectors import (
    Projector,
    fallback_projector,
    get_projector,
    register_projector,
    reset_registry,
)

__all__ = [
    "Projector",
    "fallback_projector",
    "get_projector",
    "register_projector",
    "reset_registry",
]
```

Create `packages/core/src/agent_core/bus_log/projectors.py`:

```python
"""Projector protocol + registry + fallback.

A Projector renders a bus envelope into a Tool 3 summary row, or returns
None to skip the envelope from the projected stream (e.g., heartbeat noise
that should not appear in daily summaries).

Lookup priority:
1. ``payload.type`` for ``Event`` envelopes (e.g., "HandoffReady")
2. ``envelope.kind`` for non-Events (e.g., "TextMessage")
3. fallback_projector — never returns None; renders generic content
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_core.bus.envelope import Envelope, EventPayload


@runtime_checkable
class Projector(Protocol):
    """Render a bus envelope into a Tool 3 row, or skip it."""

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None: ...


_REGISTRY: dict[str, Projector] = {}


def register_projector(key: str, projector: Projector) -> None:
    """Register a projector for an Event payload type or envelope kind.

    Re-registering the same key replaces the prior projector. This is
    intentional: pluggy entry points populate defaults at import time;
    application code may override programmatically (e.g., a test).
    """
    _REGISTRY[key] = projector


def reset_registry() -> None:
    """Clear all registrations. For tests + bootstrap re-init."""
    _REGISTRY.clear()


def get_projector(envelope: Envelope) -> Projector:
    """Resolve the projector for an envelope per the lookup priority.

    Returns the fallback projector if no specific projector is registered;
    never returns None — every envelope has a projector that will render
    *something*, possibly via the generic fallback shape.
    """
    if isinstance(envelope.payload, EventPayload):
        by_type = _REGISTRY.get(envelope.payload.type)
        if by_type is not None:
            return by_type
    by_kind = _REGISTRY.get(envelope.kind)
    if by_kind is not None:
        return by_kind
    return fallback_projector


class _FallbackProjector:
    """Last-resort projector — renders any envelope into a generic row.

    Never returns None: keeps unknown envelope kinds visible in summaries
    instead of silently dropping them. Concrete projectors should override
    by registering against a specific kind / type id.
    """

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None:
        # Implemented in Task 2 once we have rendering helpers; for now
        # return a placeholder dict so the registry tests pass.
        return {"placeholder": True}


fallback_projector: Projector = _FallbackProjector()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_bus_log_projectors_registry.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/bus_log/ packages/core/tests/test_bus_log_projectors_registry.py
git commit -m "feat(bus_log): projector protocol + registry skeleton"
```

---

### Task 2: TextMessage and Fallback projectors (real implementations)

**Files:**
- Modify: `packages/core/src/agent_core/bus_log/projectors.py`
- Test: `packages/core/tests/test_bus_log_default_projectors.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_bus_log_default_projectors.py`:

```python
"""Default projectors: TextMessage + Fallback."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
from agent_core.bus_log.projectors import (
    TextMessageProjector,
    fallback_projector,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    reset_registry()
    yield
    reset_registry()


def _ts() -> datetime:
    # Fixed UTC instant: 2026-05-03 17:42:13 UTC == 13:42:13 EDT (US/Eastern is UTC-4 in May).
    return datetime(2026, 5, 3, 17, 42, 13, tzinfo=UTC)


def _text_env(*, frm: str = "discord", to: str = "pepper", text: str = "hi", metadata=None) -> Envelope:
    return Envelope(
        id="e1",
        correlation_id="c1",
        from_=frm,
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        urgency="yellow",
        metadata=metadata or {},
        created_at=_ts(),
    )


class TestTextMessageProjector:
    def test_dir_in_when_perspective_is_recipient(self):
        row = TextMessageProjector().render(_text_env(), perspective="pepper", timezone="US/Eastern")
        assert row is not None
        assert row["dir"] == "in"

    def test_dir_out_when_perspective_is_sender(self):
        row = TextMessageProjector().render(
            _text_env(frm="pepper", to="discord"),
            perspective="pepper",
            timezone="US/Eastern",
        )
        assert row is not None
        assert row["dir"] == "out"

    def test_dir_self_when_perspective_is_both(self):
        row = TextMessageProjector().render(
            _text_env(frm="pepper", to="pepper"),
            perspective="pepper",
            timezone="US/Eastern",
        )
        assert row is not None
        assert row["dir"] == "self"

    def test_ts_renders_in_requested_timezone(self):
        row = TextMessageProjector().render(_text_env(), perspective="pepper", timezone="US/Eastern")
        # 17:42:13 UTC -> 13:42:13 -04:00 in May (EDT).
        assert row["ts"] == "2026-05-03T13:42:13-04:00"

    def test_ts_renders_in_utc_when_requested(self):
        row = TextMessageProjector().render(_text_env(), perspective="pepper", timezone="UTC")
        assert row["ts"] == "2026-05-03T17:42:13+00:00"

    def test_sender_uses_metadata_display_name_when_present(self):
        row = TextMessageProjector().render(
            _text_env(metadata={"discord_user_display_name": "Jeff"}),
            perspective="pepper",
            timezone="US/Eastern",
        )
        assert row["sender"] == "Jeff"

    def test_sender_falls_back_to_envelope_from(self):
        row = TextMessageProjector().render(_text_env(), perspective="pepper", timezone="US/Eastern")
        assert row["sender"] == "discord"

    def test_src_is_envelope_from(self):
        row = TextMessageProjector().render(_text_env(), perspective="pepper", timezone="US/Eastern")
        assert row["src"] == "discord"

    def test_cid_is_correlation_id(self):
        row = TextMessageProjector().render(_text_env(), perspective="pepper", timezone="US/Eastern")
        assert row["cid"] == "c1"

    def test_content_is_payload_text(self):
        row = TextMessageProjector().render(
            _text_env(text="Did you see the report?"),
            perspective="pepper",
            timezone="US/Eastern",
        )
        assert row["content"] == "Did you see the report?"


class TestFallbackProjector:
    def test_renders_unknown_event_with_event_prefix(self):
        env = Envelope(
            id="x1",
            correlation_id="cx",
            from_="some-source",
            to="pepper",
            kind="Event",
            payload=EventPayload(type="UnknownThing", data={"k": "v"}),
            created_at=_ts(),
        )
        row = fallback_projector.render(env, perspective="pepper", timezone="US/Eastern")
        assert row is not None
        assert row["content"].startswith("event:UnknownThing")
        assert "k" in row["content"]
        assert row["dir"] == "in"
        assert row["src"] == "some-source"
        assert row["cid"] == "cx"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_bus_log_default_projectors.py -v`
Expected: FAIL — `ImportError: cannot import name 'TextMessageProjector' from 'agent_core.bus_log.projectors'`.

- [ ] **Step 3: Implement TextMessage and replace placeholder fallback**

Edit `packages/core/src/agent_core/bus_log/projectors.py`. Add these imports and module-level helpers near the top (after the existing imports):

```python
import json
from datetime import UTC
from zoneinfo import ZoneInfo

from agent_core.bus.envelope import TextMessagePayload
```

Add this helper above the `Projector` Protocol:

```python
def _render_ts(envelope: Envelope, timezone: str) -> str:
    """Render envelope.created_at in the requested IANA timezone."""
    src_dt = envelope.created_at
    if src_dt.tzinfo is None:
        src_dt = src_dt.replace(tzinfo=UTC)
    return src_dt.astimezone(ZoneInfo(timezone)).isoformat()


def _render_dir(envelope: Envelope, perspective: str) -> str:
    is_to = envelope.to == perspective
    is_from = envelope.from_ == perspective
    if is_to and is_from:
        return "self"
    if is_to:
        return "in"
    return "out"
```

Add a real `TextMessageProjector` class (place it above the existing `_FallbackProjector`):

```python
class TextMessageProjector:
    """Default projector for ``kind="TextMessage"`` envelopes.

    Maps Discord/relay/scheduler text traffic into the Tool 3 row shape.
    Sender display name is taken from envelope metadata when present
    (e.g., ``discord_user_display_name`` set by DiscordEndpoint).
    """

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None:
        if not isinstance(envelope.payload, TextMessagePayload):
            return None
        sender = envelope.metadata.get("discord_user_display_name") or envelope.from_
        return {
            "ts": _render_ts(envelope, timezone),
            "dir": _render_dir(envelope, perspective),
            "src": envelope.from_,
            "cid": envelope.correlation_id,
            "sender": sender,
            "content": envelope.payload.text,
        }
```

Replace the existing `_FallbackProjector.render` body with the real fallback:

```python
class _FallbackProjector:
    """Last-resort projector — renders any envelope into a generic row.

    Never returns None: keeps unknown envelope kinds visible in summaries
    instead of silently dropping them. Specific projectors should override
    by registering against a specific kind / type id.
    """

    _MAX_DATA_CHARS = 200

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None:
        if isinstance(envelope.payload, EventPayload):
            data_str = json.dumps(envelope.payload.data, sort_keys=True, default=str)
            if len(data_str) > self._MAX_DATA_CHARS:
                data_str = data_str[: self._MAX_DATA_CHARS - 1] + "…"
            content = f"event:{envelope.payload.type} data={data_str}"
        else:
            content = f"{envelope.kind}:{envelope.id}"
        return {
            "ts": _render_ts(envelope, timezone),
            "dir": _render_dir(envelope, perspective),
            "src": envelope.from_,
            "cid": envelope.correlation_id,
            "sender": envelope.from_,
            "content": content,
        }
```

Add `TextMessageProjector` to the `__init__.py` re-exports (note: `reset_registry` is intentionally not re-exported here — tests import it from `agent_core.bus_log.projectors` directly because it's a test-only helper):

```python
from agent_core.bus_log.projectors import (
    Projector,
    TextMessageProjector,
    fallback_projector,
    get_projector,
    register_projector,
)

__all__ = [
    "Projector",
    "TextMessageProjector",
    "fallback_projector",
    "get_projector",
    "register_projector",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_bus_log_default_projectors.py packages/core/tests/test_bus_log_projectors_registry.py -v`
Expected: all green (16 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/bus_log/ packages/core/tests/test_bus_log_default_projectors.py
git commit -m "feat(bus_log): TextMessage projector + real fallback projector"
```

---

### Task 3: iter_envelopes — raw read

**Files:**
- Create: `packages/core/src/agent_core/bus_log/reader.py`
- Modify: `packages/core/src/agent_core/bus_log/__init__.py`
- Test: `packages/core/tests/test_bus_log_reader.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_bus_log_reader.py`:

```python
"""Reader: iter_envelopes — raw round-trip + time bounds + tolerant parsing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
from agent_core.bus_log.reader import iter_envelopes


def _text_env(eid: str, *, created: datetime, frm: str = "src", to: str = "pepper") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_=frm,
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text=f"text-{eid}"),
        created_at=created,
    )


def _event_env(eid: str, *, created: datetime, event_type: str, data: dict) -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="src",
        to="pepper",
        kind="Event",
        payload=EventPayload(type=event_type, data=data),
        created_at=created,
    )


def _write_jsonl(path: Path, envelopes: list[Envelope]) -> None:
    lines = [env.model_dump_json(by_alias=True) for env in envelopes]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_iter_envelopes_round_trips_text_and_event(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("e1", created=base),
        _event_env("e2", created=base + timedelta(seconds=1), event_type="HandoffReady",
                   data={"job_id": "j-1", "handoff_path": "/x/handoff.md"}),
    ]
    path = tmp_path / "2026-05-03.jsonl"
    _write_jsonl(path, envs)
    parsed = list(iter_envelopes(path))
    assert len(parsed) == 2
    assert parsed[0].id == "e1"
    assert parsed[0].kind == "TextMessage"
    assert parsed[0].payload.text == "text-e1"
    assert parsed[1].id == "e2"
    assert parsed[1].kind == "Event"
    assert parsed[1].payload.type == "HandoffReady"
    assert parsed[1].payload.data["job_id"] == "j-1"


def test_iter_envelopes_returns_empty_for_missing_file(tmp_path: Path):
    parsed = list(iter_envelopes(tmp_path / "nonexistent.jsonl"))
    assert parsed == []


def test_iter_envelopes_skips_blank_and_malformed_lines(tmp_path: Path, caplog):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    good = _text_env("e1", created=base)
    path = tmp_path / "day.jsonl"
    path.write_text(
        good.model_dump_json(by_alias=True) + "\n"
        + "\n"  # blank line
        + "{not valid json\n"
        + "\n",
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        parsed = list(iter_envelopes(path))
    assert len(parsed) == 1
    assert parsed[0].id == "e1"
    # Malformed line was reported (operator visibility), not silently ignored.
    assert any("malformed" in r.message.lower() for r in caplog.records)


def test_iter_envelopes_filters_by_time_range(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("a", created=base),
        _text_env("b", created=base + timedelta(seconds=10)),
        _text_env("c", created=base + timedelta(seconds=20)),
    ]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    parsed = list(iter_envelopes(path, since=base + timedelta(seconds=5), until=base + timedelta(seconds=15)))
    assert [e.id for e in parsed] == ["b"]


def test_iter_envelopes_inclusive_since_exclusive_until(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("a", created=base),
        _text_env("b", created=base + timedelta(seconds=5)),
    ]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    parsed = list(iter_envelopes(path, since=base, until=base + timedelta(seconds=5)))
    # since is inclusive (a included), until is exclusive (b excluded).
    assert [e.id for e in parsed] == ["a"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_bus_log_reader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.bus_log.reader'`.

- [ ] **Step 3: Implement reader.py**

Create `packages/core/src/agent_core/bus_log/reader.py`:

```python
"""Read + filter daily bus log JSONL files.

The on-disk format is one ``Envelope.model_dump_json(by_alias=True)`` per
line. Reads are tolerant of malformed lines (logged + skipped, never
silently ignored) so a single bad line does not poison the day.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from agent_core.bus.envelope import Envelope

log = logging.getLogger(__name__)


def iter_envelopes(
    path: Path,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Iterator[Envelope]:
    """Yield envelopes from a daily JSONL file.

    Missing files yield nothing (a quiet day, not an error). Blank lines
    are skipped silently. Lines that fail Envelope validation (including
    invalid JSON syntax — ``ValidationError`` subclasses ``ValueError``
    so it covers both shapes) are logged at WARNING and skipped — operator
    gets a signal without losing the rest of the day.

    ``since`` is inclusive, ``until`` is exclusive (matches Python
    range/slice conventions). Both bounds and ``envelope.created_at`` are
    coerced to UTC before comparison so naive datetimes from any side
    don't trigger ``TypeError``.
    """
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        log.exception("bus_log: failed to read %s", path)
        return

    def _to_utc(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    since_utc = _to_utc(since) if since is not None else None
    until_utc = _to_utc(until) if until is not None else None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            env = Envelope.model_validate_json(line)
        except ValidationError as exc:
            log.warning("bus_log: malformed envelope at %s:%d (%s)", path, lineno, exc)
            continue
        ts = _to_utc(env.created_at)
        if since_utc is not None and ts < since_utc:
            continue
        if until_utc is not None and ts >= until_utc:
            continue
        yield env
```

Add to `packages/core/src/agent_core/bus_log/__init__.py`:

```python
from agent_core.bus_log.reader import iter_envelopes
```

And add `"iter_envelopes"` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_bus_log_reader.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/bus_log/ packages/core/tests/test_bus_log_reader.py
git commit -m "feat(bus_log): iter_envelopes — raw read with time bounds and malformed-line tolerance"
```

---

### Task 4: iter_for_agent — filter + project

**Files:**
- Modify: `packages/core/src/agent_core/bus_log/reader.py`
- Modify: `packages/core/src/agent_core/bus_log/__init__.py`
- Test: `packages/core/tests/test_bus_log_iter_for_agent.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_bus_log_iter_for_agent.py`:

```python
"""iter_for_agent: filter envelopes by perspective and project to Tool 3 rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus_log.projectors import (
    TextMessageProjector,
    register_projector,
    reset_registry,
)
from agent_core.bus_log.reader import iter_for_agent


@pytest.fixture(autouse=True)
def _registry_with_text_message():
    reset_registry()
    register_projector("TextMessage", TextMessageProjector())
    yield
    reset_registry()


def _text_env(eid: str, *, frm: str, to: str, created: datetime, text: str = "x") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_=frm,
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        created_at=created,
    )


def _write_jsonl(path: Path, envelopes: list[Envelope]) -> None:
    path.write_text(
        "\n".join(env.model_dump_json(by_alias=True) for env in envelopes) + "\n",
        encoding="utf-8",
    )


def test_filter_includes_envelopes_where_agent_is_to(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("a", frm="discord", to="pepper", created=base),
        _text_env("b", frm="discord", to="vale", created=base),
    ]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    rows = list(iter_for_agent(path, agent="pepper"))
    assert len(rows) == 1
    assert rows[0]["cid"] == "c-a"


def test_filter_includes_envelopes_where_agent_is_from(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("a", frm="pepper", to="discord", created=base),  # outbound
        _text_env("b", frm="vale", to="discord", created=base),    # not pepper
    ]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    rows = list(iter_for_agent(path, agent="pepper"))
    assert len(rows) == 1
    assert rows[0]["cid"] == "c-a"
    assert rows[0]["dir"] == "out"


def test_filter_excludes_envelopes_touching_neither(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [_text_env("a", frm="vale", to="discord", created=base)]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    rows = list(iter_for_agent(path, agent="pepper"))
    assert rows == []


def test_projected_default_returns_tool3_rows(tmp_path: Path):
    base = datetime(2026, 5, 3, 17, 42, 13, tzinfo=UTC)
    env = _text_env("e1", frm="discord", to="pepper", created=base, text="hi")
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, [env])
    rows = list(iter_for_agent(path, agent="pepper", timezone="US/Eastern"))
    assert len(rows) == 1
    assert rows[0] == {
        "ts": "2026-05-03T13:42:13-04:00",
        "dir": "in",
        "src": "discord",
        "cid": "c-e1",
        "sender": "discord",
        "content": "hi",
    }


def test_projected_false_yields_envelope_objects(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    env = _text_env("e1", frm="discord", to="pepper", created=base)
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, [env])
    items = list(iter_for_agent(path, agent="pepper", projected=False))
    assert len(items) == 1
    assert isinstance(items[0], Envelope)
    assert items[0].id == "e1"


def test_projector_returning_none_drops_envelope_in_projected_mode(tmp_path: Path):
    """A projector that returns None means 'do not include in summary'.
    Envelope is still in the raw stream — we just skip it during projection."""
    class _SkipAll:
        def render(self, envelope, *, perspective, timezone):
            return None

    register_projector("TextMessage", _SkipAll())

    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    env = _text_env("e1", frm="discord", to="pepper", created=base)
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, [env])
    rows = list(iter_for_agent(path, agent="pepper"))
    assert rows == []
    raw = list(iter_for_agent(path, agent="pepper", projected=False))
    assert len(raw) == 1


def test_iter_for_agent_passes_timezone_through_to_projector(tmp_path: Path):
    base = datetime(2026, 5, 3, 17, 42, 13, tzinfo=UTC)
    env = _text_env("e1", frm="discord", to="pepper", created=base)
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, [env])
    rows_utc = list(iter_for_agent(path, agent="pepper", timezone="UTC"))
    rows_eastern = list(iter_for_agent(path, agent="pepper", timezone="US/Eastern"))
    assert rows_utc[0]["ts"] == "2026-05-03T17:42:13+00:00"
    assert rows_eastern[0]["ts"] == "2026-05-03T13:42:13-04:00"


def test_iter_for_agent_honors_time_range(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("a", frm="discord", to="pepper", created=base),
        _text_env("b", frm="discord", to="pepper", created=base + timedelta(seconds=10)),
    ]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    rows = list(iter_for_agent(path, agent="pepper", since=base + timedelta(seconds=5)))
    assert len(rows) == 1
    assert rows[0]["cid"] == "c-b"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_bus_log_iter_for_agent.py -v`
Expected: FAIL — `ImportError: cannot import name 'iter_for_agent' from 'agent_core.bus_log.reader'`.

- [ ] **Step 3: Implement iter_for_agent**

Append to `packages/core/src/agent_core/bus_log/reader.py`:

```python
from agent_core.bus_log.projectors import get_projector


def iter_for_agent(
    path: Path,
    *,
    agent: str,
    projected: bool = True,
    timezone: str = "US/Eastern",
    since: datetime | None = None,
    until: datetime | None = None,
):
    """Yield envelopes from ``path`` that touch ``agent`` (to or from_).

    With ``projected=True`` (default), yield Tool 3 rows produced by
    registered projectors; envelopes whose projector returns None are
    skipped. With ``projected=False``, yield raw ``Envelope`` instances.

    ``timezone`` is forwarded to projectors for ``ts`` rendering and
    is ignored when ``projected=False``.
    """
    for env in iter_envelopes(path, since=since, until=until):
        if env.to != agent and env.from_ != agent:
            continue
        if not projected:
            yield env
            continue
        projector = get_projector(env)
        row = projector.render(env, perspective=agent, timezone=timezone)
        if row is not None:
            yield row
```

Add to `packages/core/src/agent_core/bus_log/__init__.py`:

```python
from agent_core.bus_log.reader import iter_envelopes, iter_for_agent
```

Update `__all__` to include `"iter_for_agent"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_bus_log_iter_for_agent.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/bus_log/ packages/core/tests/test_bus_log_iter_for_agent.py
git commit -m "feat(bus_log): iter_for_agent — filter + project with timezone passthrough"
```

---

### Task 5: HandoffReady, HandoffFailed, Acknowledgment-skip, scheduler-heartbeat-skip projectors

**Files:**
- Modify: `packages/core/src/agent_core/bus_log/projectors.py`
- Modify: `packages/core/src/agent_core/bus_log/__init__.py`
- Test: extend `packages/core/tests/test_bus_log_default_projectors.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/test_bus_log_default_projectors.py`:

```python
from agent_core.bus.envelope import AcknowledgmentPayload
from agent_core.bus_log.projectors import (
    AcknowledgmentSkipProjector,
    HandoffFailedProjector,
    HandoffReadyProjector,
    SchedulerHeartbeatSkipProjector,
)


class TestHandoffReadyProjector:
    def test_renders_continuity_ready_with_path(self):
        env = Envelope(
            id="h1",
            correlation_id="c-h1",
            from_="handoff-jobs",
            to="pepper",
            kind="Event",
            payload=EventPayload(
                type="HandoffReady",
                data={
                    "job_id": "j-1",
                    "session_id": "s-1",
                    "handoff_path": "/x/handoff.md",
                    "content_sha256": "abc",
                },
            ),
            created_at=_ts(),
        )
        row = HandoffReadyProjector().render(env, perspective="pepper", timezone="US/Eastern")
        assert row is not None
        assert "continuity ready" in row["content"].lower()
        assert "/x/handoff.md" in row["content"]
        assert row["src"] == "handoff-jobs"
        assert row["dir"] == "in"


class TestHandoffFailedProjector:
    def test_renders_continuity_failed_with_error(self):
        env = Envelope(
            id="f1",
            correlation_id="c-f1",
            from_="handoff-jobs",
            to="pepper",
            kind="Event",
            payload=EventPayload(
                type="HandoffFailed",
                data={"job_id": "j-2", "error": "boom"},
            ),
            created_at=_ts(),
        )
        row = HandoffFailedProjector().render(env, perspective="pepper", timezone="US/Eastern")
        assert row is not None
        assert "continuity failed" in row["content"].lower()
        assert "boom" in row["content"]
        assert row["src"] == "handoff-jobs"


class TestAcknowledgmentSkipProjector:
    def test_returns_none(self):
        env = Envelope(
            id="ack-1",
            correlation_id="c-ack",
            from_="discord",
            to="pepper",
            kind="Acknowledgment",
            payload=AcknowledgmentPayload(of="other-id"),
            created_at=_ts(),
        )
        row = AcknowledgmentSkipProjector().render(env, perspective="pepper", timezone="US/Eastern")
        assert row is None


class TestSchedulerHeartbeatSkipProjector:
    def test_skips_when_metadata_marks_heartbeat(self):
        env = _text_env(metadata={"scheduler_job": "heartbeat"})
        row = SchedulerHeartbeatSkipProjector().render(env, perspective="pepper", timezone="US/Eastern")
        assert row is None

    def test_passes_through_non_heartbeat_text_messages(self):
        env = _text_env(metadata={"scheduler_job": "daily-briefing"})
        # Non-heartbeat scheduler jobs are not this projector's concern.
        # It should defer (return a sentinel? or fall through?). Per the
        # spec we want this projector to ONLY skip exact heartbeat jobs;
        # otherwise the TextMessage projector handles it. Implementation:
        # return None means "skip from summary". To delegate, this
        # projector should NOT be registered against TextMessage globally;
        # it's only registered when it would skip. So passing it a
        # non-heartbeat returns the same Tool 3 row a generic TextMessage
        # projector would produce, OR returns None and we register it
        # selectively. We pick: this projector returns None ONLY for
        # heartbeats, else falls through to TextMessageProjector logic.
        row = SchedulerHeartbeatSkipProjector().render(env, perspective="pepper", timezone="US/Eastern")
        assert row is not None
        assert row["content"] == "hi"  # the default _text_env text
```

Implementation note for the test author: there's a coupling decision to lock — see Step 3.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_bus_log_default_projectors.py -v`
Expected: FAIL — `ImportError: cannot import name 'HandoffReadyProjector'`.

- [ ] **Step 3: Implement the four projectors**

The scheduler-heartbeat case is the only one with a coupling decision. We pick: `SchedulerHeartbeatSkipProjector` is registered against `"TextMessage"` (it replaces the plain `TextMessageProjector` when active) and internally delegates to `TextMessageProjector` for non-heartbeat envelopes. This keeps the registry single-key while letting heartbeat filtering live in one place.

Add to `packages/core/src/agent_core/bus_log/projectors.py`:

```python
class HandoffReadyProjector:
    """Projector for ``Event/HandoffReady`` envelopes published by the
    handoff daemon when continuity has been written."""

    def render(self, envelope, *, perspective, timezone):
        if not isinstance(envelope.payload, EventPayload):
            return None
        if envelope.payload.type != "HandoffReady":
            return None
        path = envelope.payload.data.get("handoff_path", "?")
        return {
            "ts": _render_ts(envelope, timezone),
            "dir": _render_dir(envelope, perspective),
            "src": envelope.from_,
            "cid": envelope.correlation_id,
            "sender": envelope.from_,
            "content": f"continuity ready: handoff.md → {path}",
        }


class HandoffFailedProjector:
    """Projector for ``Event/HandoffFailed`` envelopes."""

    def render(self, envelope, *, perspective, timezone):
        if not isinstance(envelope.payload, EventPayload):
            return None
        if envelope.payload.type != "HandoffFailed":
            return None
        err = envelope.payload.data.get("error", "unknown")
        return {
            "ts": _render_ts(envelope, timezone),
            "dir": _render_dir(envelope, perspective),
            "src": envelope.from_,
            "cid": envelope.correlation_id,
            "sender": envelope.from_,
            "content": f"continuity failed: {err}",
        }


class AcknowledgmentSkipProjector:
    """Skip Acknowledgment envelopes from projected summaries.

    The hook also filters them at write time by default (see
    ``DailyRawJsonlHook.skip_kinds``), but if an operator opts to log
    acks too, this projector ensures they don't pollute the summary.
    """

    def render(self, envelope, *, perspective, timezone):
        return None


class SchedulerHeartbeatSkipProjector:
    """Skip scheduler-heartbeat envelopes from projected summaries while
    letting other scheduler-job text traffic through.

    Recognized as: TextMessage with ``metadata.scheduler_job == "heartbeat"``
    (set by ``SchedulerEndpoint._fire``).

    Registered against ``"TextMessage"`` *replacing* the plain
    ``TextMessageProjector`` — internally delegates to it for non-heartbeat
    traffic so we keep the registry a single key per envelope shape.
    """

    _HEARTBEAT_JOB_NAMES = frozenset({"heartbeat"})

    def __init__(self) -> None:
        self._delegate = TextMessageProjector()

    def render(self, envelope, *, perspective, timezone):
        job = envelope.metadata.get("scheduler_job")
        if job in self._HEARTBEAT_JOB_NAMES:
            return None
        return self._delegate.render(envelope, perspective=perspective, timezone=timezone)
```

Update `__init__.py` re-exports:

```python
from agent_core.bus_log.projectors import (
    AcknowledgmentSkipProjector,
    HandoffFailedProjector,
    HandoffReadyProjector,
    Projector,
    SchedulerHeartbeatSkipProjector,
    TextMessageProjector,
    fallback_projector,
    get_projector,
    register_projector,
)
```

Add the new projector class names to `__all__` (do not add `reset_registry` — it's intentionally kept module-private to `agent_core.bus_log.projectors`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_bus_log_default_projectors.py -v`
Expected: all green (16 tests including new HandoffReady/HandoffFailed/Ack/Heartbeat coverage).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/bus_log/ packages/core/tests/test_bus_log_default_projectors.py
git commit -m "feat(bus_log): HandoffReady/HandoffFailed/Ack-skip/Heartbeat-skip projectors"
```

---

### Task 6: DailyRawJsonlHook — write side

**Files:**
- Create: `packages/core/src/agent_core/bus_log/writer.py`
- Create: `packages/core/src/agent_core/bus_hooks/daily_raw_jsonl.py`
- Test: `packages/core/tests/test_daily_raw_jsonl_hook.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_daily_raw_jsonl_hook.py`:

```python
"""DailyRawJsonlHook: write side. Append-only JSONL of bus envelopes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    Envelope,
    EventPayload,
    TextMessagePayload,
)
from agent_core.bus_hooks.daily_raw_jsonl import DailyRawJsonlHook
from agent_core.bus_log.reader import iter_envelopes


def _text_env(eid="e1", *, urgency="green", text="hello") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="discord",
        to="pepper",
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        urgency=urgency,
        created_at=datetime.now(UTC),
    )


def _ack_env() -> Envelope:
    return Envelope(
        id="ack-1",
        correlation_id="c-ack-1",
        from_="discord",
        to="pepper",
        kind="Acknowledgment",
        payload=AcknowledgmentPayload(of="other-id"),
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_hook_appends_text_envelope_to_dated_file(tmp_path: Path):
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="UTC")
    env = _text_env()
    out = await hook.execute(stage="pre_publish", envelope=env, params={})
    assert out is env  # never modifies envelope flow
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    written = list(iter_envelopes(files[0]))
    assert len(written) == 1
    assert written[0].id == "e1"
    assert written[0].payload.text == "hello"


@pytest.mark.asyncio
async def test_hook_round_trips_event_envelope_with_payload_data(tmp_path: Path):
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="UTC")
    env = Envelope(
        id="h1",
        correlation_id="c-h1",
        from_="handoff-jobs",
        to="pepper",
        kind="Event",
        payload=EventPayload(
            type="HandoffReady",
            data={"job_id": "j-1", "handoff_path": "/x/handoff.md"},
        ),
        created_at=datetime.now(UTC),
    )
    await hook.execute(stage="pre_publish", envelope=env, params={})
    files = list(tmp_path.glob("*.jsonl"))
    written = list(iter_envelopes(files[0]))
    assert len(written) == 1
    assert written[0].payload.type == "HandoffReady"
    assert written[0].payload.data["job_id"] == "j-1"
    assert written[0].payload.data["handoff_path"] == "/x/handoff.md"


@pytest.mark.asyncio
async def test_hook_skips_acknowledgment_by_default(tmp_path: Path):
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="UTC")
    out = await hook.execute(stage="pre_publish", envelope=_ack_env(), params={})
    assert out is _ack_env() or out.id == "ack-1"
    # No file created (nothing was written).
    assert list(tmp_path.glob("*.jsonl")) == []


@pytest.mark.asyncio
async def test_hook_can_be_configured_to_log_acks(tmp_path: Path):
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="UTC", skip_kinds=[])
    await hook.execute(stage="pre_publish", envelope=_ack_env(), params={})
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    written = list(iter_envelopes(files[0]))
    assert len(written) == 1
    assert written[0].kind == "Acknowledgment"


@pytest.mark.asyncio
async def test_hook_only_writes_at_pre_publish(tmp_path: Path):
    """pre_deliver fires per delivery attempt; logging there would
    duplicate on redelivery. The hook must no-op for pre_deliver."""
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="UTC")
    out = await hook.execute(stage="pre_deliver", envelope=_text_env(), params={})
    assert out.id == "e1"
    assert list(tmp_path.glob("*.jsonl")) == []


@pytest.mark.asyncio
async def test_hook_does_not_abort_publish_on_oserror(tmp_path: Path, monkeypatch, caplog):
    """A write failure must not raise — bus is source of truth, log is observability."""
    hook = DailyRawJsonlHook(log_root=str(tmp_path / "does-not-exist-and-cannot-be-created"), timezone="UTC")

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("agent_core.bus_log.writer.append_envelope_jsonl", _boom)

    with caplog.at_level("ERROR"):
        out = await hook.execute(stage="pre_publish", envelope=_text_env(), params={})
    assert out.id == "e1"  # envelope flow continues
    assert any("daily_raw_jsonl" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_hook_uses_iso_date_filename_in_configured_timezone(tmp_path: Path):
    """The dated filename must reflect the operator's configured timezone,
    not UTC. A 23:30 UTC publish on 2026-05-03 lands in 2026-05-03 in
    Eastern (19:30 EDT) and 2026-05-03 in UTC — pick a clearer case:
    01:30 UTC May 4 == 21:30 EDT May 3 — Eastern file is May 3."""
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="US/Eastern")
    env = Envelope(
        id="e-tz",
        correlation_id="c-tz",
        from_="discord",
        to="pepper",
        kind="TextMessage",
        payload=TextMessagePayload(text="x"),
        created_at=datetime(2026, 5, 4, 1, 30, 0, tzinfo=UTC),  # 21:30 EDT May 3
    )
    await hook.execute(stage="pre_publish", envelope=env, params={})
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    assert files[0].name == "2026-05-03.jsonl"  # Eastern date


@pytest.mark.asyncio
async def test_hook_creates_log_root_if_missing(tmp_path: Path):
    nested = tmp_path / "nested" / "deeper" / "raw"
    hook = DailyRawJsonlHook(log_root=str(nested), timezone="UTC")
    await hook.execute(stage="pre_publish", envelope=_text_env(), params={})
    assert nested.exists()
    assert any(nested.glob("*.jsonl"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_daily_raw_jsonl_hook.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.bus_log.writer'`.

- [ ] **Step 3: Implement writer.py**

Create `packages/core/src/agent_core/bus_log/writer.py`:

```python
"""Write side of the bus log: small append-only helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_core.bus.envelope import Envelope


def default_log_root() -> Path:
    """Default daemon-owned location for daily JSONL files.

    Both the BusHook (write) and ClaudeCodeMCPEndpoint (read for
    ``show_my_day``) default here, so zero-config setups work.
    """
    return Path.home() / ".agent-core" / "bus" / "raw"


def daily_path(
    log_root: Path,
    *,
    timezone: str = "US/Eastern",
    when: datetime | None = None,
) -> Path:
    """Return ``<log_root>/<YYYY-MM-DD>.jsonl`` for ``when`` in the given
    timezone (defaults to now).

    Date rollover happens at local midnight in the configured timezone —
    a 23:50 ET publish lands in today's file, an 00:10 ET publish
    tomorrow. Operators in different timezones get different rollover
    points, which matches expectations for daily summaries.
    """
    if when is None:
        when = datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    local_date = when.astimezone(ZoneInfo(timezone)).date()
    return log_root / f"{local_date.isoformat()}.jsonl"


def append_envelope_jsonl(path: Path, envelope: Envelope) -> None:
    """Append a single ``Envelope`` to ``path`` as one JSON line.

    Uses ``model_dump_json(by_alias=True)`` so the on-disk shape uses
    ``"from"`` (the alias), matching how envelopes appear elsewhere on
    the wire. Reading is symmetric — ``Envelope.model_validate_json``
    accepts both the alias and the field name because of
    ``populate_by_name=True``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = envelope.model_dump_json(by_alias=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
```

Create `packages/core/src/agent_core/bus_hooks/daily_raw_jsonl.py`:

```python
"""``BusHook`` that writes every published envelope to ``daily/<date>.jsonl``.

Cutover #04: bus traffic (Discord, scheduler, relay, agent-to-agent) lands
in a single bus-owned daily JSONL. Each agent's reflection job filters this
log to its perspective via ``agent_core.bus_log.iter_for_agent``.

Registered only at ``pre_publish`` so each logical publish is logged once
(redelivery does not re-run pre_publish).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from agent_core.bus.envelope import Envelope
from agent_core.bus_log import writer as _writer

log = logging.getLogger(__name__)


_DEFAULT_SKIP_KINDS = ("Acknowledgment", "Progress", "Cancellation")


class DailyRawJsonlHook:
    """Append every published envelope to a dated daemon-owned JSONL.

    Configuration (via ``agent_core.yaml`` ``bus_hooks.pre_publish`` block):

    ```yaml
    bus_hooks:
      pre_publish:
        - type: builtin.daily_raw_jsonl
          params:
            log_root: "~/.agent-core/bus/raw"  # default
            timezone: "US/Eastern"              # default
            skip_kinds: ["Acknowledgment", "Progress", "Cancellation"]
    ```
    """

    def __init__(
        self,
        log_root: str | None = None,
        *,
        timezone: str = "US/Eastern",
        skip_kinds: list[str] | None = None,
    ) -> None:
        self._log_root = Path(log_root).expanduser() if log_root else _writer.default_log_root()
        self._timezone = timezone
        self._skip_kinds = frozenset(
            _DEFAULT_SKIP_KINDS if skip_kinds is None else skip_kinds
        )

    async def execute(
        self,
        stage: Literal["pre_publish", "pre_deliver"],
        envelope: Envelope,
        params: dict,
    ) -> Envelope | None:
        # Only log at pre_publish; pre_deliver fires per delivery attempt
        # and would duplicate on redelivery.
        if stage != "pre_publish":
            return envelope
        if envelope.kind in self._skip_kinds:
            return envelope
        path = _writer.daily_path(self._log_root, timezone=self._timezone, when=envelope.created_at)
        try:
            _writer.append_envelope_jsonl(path, envelope)
        except OSError:
            log.exception("daily_raw_jsonl: failed to append to %s", path)
        return envelope
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_daily_raw_jsonl_hook.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/bus_log/writer.py packages/core/src/agent_core/bus_hooks/daily_raw_jsonl.py packages/core/tests/test_daily_raw_jsonl_hook.py
git commit -m "feat(bus_hooks): DailyRawJsonlHook — append-only bus log writer at pre_publish"
```

---

### Task 7: Pluggy entry-point hookspec for projectors

**Files:**
- Modify: `packages/core/src/agent_core/plugins/specs.py`
- Modify: `packages/core/src/agent_core/plugins/manager.py`
- Modify: `packages/core/src/agent_core/plugins/builtin_aliases.py`
- Modify: `packages/core/src/agent_core/bus_log/__init__.py`
- Test: `packages/core/tests/test_bus_log_projector_discovery.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_bus_log_projector_discovery.py`:

```python
"""Pluggy entry-point discovery for default projectors."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
from agent_core.bus_log import bootstrap_default_projectors, get_projector
from agent_core.bus_log.projectors import reset_registry


def _ts() -> datetime:
    return datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolate_registry():
    reset_registry()
    yield
    reset_registry()


def test_bootstrap_registers_text_message_projector():
    bootstrap_default_projectors()
    env = Envelope(
        id="e1", correlation_id="c1", from_="discord", to="pepper",
        kind="TextMessage", payload=TextMessagePayload(text="hi"),
        created_at=_ts(),
    )
    p = get_projector(env)
    assert p is not None
    # Render to verify it's a real projector, not the fallback.
    row = p.render(env, perspective="pepper", timezone="UTC")
    assert row["content"] == "hi"


def test_bootstrap_registers_handoff_ready_projector():
    bootstrap_default_projectors()
    env = Envelope(
        id="h1", correlation_id="ch1", from_="handoff-jobs", to="pepper",
        kind="Event",
        payload=EventPayload(type="HandoffReady", data={"handoff_path": "/x/h.md"}),
        created_at=_ts(),
    )
    p = get_projector(env)
    row = p.render(env, perspective="pepper", timezone="UTC")
    assert "continuity ready" in row["content"].lower()


def test_bootstrap_registers_handoff_failed_projector():
    bootstrap_default_projectors()
    env = Envelope(
        id="f1", correlation_id="cf1", from_="handoff-jobs", to="pepper",
        kind="Event",
        payload=EventPayload(type="HandoffFailed", data={"error": "boom"}),
        created_at=_ts(),
    )
    p = get_projector(env)
    row = p.render(env, perspective="pepper", timezone="UTC")
    assert "continuity failed" in row["content"].lower()


def test_bootstrap_registers_acknowledgment_skip_projector():
    from agent_core.bus.envelope import AcknowledgmentPayload
    bootstrap_default_projectors()
    env = Envelope(
        id="a1", correlation_id="ca1", from_="discord", to="pepper",
        kind="Acknowledgment", payload=AcknowledgmentPayload(of="other"),
        created_at=_ts(),
    )
    p = get_projector(env)
    row = p.render(env, perspective="pepper", timezone="UTC")
    assert row is None  # Acks are skipped


def test_bootstrap_uses_heartbeat_skip_for_text_messages():
    """The TextMessage slot is filled by SchedulerHeartbeatSkipProjector,
    which delegates to TextMessageProjector for non-heartbeat traffic
    and returns None for scheduler heartbeats."""
    bootstrap_default_projectors()
    env = Envelope(
        id="hb", correlation_id="chb", from_="scheduler", to="pepper",
        kind="TextMessage",
        payload=TextMessagePayload(text="tick"),
        metadata={"scheduler_job": "heartbeat"},
        created_at=_ts(),
    )
    p = get_projector(env)
    row = p.render(env, perspective="pepper", timezone="UTC")
    assert row is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_bus_log_projector_discovery.py -v`
Expected: FAIL — `ImportError: cannot import name 'bootstrap_default_projectors'`.

- [ ] **Step 3: Add the hookspec, BuiltinRuntimePlugin entry, and bootstrap**

Edit `packages/core/src/agent_core/plugins/specs.py`. Add the new hookspec at the bottom of `class AgentCoreSpecs`:

```python
    @hookspec
    def register_bus_log_projectors(self) -> dict[str, Any]:
        """Return projector key -> instance registrations.
        Keys are either ``EventPayload.type`` strings (e.g., "HandoffReady")
        or envelope ``kind`` strings (e.g., "TextMessage")."""
        raise NotImplementedError
```

Edit `packages/core/src/agent_core/plugins/manager.py`. Add a getter alongside the existing `get_*_types`:

```python
def get_bus_log_projectors(pm: pluggy.PluginManager) -> dict[str, Any]:
    """Discover projector registrations from all loaded plugins.
    Last-write-wins on duplicate keys (consistent with register_projector
    semantics)."""
    merged: dict[str, Any] = {}
    for mapping in pm.hook.register_bus_log_projectors():
        if mapping:
            merged.update(mapping)
    return merged
```

(Use `dict.update` rather than `_merge_type_maps` because projector duplicates are *intentional* override semantics, not an error.)

Edit `packages/core/src/agent_core/plugins/builtin_aliases.py`. Add a `register_bus_log_projectors` to `BuiltinRuntimePlugin`. First, find the existing `class BuiltinRuntimePlugin` block. Add this method:

```python
    @hookimpl
    def register_bus_log_projectors(self) -> dict[str, Any]:
        from agent_core.bus_log.projectors import (
            AcknowledgmentSkipProjector,
            HandoffFailedProjector,
            HandoffReadyProjector,
            SchedulerHeartbeatSkipProjector,
        )
        return {
            # Replace plain TextMessageProjector with the heartbeat-aware
            # variant so scheduler heartbeats are filtered from summaries.
            "TextMessage": SchedulerHeartbeatSkipProjector(),
            "Acknowledgment": AcknowledgmentSkipProjector(),
            "HandoffReady": HandoffReadyProjector(),
            "HandoffFailed": HandoffFailedProjector(),
        }
```

Edit `packages/core/src/agent_core/bus_log/__init__.py`. Add a `bootstrap_default_projectors` function and re-export it:

```python
def bootstrap_default_projectors() -> None:
    """Discover projectors via pluggy and load them into the registry.
    Called once at runtime startup (CLI / daemon / MCP endpoint init).
    Safe to call multiple times — re-registering replaces.
    """
    from agent_core.plugins.manager import create_plugin_manager, get_bus_log_projectors

    pm = create_plugin_manager()
    for key, projector in get_bus_log_projectors(pm).items():
        register_projector(key, projector)
```

Update `__all__` in `__init__.py` to include `"bootstrap_default_projectors"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_bus_log_projector_discovery.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/plugins/ packages/core/src/agent_core/bus_log/__init__.py packages/core/tests/test_bus_log_projector_discovery.py
git commit -m "feat(plugins): bus_log projectors via pluggy register_bus_log_projectors hookspec"
```

---

### Task 8: Register `builtin.daily_raw_jsonl` in bus_hook types

**Files:**
- Modify: `packages/core/src/agent_core/plugins/builtin_aliases.py`
- Test: `packages/core/tests/test_bus_log_projector_discovery.py` (extend) — or add a small new test file

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/test_daily_raw_jsonl_hook.py`:

```python
def test_daily_raw_jsonl_registered_as_builtin_bus_hook_type():
    """The hook is discoverable under its yaml alias `builtin.daily_raw_jsonl`."""
    from agent_core.plugins.manager import create_plugin_manager, get_bus_hook_types
    pm = create_plugin_manager()
    types = get_bus_hook_types(pm)
    assert "builtin.daily_raw_jsonl" in types
    assert types["builtin.daily_raw_jsonl"] is DailyRawJsonlHook
```

- [ ] **Step 2: Run tests to verify it fails**

Run: `uv run pytest packages/core/tests/test_daily_raw_jsonl_hook.py::test_daily_raw_jsonl_registered_as_builtin_bus_hook_type -v`
Expected: FAIL — `KeyError: 'builtin.daily_raw_jsonl'` or `assert "builtin.daily_raw_jsonl" in {}`.

- [ ] **Step 3: Add the alias**

Edit `packages/core/src/agent_core/plugins/builtin_aliases.py`. Find the `_BUS_HOOK_TYPES` dict (or the bus-hook section of the imports) and add:

```python
from agent_core.bus_hooks.daily_raw_jsonl import DailyRawJsonlHook
```

Find the existing `register_bus_hook_types` method on `BuiltinRuntimePlugin` (or `_BUS_HOOK_TYPES` dict, depending on the pattern in that file). Add the new alias:

```python
    @hookimpl
    def register_bus_hook_types(self) -> dict[str, type[Any]]:
        return {
            # ... existing entries (preserve them) ...
            "builtin.daily_raw_jsonl": DailyRawJsonlHook,
        }
```

If the file uses a module-level `_BUS_HOOK_TYPES` dict instead, add `"builtin.daily_raw_jsonl": DailyRawJsonlHook` there.

- [ ] **Step 4: Run tests to verify it passes**

Run: `uv run pytest packages/core/tests/test_daily_raw_jsonl_hook.py -v`
Expected: 9 passed (8 prior + the registration test).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/plugins/builtin_aliases.py packages/core/tests/test_daily_raw_jsonl_hook.py
git commit -m "feat(plugins): register builtin.daily_raw_jsonl bus hook type"
```

---

### Task 9: CLI `agent-core bus-log show`

**Files:**
- Create: `packages/core/src/agent_core/bus_log/cli.py`
- Modify: `packages/core/src/agent_core/cli.py`
- Test: `packages/core/tests/test_bus_log_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_bus_log_cli.py`:

```python
"""CLI: agent-core bus-log show."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.cli import app


@pytest.fixture
def sample_log(tmp_path: Path) -> Path:
    """Build a small daily file with two envelopes Pepper participates in
    and one she doesn't."""
    base = datetime(2026, 5, 3, 17, 42, 13, tzinfo=UTC)
    envs = [
        Envelope(
            id="a", correlation_id="ca", from_="discord", to="pepper",
            kind="TextMessage", payload=TextMessagePayload(text="hi"),
            created_at=base,
        ),
        Envelope(
            id="b", correlation_id="cb", from_="pepper", to="discord",
            kind="TextMessage", payload=TextMessagePayload(text="reply"),
            created_at=base,
        ),
        Envelope(
            id="c", correlation_id="cc", from_="vale", to="discord",
            kind="TextMessage", payload=TextMessagePayload(text="not pepper"),
            created_at=base,
        ),
    ]
    log_root = tmp_path / "raw"
    log_root.mkdir()
    target = log_root / "2026-05-03.jsonl"
    target.write_text(
        "\n".join(env.model_dump_json(by_alias=True) for env in envs) + "\n",
        encoding="utf-8",
    )
    return log_root


def test_bus_log_show_requires_agent(sample_log: Path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--date", "2026-05-03",
        "--log-root", str(sample_log),
    ])
    assert result.exit_code != 0
    assert "agent" in result.output.lower() or "agent" in (result.stderr if hasattr(result, "stderr") else "")


def test_bus_log_show_projected_default_filters_to_agent(sample_log: Path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--agent", "pepper",
        "--date", "2026-05-03",
        "--log-root", str(sample_log),
        "--timezone", "UTC",
    ])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines]
    assert len(rows) == 2  # a (in) and b (out); c excluded
    assert {r["cid"] for r in rows} == {"ca", "cb"}


def test_bus_log_show_raw_outputs_full_envelopes(sample_log: Path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--agent", "pepper",
        "--date", "2026-05-03",
        "--log-root", str(sample_log),
        "--raw",
    ])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    parsed = [json.loads(line) for line in lines]
    assert len(parsed) == 2
    # Raw envelopes have id/from/to/kind, NOT the projected ts/dir/sender shape.
    assert all("kind" in p for p in parsed)
    assert all("from" in p for p in parsed)


def test_bus_log_show_limit_returns_last_n(sample_log: Path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--agent", "pepper",
        "--date", "2026-05-03",
        "--log-root", str(sample_log),
        "--limit", "1",
    ])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1


def test_bus_log_show_missing_file_yields_no_output(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--agent", "pepper",
        "--date", "2026-05-03",
        "--log-root", str(tmp_path),  # empty
    ])
    assert result.exit_code == 0
    # Empty stdout is the right behavior: a quiet day, not an error.
    assert result.output.strip() == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_bus_log_cli.py -v`
Expected: FAIL — `Error: No such command 'bus-log'.` from Typer.

- [ ] **Step 3: Implement the CLI subapp**

Create `packages/core/src/agent_core/bus_log/cli.py`:

```python
"""``agent-core bus-log`` Typer subapp."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer

from agent_core.bus_log import bootstrap_default_projectors, iter_for_agent
from agent_core.bus_log.reader import iter_envelopes
from agent_core.bus_log.writer import default_log_root

bus_log_app = typer.Typer(
    name="bus-log",
    help="Inspect the bus daily JSONL log (cutover #04).",
    no_args_is_help=True,
)


@bus_log_app.command("show")
def show(
    agent: str = typer.Option(..., "--agent", help="Agent name (perspective for filtering and projection)."),
    date: str | None = typer.Option(None, "--date", help="YYYY-MM-DD (in --timezone). Defaults to today."),
    log_root: Path = typer.Option(
        None,  # type: ignore[arg-type]
        "--log-root",
        help="Daily JSONL directory. Defaults to ~/.agent-core/bus/raw.",
    ),
    timezone: str = typer.Option("US/Eastern", "--timezone", help="IANA timezone for date interpretation and ts rendering."),
    raw: bool = typer.Option(False, "--raw", help="Output full envelopes (not projected Tool 3 rows)."),
    limit: int | None = typer.Option(None, "--limit", help="Last N rows only."),
) -> None:
    """Show bus log entries for AGENT on DATE.

    Default (projected) output: Tool 3-shaped rows ready for the
    reflection job. ``--raw`` emits full envelope JSON for debugging.
    """
    bootstrap_default_projectors()

    root = log_root if log_root is not None else default_log_root()
    target_date = date or datetime.now(UTC).date().isoformat()
    path = root / f"{target_date}.jsonl"

    if raw:
        items = (env.model_dump(by_alias=True, mode="json") for env in iter_envelopes(path))
        # Filter to the agent's perspective in raw mode too — operator usually wants their slice.
        items = (
            obj for obj in items
            if obj.get("to") == agent or obj.get("from") == agent
        )
    else:
        items = iter_for_agent(path, agent=agent, projected=True, timezone=timezone)

    rows = list(items)
    if limit is not None:
        rows = rows[-limit:]
    for row in rows:
        typer.echo(json.dumps(row))
```

Edit `packages/core/src/agent_core/cli.py` to register the subapp. After the existing `app.add_typer(...)` calls, add:

```python
from agent_core.bus_log.cli import bus_log_app
app.add_typer(bus_log_app, name="bus-log")
```

(Place the import at the top of the file alongside the others.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_bus_log_cli.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/bus_log/cli.py packages/core/src/agent_core/cli.py packages/core/tests/test_bus_log_cli.py
git commit -m "feat(cli): agent-core bus-log show — inspect daily bus traffic"
```

---

### Task 10: MCP tool `show_my_day` on ClaudeCodeMCPEndpoint

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py`
- Test: `packages/core/tests/test_show_my_day_mcp_tool.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_show_my_day_mcp_tool.py`:

```python
"""MCP tool ``show_my_day``: agent-scoped day view."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


@pytest.fixture
def sample_log(tmp_path: Path) -> Path:
    base = datetime(2026, 5, 3, 17, 42, 13, tzinfo=UTC)
    envs = [
        Envelope(
            id="a", correlation_id="ca", from_="discord", to="pepper",
            kind="TextMessage", payload=TextMessagePayload(text="hi"),
            created_at=base,
        ),
        Envelope(
            id="b", correlation_id="cb", from_="discord", to="vale",
            kind="TextMessage", payload=TextMessagePayload(text="not pepper"),
            created_at=base,
        ),
    ]
    root = tmp_path / "raw"
    root.mkdir()
    (root / "2026-05-03.jsonl").write_text(
        "\n".join(e.model_dump_json(by_alias=True) for e in envs) + "\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.asyncio
async def test_show_my_day_returns_only_calling_agents_traffic(sample_log: Path):
    """Endpoint named ``pepper`` returns Pepper's perspective ONLY.
    There is no agent= argument; the agent identity comes from self.name."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper", bus_log_root=sample_log)
    rows = await ep._show_my_day_impl(date="2026-05-03", projected=True)
    assert len(rows) == 1
    assert rows[0]["cid"] == "ca"
    assert rows[0]["dir"] == "in"


@pytest.mark.asyncio
async def test_show_my_day_returns_empty_when_no_log_for_date(sample_log: Path):
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper", bus_log_root=sample_log)
    rows = await ep._show_my_day_impl(date="2026-04-01", projected=True)
    assert rows == []


@pytest.mark.asyncio
async def test_show_my_day_limit_returns_last_n(sample_log: Path, tmp_path: Path):
    """Add a second pepper-touching envelope and verify limit=1 returns the latest."""
    base = datetime(2026, 5, 3, 17, 42, 13, tzinfo=UTC)
    extra = Envelope(
        id="z", correlation_id="cz", from_="pepper", to="discord",
        kind="TextMessage", payload=TextMessagePayload(text="reply"),
        created_at=datetime(2026, 5, 3, 17, 50, 0, tzinfo=UTC),
    )
    target = sample_log / "2026-05-03.jsonl"
    target.write_text(
        target.read_text(encoding="utf-8") + extra.model_dump_json(by_alias=True) + "\n",
        encoding="utf-8",
    )
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper", bus_log_root=sample_log)
    rows = await ep._show_my_day_impl(date="2026-05-03", projected=True, limit=1)
    assert len(rows) == 1
    assert rows[0]["cid"] == "cz"  # the latest


@pytest.mark.asyncio
async def test_show_my_day_default_log_root_when_none_given():
    """Constructor accepts None and falls back to ~/.agent-core/bus/raw."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    from agent_core.bus_log.writer import default_log_root
    assert ep._bus_log_root == default_log_root()


@pytest.mark.asyncio
async def test_show_my_day_raw_returns_envelope_dicts(sample_log: Path):
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper", bus_log_root=sample_log)
    rows = await ep._show_my_day_impl(date="2026-05-03", projected=False)
    assert len(rows) == 1
    assert rows[0]["id"] == "a"
    assert "kind" in rows[0]


@pytest.mark.asyncio
async def test_show_my_day_cannot_be_coerced_to_other_agent(sample_log: Path):
    """The tool has no `agent` parameter; vale's endpoint sees only vale's
    traffic regardless of how the call is made."""
    vale = ClaudeCodeMCPEndpoint(name="vale", mount="/mcp/vale", bus_log_root=sample_log)
    rows = await vale._show_my_day_impl(date="2026-05-03", projected=True)
    assert len(rows) == 1
    assert rows[0]["cid"] == "cb"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_show_my_day_mcp_tool.py -v`
Expected: FAIL — `TypeError: ClaudeCodeMCPEndpoint.__init__() got an unexpected keyword argument 'bus_log_root'`.

- [ ] **Step 3: Add `bus_log_root` constructor param + `show_my_day` MCP tool**

Edit `packages/core/src/agent_core/endpoints/claude_code_mcp.py`.

Add this import near the top of the file alongside the other agent_core imports:

```python
from agent_core.bus_log import bootstrap_default_projectors, iter_for_agent
from agent_core.bus_log.writer import default_log_root
```

In the `ClaudeCodeMCPEndpoint.__init__` signature (the one that has `wake_on_all_acknowledgments`, `outbound_registry_ttl_seconds`, etc.), add the parameter:

```python
        bus_log_root: Path | None = None,
```

Inside `__init__`, after `self.name = name`, add:

```python
        self._bus_log_root = (
            Path(bus_log_root).expanduser() if bus_log_root is not None else default_log_root()
        )
```

(Add `from pathlib import Path` at the top if it isn't imported already.)

Add a private implementation method on the class (suitable for direct calls in tests as well as wrapping by the MCP tool):

```python
    async def _show_my_day_impl(
        self,
        *,
        date: str | None = None,
        projected: bool = True,
        limit: int | None = None,
        timezone: str = "US/Eastern",
    ) -> list[dict]:
        """Return today's bus traffic touching this agent.

        Agent identity comes from ``self.name`` — there is no ``agent``
        parameter exposed via MCP. Each ClaudeCodeMCPEndpoint instance
        is bound to one agent at construction; cross-agent leakage is
        prevented by construction, not by trusting the caller.
        """
        bootstrap_default_projectors()
        target = date or datetime.now(UTC).date().isoformat()
        path = self._bus_log_root / f"{target}.jsonl"
        if projected:
            rows = list(iter_for_agent(path, agent=self.name, projected=True, timezone=timezone))
        else:
            rows = [
                env.model_dump(by_alias=True, mode="json")
                for env in iter_for_agent(path, agent=self.name, projected=False)
            ]
        return rows[-limit:] if limit else rows
```

Now register the MCP tool. Find the existing `@self._mcp.tool()` block that defines `list_pending` (around line 668 of `claude_code_mcp.py`). After the `handle` tool registration, add:

```python
        @self._mcp.tool()
        async def show_my_day(
            date: str | None = None,
            projected: bool = True,
            limit: int | None = None,
        ) -> list[dict]:
            """Return today's bus traffic for this agent.

            Use for self-introspection ('what just happened') or feeding
            into a reflection summary. Projected output is Tool 3-shaped
            rows; ``projected=False`` returns full envelope JSON.

            The agent identity is the name this endpoint was constructed
            with — no ``agent`` parameter is exposed to prevent cross-agent
            queries. ``date`` defaults to today (UTC).
            """
            return await self._show_my_day_impl(date=date, projected=projected, limit=limit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_show_my_day_mcp_tool.py -v`
Expected: 6 passed.

Run the full claude_code_mcp test suite to confirm no regressions:

Run: `uv run pytest packages/core/tests/test_claude_code_mcp.py packages/core/tests/test_claude_code_mcp_auto_ack.py packages/core/tests/test_notify_mail_arrived.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/tests/test_show_my_day_mcp_tool.py
git commit -m "feat(claude_code_mcp): show_my_day MCP tool — agent-scoped bus log view"
```

---

### Task 11: Wire `pepper-agent-core.yaml` and tripwire test

**Files:**
- Modify: `docs/examples/pepper-agent-core.yaml`
- Modify: `packages/core/tests/test_pepper_example_yaml.py`

- [ ] **Step 1: Add the failing test**

Append a test class to `packages/core/tests/test_pepper_example_yaml.py`:

```python
class TestPepperExampleYamlBusLog:
    """Cutover #04 wiring tripwire."""

    def test_bus_hooks_pre_publish_registers_daily_raw_jsonl(self, pepper_pipeline: Pipeline):
        """The example yaml must register builtin.daily_raw_jsonl on
        pre_publish so Pepper's bus traffic is captured for tomorrow's
        reflection summary."""
        # Pipeline doesn't model bus_hooks (it models hook-tool pipelines);
        # we need to load the yaml separately to inspect bus_hooks.
        import yaml as pyyaml
        raw = pyyaml.safe_load(_EXAMPLE_YAML.read_text(encoding="utf-8"))
        bus_hooks = (raw or {}).get("bus_hooks", {}) or {}
        pre_publish = bus_hooks.get("pre_publish") or []
        types = [entry.get("type") for entry in pre_publish]
        assert "builtin.daily_raw_jsonl" in types, (
            "Cutover #04 expects the daily JSONL hook on pre_publish"
        )
```

- [ ] **Step 2: Run tests to verify it fails**

Run: `uv run pytest packages/core/tests/test_pepper_example_yaml.py -v`
Expected: the new test FAILS with `AssertionError: Cutover #04 expects the daily JSONL hook on pre_publish`. Existing tests still pass.

- [ ] **Step 3: Add the bus_hooks block to the example yaml**

Edit `docs/examples/pepper-agent-core.yaml`. After the existing `endpoints:` block and before the `pipelines:` block, add:

```yaml
bus_hooks:
  pre_publish:
    # Cutover #04: every published envelope (Discord, scheduler, relay,
    # cross-agent) lands as JSONL in the daemon-owned daily log. Each
    # agent's reflection job (Pepper's 3 AM cron) filters this log to
    # its perspective via agent_core.bus_log.iter_for_agent. Defaults:
    # log_root ~/.agent-core/bus/raw, timezone US/Eastern, skip_kinds
    # ["Acknowledgment","Progress","Cancellation"].
    - type: builtin.daily_raw_jsonl
      params: {}
```

Update the preamble comment block (just below "How it works:" near the top of the file). Add a numbered item:

```yaml
#   3. pre_publish: every envelope on the bus (Discord inbound, Pepper's
#      replies, scheduler triggers, agent-to-agent) is appended to
#      ~/.agent-core/bus/raw/<date>.jsonl. Pepper's 3 AM reflection job
#      reads this log via agent_core.bus_log.iter_for_agent.
```

(Renumber the existing items 3 onwards — what was item 3 is now item 4.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_pepper_example_yaml.py -v`
Expected: all tests pass (the existing tripwire tests + the new bus_hooks test).

Optional smoke check: load the yaml via the runner to verify it doesn't fail validation:

```bash
uv run python -c "import yaml; yaml.safe_load(open('docs/examples/pepper-agent-core.yaml','r').read())"
```

Expected: no error, valid YAML.

- [ ] **Step 5: Commit**

```bash
git add docs/examples/pepper-agent-core.yaml packages/core/tests/test_pepper_example_yaml.py
git commit -m "feat(yaml): wire builtin.daily_raw_jsonl bus hook into pepper example + tripwire test"
```

---

### Task 12: Test playbook + ledger updates

**Files:**
- Create: `docs/cutover/test-playbooks/04-daily-jsonl-pipeline.md`
- Modify: `docs/cutover/test-playbooks/README.md`
- Modify: `docs/requirements/pepper-cutover-04-daily-jsonl-pipeline.md`
- Modify: `docs/requirements/pepper-cutover-agent-playbook.md`
- Modify: `docs/requirements/pepper-pre-cutover-must-haves.md`

- [ ] **Step 1: Write the test playbook**

Create `docs/cutover/test-playbooks/04-daily-jsonl-pipeline.md`:

```markdown
# Cutover #04 — Daily JSONL pipeline (test playbook)

**Spec:** [`docs/requirements/pepper-cutover-04-daily-jsonl-pipeline.md`](../../requirements/pepper-cutover-04-daily-jsonl-pipeline.md)
**Design:** [`docs/superpowers/specs/2026-05-03-bus-log-pipeline-design.md`](../../superpowers/specs/2026-05-03-bus-log-pipeline-design.md)
**Implementation commits:** (filled in when committed)

## What was implemented

Single bus-owned daily JSONL log written by a `pre_publish` BusHook, plus a read library + inspection CLI + per-agent MCP tool.

- Write: `builtin.daily_raw_jsonl` writes every published envelope to `~/.agent-core/bus/raw/<date>.jsonl` in bus-native shape (full envelope, no info loss). Skips `Acknowledgment`/`Progress`/`Cancellation` by default; configurable.
- Library: `agent_core.bus_log` exposes `iter_envelopes` (raw) and `iter_for_agent` (filter to one agent + project to Tool 3 rows via registered projectors).
- Projectors: default coverage for `TextMessage` (with scheduler-heartbeat skip), `Acknowledgment` (skip), `HandoffReady`, `HandoffFailed`, plus a fallback projector that renders unregistered event types generically (never silently dropped). New event types register projectors via the `register_bus_log_projectors` pluggy hookspec.
- CLI: `agent-core bus-log show --agent <name> [--date YYYY-MM-DD] [--projected | --raw] [--limit N]` — for cron and operators.
- MCP: `show_my_day(date=None, projected=True, limit=None)` on `ClaudeCodeMCPEndpoint` — agent identity is read from `self.name`; there is no `agent` parameter, so cross-agent queries are prevented by construction.
- Pepper example yaml (`docs/examples/pepper-agent-core.yaml`) registers the hook on `pre_publish`. Tripwire test in `test_pepper_example_yaml.py` catches removal.

Pepper's existing 3 AM reflection job (her code, not in this repo) gets a single one-line change to call `iter_for_agent(...)` instead of reading raw JSON files. That's "the single documented adapter" the spec invites.

## Acceptance criteria (from spec §"Done looks like")

A mixed-traffic test day produces a JSONL file the reflection job can summarize:

1. Discord inbound message + Pepper's reply → both in JSONL with consistent envelope shape.
2. Scheduler trigger (e.g., scheduled prompt) → in JSONL.
3. Channel-relay event (notification through `notifications/claude/channel`) — the underlying envelope that triggered the relay → in JSONL.
4. The existing reflection job runs at 3 AM and produces `Memory/daily/summaries/<date>.md` matching the current shape (after the one-line `iter_for_agent` adapter).

Heartbeat-noise filtering still applies (the WAR `gather.py` filters scheduler-heartbeat entries; the projector layer filters them too via `SchedulerHeartbeatSkipProjector`).

## Verification steps (end-of-cutover)

### Step 1 — Automated unit + integration tests

```powershell
cd E:\workspaces\ai\agents\agent_core
uv run pytest packages/core/tests/test_bus_log_projectors_registry.py `
              packages/core/tests/test_bus_log_default_projectors.py `
              packages/core/tests/test_bus_log_reader.py `
              packages/core/tests/test_bus_log_iter_for_agent.py `
              packages/core/tests/test_bus_log_projector_discovery.py `
              packages/core/tests/test_daily_raw_jsonl_hook.py `
              packages/core/tests/test_bus_log_cli.py `
              packages/core/tests/test_show_my_day_mcp_tool.py `
              packages/core/tests/test_pepper_example_yaml.py -v
```

Expected: all green. Confirms the registry, projectors (TextMessage/Fallback/HandoffReady/HandoffFailed/Acknowledgment-skip/Heartbeat-skip), reader (raw + filtered + projected, time-bounded), pluggy entry-point discovery, BusHook (write side, skip kinds, timezone-aware date rolling, OSError tolerance), CLI (all flag combinations), MCP tool (auto-scoping by `self.name`), and the Pepper-yaml tripwire all hold.

### Step 2 — Live mixed-traffic day

Boot the bus daemon with the example yaml. Drive a small mixed-traffic day:

1. Send a Discord message to Pepper. She replies via the channel relay.
2. Trigger a scheduler job (one-shot near-future entry).
3. Force a SessionEnd to fire a `HandoffReady` (cutover #02 mechanism).

**Test:**

```powershell
uv run agent-core bus-log show --agent pepper --date $(Get-Date -Format "yyyy-MM-dd")
```

Expected: Tool 3-shaped rows on stdout covering all three streams. Heartbeat envelopes (if scheduler heartbeats are configured) must NOT appear in projected output — confirm with `--raw` that they exist on disk:

```powershell
uv run agent-core bus-log show --agent pepper --date $(Get-Date -Format "yyyy-MM-dd") --raw `
  | Select-String '"scheduler_job":"heartbeat"'
```

Expected: matches present in raw output, absent from projected.

### Step 3 — Reflection-job adapter

In Pepper's reflection codebase (separate repo), update `gather.py` to read via `iter_for_agent`:

```python
from agent_core.bus_log import iter_for_agent
rows = list(iter_for_agent(
    Path.home() / ".agent-core/bus/raw" / f"{date}.jsonl",
    agent="pepper",
    projected=True,
))
```

Run the 3 AM reflection cron manually against yesterday's log. **Test:** `Memory/daily/summaries/<yesterday>.md` is produced, has Pepper's traffic, and matches the existing shape Friday's WAR consumes.

### Step 4 — In-session self-introspection (MCP tool)

From a live Pepper session, call `show_my_day` via MCP. Expected: returns the same Tool 3 rows the CLI produces, but scoped automatically to Pepper. Verify cross-agent isolation: Pepper cannot pass `agent="vale"` because the parameter doesn't exist.

## Pass/fail summary

| Check | Pass when |
|---|---|
| Step 1 | All 9 listed test files green. |
| Step 2 | Mixed-traffic day produces projected rows for Discord in/out, scheduler trigger, and HandoffReady; heartbeats absent from projected, present in raw. |
| Step 3 | Reflection job's one-line adapter runs unmodified; produces `daily/summaries/<date>.md` matching existing shape. |
| Step 4 | `show_my_day` returns Pepper's rows; cannot be coerced to another agent. |

## Known limitations (recorded; not blocking #04 done)

- **Cross-machine deployment.** The bus log lives on the daemon's machine. If Pepper's reflection ever runs on a different machine than the daemon, an HTTP export endpoint or file sync becomes necessary — separate ticket.
- **Multi-tenant isolation.** All agents' traffic shares one file. Today all agents are owned by the same operator; if multi-tenant ever becomes real, per-agent files become a follow-up ticket.
- **Slow-consumer / disk-full robustness.** The hook catches `OSError` and continues (logged at ERROR). An adversarial environment could lose log entries. Acceptable because the bus is the source of truth; the log is observability.
- **No backfill from existing `Memory/daily/raw/`.** If a historical re-summary is ever needed, that's an ad-hoc migration, not framework code.
```

- [ ] **Step 2: Update the test-playbook README index**

Edit `docs/cutover/test-playbooks/README.md`. Find the `## Index` table and insert a row for #04 (sorted by ticket number) between #02 and #07:

```markdown
| 04 | [Daily JSONL pipeline](04-daily-jsonl-pipeline.md) | Implementation complete; verification pending end-of-cutover run. Single bus-owned log + read-time filter + per-agent MCP introspection. |
```

- [ ] **Step 3: Update the spec frontmatter**

Edit `docs/requirements/pepper-cutover-04-daily-jsonl-pipeline.md`. Replace the `**Status:**` line:

```markdown
**Status:** Implementation complete (verification deferred to end-of-cutover gate; see [`docs/cutover/test-playbooks/04-daily-jsonl-pipeline.md`](../cutover/test-playbooks/04-daily-jsonl-pipeline.md))
```

- [ ] **Step 4: Update the agent playbook ledger**

Edit `docs/requirements/pepper-cutover-agent-playbook.md`. Find the per-ticket status table row for `04`. Replace it:

```markdown
| 04 | [Daily JSONL pipeline](pepper-cutover-04-daily-jsonl-pipeline.md) | **Implementation complete** | Single bus-owned daily JSONL at `~/.agent-core/bus/raw/<date>.jsonl` written by `builtin.daily_raw_jsonl` on `pre_publish`. New `agent_core.bus_log` library exposes `iter_envelopes` + `iter_for_agent` (filter + project via pluggy-registered projectors). Three call surfaces: CLI `agent-core bus-log show --agent <name>`, MCP tool `show_my_day` on `ClaudeCodeMCPEndpoint` (auto-scoped to `self.name`), and direct library import for Pepper's reflection job. Default projectors: `TextMessage` (with scheduler-heartbeat skip), `HandoffReady`, `HandoffFailed`, `Acknowledgment` (skip), plus a fallback projector for unregistered event types. Test playbook: [`04-daily-jsonl-pipeline.md`](../cutover/test-playbooks/04-daily-jsonl-pipeline.md). |
```

- [ ] **Step 5: Update the epic ledger**

Edit `docs/requirements/pepper-pre-cutover-must-haves.md`. Find the `04` row in the at-a-glance table and change `Not started` to `Implementation complete   `.

- [ ] **Step 6: Run all tests one more time as the final pass**

Run: `uv run pytest packages/core/tests/ -q 2>&1 | tail -10`
Expected: full suite green (every previously-passing test still passes; the 8 new test files contribute their tests on top).

- [ ] **Step 7: Commit**

```bash
git add docs/cutover/test-playbooks/04-daily-jsonl-pipeline.md docs/cutover/test-playbooks/README.md docs/requirements/pepper-cutover-04-daily-jsonl-pipeline.md docs/requirements/pepper-cutover-agent-playbook.md docs/requirements/pepper-pre-cutover-must-haves.md
git commit -m "docs(cutover): #04 test playbook + ledger update"
```

---

## Self-review

**Spec coverage:**
- §"Done looks like" #1 (Discord inbound + reply in JSONL with consistent envelope) → Task 6 + Task 4 (write + filter + project both `to=` and `from_=` flow). Tested in Tasks 4, 6.
- §"Done looks like" #2 (scheduler trigger in JSONL) → Task 6 (kind-agnostic write) + Task 5 (heartbeat skip projector keeps non-heartbeat scheduler text). Tested in Task 5.
- §"Done looks like" #3 (channel-relay event) → covered by the underlying envelope being captured at `pre_publish` (Task 6).
- §"Done looks like" #4 (reflection job runs unchanged with single adapter) → Task 4 ships `iter_for_agent` and the test playbook's Step 3 documents the adapter line.
- Bus-native shape with full info → Task 6 (`model_dump_json(by_alias=True)`) + Task 3 round-trip test.
- Pluggy entry-point projector registration → Task 7.
- Three call surfaces (library, CLI, MCP) → Tasks 4, 9, 10.
- `--agent` required at CLI → Task 9 (`...=typer.Option(...)`).
- Auto-scoped MCP tool → Task 10 (no agent param; `self.name`).
- Heartbeat skip + ack skip + fallback never silently drops → Task 2, Task 5.
- Default log root `~/.agent-core/bus/raw` shared between hook and endpoint → Task 6 + Task 10.

All decision-bearing acceptance items map to a task.

**Placeholder scan:** every code step shows the actual code; every test step shows the actual test; every command shows the exact PowerShell/uv invocation; no "TBD" / "TODO" / "implement later" / "similar to Task N".

**Type / signature consistency:**
- `Projector.render(envelope, *, perspective, timezone)` — same signature in every projector class across Tasks 1, 2, 5.
- `iter_for_agent(path, *, agent, projected=True, timezone="US/Eastern", since=None, until=None)` — Task 4 declares; Tasks 9, 10 call with these names.
- `_show_my_day_impl(*, date=None, projected=True, limit=None, timezone="US/Eastern")` — Task 10 declares + uses consistently.
- `register_projector(key, projector)` — Tasks 1, 7.
- `bootstrap_default_projectors()` — Task 7 declares; Tasks 9, 10 call.
- `default_log_root()`, `daily_path(...)`, `append_envelope_jsonl(...)` — Task 6 declares; Task 10 uses `default_log_root`.
- BusHook constructor `DailyRawJsonlHook(log_root=None, *, timezone="US/Eastern", skip_kinds=None)` — Task 6.
- `_BUS_HOOK_TYPES` / `register_bus_hook_types` plugin — Task 8 follows the existing pattern in `builtin_aliases.py`.

All consistent.

---

## Plan complete — execution handoff

Plan saved to `docs/superpowers/plans/2026-05-03-bus-log-pipeline.md` with 12 tasks across write-side, read library, projector machinery, plugin registration, CLI, MCP tool, yaml wiring, and ledger updates.
