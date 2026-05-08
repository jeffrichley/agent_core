# Bus Tail / Audit Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a read-only `builtin.bus_tail_mcp` endpoint type — opt-in, mounted on the bus's existing HTTPHost — that exposes four MCP tools (`tail`, `get_envelope`, `trace_correlation`, `metrics`) over the bus's existing SQLite envelopes table for cross-endpoint debugging.

**Architecture:** New `bus_tail/` package in `agent_core` with a `PersistenceReader` query layer over the existing `Persistence`, six per-kind schema-summary functions, and a `BusTailMCPEndpoint` that hosts a FastMCP server and four tools. Persistence flows through the existing endpoint lifecycle — `start(bus_handle)` resolves the store via a new `BusHandle.persistence()` accessor. No new database, no new fields on `RunnerServices`, no plugin-manager hooks.

**Tech Stack:** Python 3.12, `aiosqlite`, `fastmcp`, `pydantic` (existing envelope models), `pytest` + `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-05-07-issue-16-bus-tail-audit-design.md`

---

## Working branch

Create the branch from current `main` and work on it for the entire plan.

```bash
git checkout main && git pull --ff-only && git checkout -b feat/issue-16-bus-tail-audit-feed
```

Every commit lands on this branch. PR target: `main`.

---

## Reference files (don't read until you need them)

- `packages/core/src/agent_core/bus/persistence.py` — existing `Persistence` class. Read this before Task 2.
- `packages/core/src/agent_core/bus/envelope.py` — `Envelope` + six payload classes. Read this before Task 1.
- `packages/core/src/agent_core/bus/handle.py` — current `BusHandle`. Read this before Task 3.
- `packages/core/src/agent_core/bus/core.py` — `Bus` class; `Bus._store` is created in `Bus.start()` *before* endpoint `start()` calls. Skim before Task 3.
- `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — reference pattern for a FastMCP-hosting bus endpoint (`mount`, `asgi_app()`, `start`, `deliver`, `stop`). Skim before Task 4.
- `packages/core/src/agent_core/mcp_audit/writer.py` — reference for a frozen-dataclass + module structure mirror.
- `packages/core/src/agent_core/bus/runner.py` — yaml→endpoint construction. Read before Task 6.

---

## Task 1: Schema-summary registry

**Files:**
- Create: `packages/core/src/agent_core/bus_tail/__init__.py`
- Create: `packages/core/src/agent_core/bus_tail/summaries.py`
- Test: `packages/core/tests/test_bus_tail_summaries.py`

**Why first:** Pure functions. No bus dependency, no async, no I/O. Fast TDD cycle. Subsequent tasks consume the `SUMMARIZERS` registry.

- [ ] **Step 1: Create the package init (empty for now)**

```python
# packages/core/src/agent_core/bus_tail/__init__.py
"""Read-only audit/tail surface for the bus.

See docs/superpowers/specs/2026-05-07-issue-16-bus-tail-audit-design.md.
"""
```

- [ ] **Step 2: Write failing tests for all six summarizers + registry + unknown-kind fallback**

```python
# packages/core/tests/test_bus_tail_summaries.py
"""Tests for the per-kind schema-summary registry.

Each summarizer must return a value-free shape: keys describe the payload
structure, but no user-supplied values leak through. Tool/event/status/of
fields are structural identifiers (closed enums or namespaces) and are
safe to surface verbatim per the design spec.
"""

from __future__ import annotations

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    CancellationPayload,
    EventPayload,
    ProgressPayload,
    TextMessagePayload,
    ToolInvocationPayload,
)
from agent_core.bus_tail.summaries import SUMMARIZERS, summarize_payload


def test_summarize_text_message_returns_shape_only():
    payload = TextMessagePayload(text="hello world", attachments=[{"a": 1}, {"b": 2}])
    summary = SUMMARIZERS["TextMessage"](payload)
    assert summary == {"text_length": 11, "attachment_count": 2}
    # No raw text value leaks.
    assert "hello world" not in str(summary)


def test_summarize_tool_invocation_includes_tool_name_and_keys():
    payload = ToolInvocationPayload(tool="send_envelope", args={"to": "pepper", "text": "hi"})
    summary = SUMMARIZERS["ToolInvocation"](payload)
    assert summary == {
        "tool": "send_envelope",
        "arg_count": 2,
        "arg_keys": ["text", "to"],  # sorted
    }
    # No arg values leak.
    assert "pepper" not in str(summary)
    assert "hi" not in str(summary)


def test_summarize_event_includes_type_version_keys():
    payload = EventPayload(type="HandoffReady", schema_version="1", data={"x": 1, "y": 2})
    summary = SUMMARIZERS["Event"](payload)
    assert summary == {
        "type": "HandoffReady",
        "schema_version": "1",
        "data_keys": ["x", "y"],
    }
    # No data values leak.
    assert "1" not in summary["data_keys"]
    assert 1 not in summary["data_keys"]


def test_summarize_cancellation_marks_reason_presence_only():
    with_reason = CancellationPayload(reason="user_clicked_stop")
    without = CancellationPayload(reason=None)
    assert SUMMARIZERS["Cancellation"](with_reason) == {"has_reason": True}
    assert SUMMARIZERS["Cancellation"](without) == {"has_reason": False}


def test_summarize_progress_keeps_status_drops_note_text():
    payload = ProgressPayload(status="working", note="halfway done", percent=50.0)
    summary = SUMMARIZERS["Progress"](payload)
    assert summary == {"status": "working", "has_note": True, "has_percent": True}
    assert "halfway done" not in str(summary)


def test_summarize_acknowledgment_includes_of_reference():
    payload = AcknowledgmentPayload(of="env-123", note="seen")
    summary = SUMMARIZERS["Acknowledgment"](payload)
    assert summary == {"of": "env-123", "has_note": True}


def test_summarize_payload_dispatches_by_kind():
    payload = TextMessagePayload(text="hi", attachments=[])
    summary = summarize_payload(payload)
    assert summary == {"text_length": 2, "attachment_count": 0}


def test_summarize_payload_unknown_kind_returns_warning():
    # Construct a payload-like object with an unknown kind via a stub.
    class FakePayload:
        kind = "FutureKind"

    summary = summarize_payload(FakePayload())
    assert summary == {"warning": "no summarizer for kind=FutureKind"}


def test_registry_covers_every_envelope_kind():
    """Guard against new envelope kinds shipping without summarizers."""
    from typing import get_args

    from agent_core.bus.envelope import EnvelopePayload

    # EnvelopePayload is Annotated[Union[...], Field(...)].
    # get_args returns (Union[...], Field(...)); first element is the union.
    union = get_args(EnvelopePayload)[0]
    payload_classes = get_args(union)
    kinds = {cls.model_fields["kind"].default for cls in payload_classes}
    assert kinds == set(SUMMARIZERS.keys()), (
        f"missing summarizers for: {kinds - set(SUMMARIZERS.keys())}; "
        f"stale entries: {set(SUMMARIZERS.keys()) - kinds}"
    )
```

- [ ] **Step 3: Run tests and verify they fail**

Run: `uv run pytest packages/core/tests/test_bus_tail_summaries.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_core.bus_tail.summaries'`

- [ ] **Step 4: Implement the summarizers and registry**

```python
# packages/core/src/agent_core/bus_tail/summaries.py
"""Per-kind schema-summary functions.

Each summarizer returns a value-free shape — keys describe the payload's
structure, but no user-supplied values leak. Tool/event/status/of fields
are structural identifiers (closed enums or namespaces) and are surfaced
verbatim per the design spec.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    CancellationPayload,
    EventPayload,
    ProgressPayload,
    TextMessagePayload,
    ToolInvocationPayload,
)


def summarize_text_message(p: TextMessagePayload) -> dict[str, Any]:
    return {"text_length": len(p.text), "attachment_count": len(p.attachments)}


def summarize_tool_invocation(p: ToolInvocationPayload) -> dict[str, Any]:
    return {
        "tool": p.tool,
        "arg_count": len(p.args),
        "arg_keys": sorted(p.args.keys()),
    }


def summarize_event(p: EventPayload) -> dict[str, Any]:
    return {
        "type": p.type,
        "schema_version": p.schema_version,
        "data_keys": sorted(p.data.keys()),
    }


def summarize_cancellation(p: CancellationPayload) -> dict[str, Any]:
    return {"has_reason": p.reason is not None}


def summarize_progress(p: ProgressPayload) -> dict[str, Any]:
    return {
        "status": p.status,
        "has_note": p.note is not None,
        "has_percent": p.percent is not None,
    }


def summarize_acknowledgment(p: AcknowledgmentPayload) -> dict[str, Any]:
    return {"of": p.of, "has_note": p.note is not None}


SUMMARIZERS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "TextMessage": summarize_text_message,
    "Event": summarize_event,
    "ToolInvocation": summarize_tool_invocation,
    "Cancellation": summarize_cancellation,
    "Progress": summarize_progress,
    "Acknowledgment": summarize_acknowledgment,
}


def summarize_payload(payload: Any) -> dict[str, Any]:
    """Dispatch to the registered summarizer; warn on unknown kinds."""
    kind = getattr(payload, "kind", None)
    summarizer = SUMMARIZERS.get(kind) if kind else None
    if summarizer is None:
        return {"warning": f"no summarizer for kind={kind}"}
    return summarizer(payload)


__all__ = ["SUMMARIZERS", "summarize_payload"]
```

- [ ] **Step 5: Run tests and verify they pass**

Run: `uv run pytest packages/core/tests/test_bus_tail_summaries.py -v`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/bus_tail/__init__.py \
        packages/core/src/agent_core/bus_tail/summaries.py \
        packages/core/tests/test_bus_tail_summaries.py
git commit -m "feat(bus_tail): per-kind schema-summary registry

Six summarizers, one per envelope kind. Each returns a value-free
shape — keys describe payload structure but no user content leaks.
Registry covers every EnvelopePayload kind (test asserts no drift).

Refs #16"
```

---

## Task 2: PersistenceReader query layer

**Files:**
- Create: `packages/core/src/agent_core/bus_tail/reader.py`
- Test: `packages/core/tests/test_persistence_reader.py`

**Why second:** The reader wraps the existing `Persistence` and is independently testable against a real SQLite file (no MCP, no FastMCP, no endpoint). Tools in Task 5 consume it.

**Background:** `Persistence` (in `bus/persistence.py`) already exposes `connect`, `insert`, `get`, `list_pending`, `list_by_correlation`, etc. We wrap it (don't extend it) to keep tail-specific queries out of the bus's hot-path module.

The envelopes table schema (relevant columns for queries):
```
id TEXT PRIMARY KEY, correlation_id TEXT, in_reply_to TEXT,
from_endpoint TEXT, to_endpoint TEXT, kind TEXT,
payload_json TEXT, metadata_json TEXT, urgency TEXT,
expires_at TIMESTAMP, created_at TIMESTAMP,
state TEXT, delivery_count INT, last_attempted TIMESTAMP, ...
```

States: `pending | in_flight | acked | dead_letter | expired`.

- [ ] **Step 1: Write failing tests**

```python
# packages/core/tests/test_persistence_reader.py
"""Tests for PersistenceReader — the read-only audit/tail query layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.persistence import Persistence
from agent_core.bus_tail.reader import PersistenceReader


def _make_envelope(
    *,
    id_: str,
    from_: str = "alice",
    to: str = "bob",
    kind: str = "TextMessage",
    text: str = "hi",
    urgency: str = "green",
    correlation_id: str | None = None,
    in_reply_to: str | None = None,
    created_at: datetime | None = None,
) -> Envelope:
    return Envelope(
        id=id_,
        correlation_id=correlation_id or id_,
        in_reply_to=in_reply_to,
        from_=from_,
        to=to,
        kind=kind,
        payload=TextMessagePayload(text=text),
        urgency=urgency,
        created_at=created_at or datetime.now(UTC),
    )


@pytest_asyncio.fixture
async def persistence(tmp_path: Path):
    p = Persistence(tmp_path / "bus.sqlite")
    await p.connect()
    yield p
    await p.close()


@pytest.mark.asyncio
async def test_tail_returns_newest_first(persistence: Persistence):
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    for i in range(5):
        await persistence.insert(
            _make_envelope(id_=f"e{i}", created_at=base + timedelta(minutes=i))
        )
    reader = PersistenceReader(persistence)
    rows = await reader.tail(limit=10)
    assert [r.id for r in rows] == ["e4", "e3", "e2", "e1", "e0"]


@pytest.mark.asyncio
async def test_tail_limit_clamps_to_max(persistence: Persistence):
    for i in range(5):
        await persistence.insert(_make_envelope(id_=f"e{i}"))
    reader = PersistenceReader(persistence)
    rows = await reader.tail(limit=10_000)
    assert len(rows) == 5  # max clamp doesn't synthesize rows; just caps at MAX_LIMIT.


@pytest.mark.asyncio
async def test_tail_limit_clamps_to_min(persistence: Persistence):
    for i in range(3):
        await persistence.insert(_make_envelope(id_=f"e{i}"))
    reader = PersistenceReader(persistence)
    rows = await reader.tail(limit=0)
    assert len(rows) == 1  # clamps to 1


@pytest.mark.asyncio
async def test_tail_filter_by_from_endpoint(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="a", from_="alice"))
    await persistence.insert(_make_envelope(id_="b", from_="bob"))
    reader = PersistenceReader(persistence)
    rows = await reader.tail(from_endpoint="alice")
    assert [r.id for r in rows] == ["a"]


@pytest.mark.asyncio
async def test_tail_filter_by_to_endpoint(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="a", to="alice"))
    await persistence.insert(_make_envelope(id_="b", to="bob"))
    reader = PersistenceReader(persistence)
    rows = await reader.tail(to_endpoint="bob")
    assert [r.id for r in rows] == ["b"]


@pytest.mark.asyncio
async def test_tail_filter_by_kind(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="a", kind="TextMessage"))
    reader = PersistenceReader(persistence)
    rows = await reader.tail(kind="TextMessage")
    assert [r.id for r in rows] == ["a"]
    rows_other = await reader.tail(kind="Event")
    assert rows_other == []


@pytest.mark.asyncio
async def test_tail_filter_by_urgency(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="g", urgency="green"))
    await persistence.insert(_make_envelope(id_="r", urgency="red"))
    reader = PersistenceReader(persistence)
    rows = await reader.tail(urgency="red")
    assert [r.id for r in rows] == ["r"]


@pytest.mark.asyncio
async def test_tail_filter_by_state(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="p"))  # pending by default
    await persistence.insert(_make_envelope(id_="a"))
    await persistence.mark_acked("a")
    reader = PersistenceReader(persistence)
    rows = await reader.tail(state="acked")
    assert [r.id for r in rows] == ["a"]


@pytest.mark.asyncio
async def test_tail_since_is_inclusive(persistence: Persistence):
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    await persistence.insert(_make_envelope(id_="e0", created_at=base))
    await persistence.insert(
        _make_envelope(id_="e1", created_at=base + timedelta(minutes=1))
    )
    reader = PersistenceReader(persistence)
    rows = await reader.tail(since=base)
    assert {r.id for r in rows} == {"e0", "e1"}


@pytest.mark.asyncio
async def test_tail_before_is_exclusive(persistence: Persistence):
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    await persistence.insert(_make_envelope(id_="e0", created_at=base))
    await persistence.insert(
        _make_envelope(id_="e1", created_at=base + timedelta(minutes=1))
    )
    reader = PersistenceReader(persistence)
    rows = await reader.tail(before=base + timedelta(minutes=1))
    assert {r.id for r in rows} == {"e0"}


@pytest.mark.asyncio
async def test_get_envelope_returns_envelope_or_none(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="e0"))
    reader = PersistenceReader(persistence)
    found = await reader.get_envelope("e0")
    assert found is not None
    assert found.id == "e0"
    missing = await reader.get_envelope("nope")
    assert missing is None


@pytest.mark.asyncio
async def test_list_by_correlation_returns_chain_oldest_first(persistence: Persistence):
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    cid = "corr-1"
    await persistence.insert(
        _make_envelope(id_="root", correlation_id=cid, created_at=base)
    )
    await persistence.insert(
        _make_envelope(
            id_="reply",
            correlation_id=cid,
            in_reply_to="root",
            created_at=base + timedelta(seconds=1),
        )
    )
    reader = PersistenceReader(persistence)
    rows = await reader.list_by_correlation(cid)
    assert [r.id for r in rows] == ["root", "reply"]


@pytest.mark.asyncio
async def test_list_by_correlation_unknown_returns_empty(persistence: Persistence):
    reader = PersistenceReader(persistence)
    rows = await reader.list_by_correlation("missing")
    assert rows == []


@pytest.mark.asyncio
async def test_metrics_snapshot_counts_by_kind_and_state(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="t1", kind="TextMessage"))
    await persistence.insert(_make_envelope(id_="t2", kind="TextMessage"))
    await persistence.insert(_make_envelope(id_="a1", kind="TextMessage"))
    await persistence.mark_acked("a1")
    reader = PersistenceReader(persistence)
    snap = await reader.metrics_snapshot()
    assert snap["window"] == "last_24h"
    assert snap["counts_by_kind"]["TextMessage"] == 3
    assert snap["counts_by_state"]["pending"] == 2
    assert snap["counts_by_state"]["acked"] == 1


@pytest.mark.asyncio
async def test_metrics_window_excludes_old_envelopes(persistence: Persistence):
    long_ago = datetime.now(UTC) - timedelta(hours=48)
    recent = datetime.now(UTC) - timedelta(minutes=10)
    await persistence.insert(_make_envelope(id_="old", created_at=long_ago))
    await persistence.insert(_make_envelope(id_="new", created_at=recent))
    reader = PersistenceReader(persistence)
    snap = await reader.metrics_snapshot(window_hours=24)
    assert snap["total_envelopes"] == 1


@pytest.mark.asyncio
async def test_metrics_queue_depth_counts_pending_only(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="p1", to="alice"))
    await persistence.insert(_make_envelope(id_="p2", to="alice"))
    await persistence.insert(_make_envelope(id_="a1", to="bob"))
    await persistence.mark_acked("a1")
    reader = PersistenceReader(persistence)
    snap = await reader.metrics_snapshot()
    assert snap["queue_depth_by_endpoint"]["alice"] == 2
    assert snap["queue_depth_by_endpoint"].get("bob", 0) == 0


@pytest.mark.asyncio
async def test_metrics_ack_latency_null_below_sample_threshold(persistence: Persistence):
    # Insert + ack 5 envelopes (below threshold of 10).
    base = datetime.now(UTC) - timedelta(minutes=10)
    for i in range(5):
        await persistence.insert(_make_envelope(id_=f"e{i}", created_at=base))
        await persistence.mark_in_flight(f"e{i}", base + timedelta(seconds=5))
        await persistence.mark_acked(f"e{i}")
    reader = PersistenceReader(persistence)
    snap = await reader.metrics_snapshot()
    assert snap["ack_latency_ms"] is None


@pytest.mark.asyncio
async def test_metrics_ack_latency_percentiles_above_sample_threshold(
    persistence: Persistence,
):
    base = datetime.now(UTC) - timedelta(minutes=10)
    for i in range(15):
        await persistence.insert(_make_envelope(id_=f"e{i}", created_at=base))
        # last_attempted - created_at varies from 1s to 15s
        attempted = base + timedelta(seconds=i + 1)
        await persistence.mark_in_flight(f"e{i}", attempted)
        await persistence.mark_acked(f"e{i}")
    reader = PersistenceReader(persistence)
    snap = await reader.metrics_snapshot()
    latency = snap["ack_latency_ms"]
    assert latency is not None
    assert {"p50", "p95", "p99"}.issubset(latency.keys())
    # All three percentiles must be > 0 and <= 15_000ms (15s).
    for v in latency.values():
        assert 0 < v <= 15_000
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest packages/core/tests/test_persistence_reader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_core.bus_tail.reader'`

- [ ] **Step 3: Implement `PersistenceReader`**

```python
# packages/core/src/agent_core/bus_tail/reader.py
"""Read-only query layer over the bus's existing Persistence.

The reader wraps a Persistence handle and adds tail-specific queries
(broad filters, aggregations) without polluting the bus's hot-path API.
No second connection, no parallel cache, no writes — just SELECT/COUNT
queries against the existing envelopes table.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import aiosqlite

from agent_core.bus.envelope import Envelope
from agent_core.bus.persistence import Persistence, _row_to_envelope

DEFAULT_LIMIT = 50
MAX_LIMIT = 1000
MIN_LIMIT = 1
ACK_LATENCY_MIN_SAMPLES = 10
DEFAULT_METRICS_WINDOW_HOURS = 24


class PersistenceReader:
    """Read-only query layer for the audit/tail surface."""

    def __init__(self, persistence: Persistence) -> None:
        self._persistence = persistence

    def _conn(self) -> aiosqlite.Connection:
        # Reuse the persistence's existing connection. Assumes connect() ran.
        return self._persistence._require_conn()

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        return max(MIN_LIMIT, min(MAX_LIMIT, int(limit)))

    async def tail(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        since: datetime | None = None,
        before: datetime | None = None,
        from_endpoint: str | None = None,
        to_endpoint: str | None = None,
        kind: str | None = None,
        urgency: Literal["green", "yellow", "red"] | None = None,
        state: Literal["pending", "in_flight", "acked", "dead_letter", "expired"]
        | None = None,
    ) -> list[Envelope]:
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since.isoformat())
        if before is not None:
            clauses.append("created_at < ?")
            params.append(before.isoformat())
        if from_endpoint is not None:
            clauses.append("from_endpoint = ?")
            params.append(from_endpoint)
        if to_endpoint is not None:
            clauses.append("to_endpoint = ?")
            params.append(to_endpoint)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if urgency is not None:
            clauses.append("urgency = ?")
            params.append(urgency)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM envelopes {where} "  # noqa: S608 (no user-controlled SQL — params are bound)
            "ORDER BY created_at DESC LIMIT ?"
        )
        params.append(self._clamp_limit(limit))

        conn = self._conn()
        conn.row_factory = aiosqlite.Row
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_envelope(dict(r)) for r in rows]

    async def get_envelope(self, id_: str) -> Envelope | None:
        return await self._persistence.get(id_)

    async def list_by_correlation(self, correlation_id: str) -> list[Envelope]:
        return await self._persistence.list_by_correlation(correlation_id)

    async def metrics_snapshot(
        self, *, window_hours: int = DEFAULT_METRICS_WINDOW_HOURS
    ) -> dict[str, Any]:
        cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
        cutoff_iso = cutoff.isoformat()
        conn = self._conn()
        conn.row_factory = aiosqlite.Row

        # total_envelopes
        async with conn.execute(
            "SELECT COUNT(*) AS c FROM envelopes WHERE created_at >= ?", (cutoff_iso,)
        ) as cur:
            total_row = await cur.fetchone()
        total_envelopes = int(total_row["c"]) if total_row else 0

        # counts_by_kind
        async with conn.execute(
            "SELECT kind, COUNT(*) AS c FROM envelopes WHERE created_at >= ? GROUP BY kind",
            (cutoff_iso,),
        ) as cur:
            kind_rows = await cur.fetchall()
        counts_by_kind = {r["kind"]: int(r["c"]) for r in kind_rows}

        # counts_by_state
        async with conn.execute(
            "SELECT state, COUNT(*) AS c FROM envelopes WHERE created_at >= ? GROUP BY state",
            (cutoff_iso,),
        ) as cur:
            state_rows = await cur.fetchall()
        counts_by_state = {r["state"]: int(r["c"]) for r in state_rows}

        # queue_depth_by_endpoint (pending only, all-time — pending isn't time-bounded)
        async with conn.execute(
            "SELECT to_endpoint, COUNT(*) AS c FROM envelopes "
            "WHERE state = 'pending' GROUP BY to_endpoint"
        ) as cur:
            depth_rows = await cur.fetchall()
        queue_depth_by_endpoint = {r["to_endpoint"]: int(r["c"]) for r in depth_rows}

        # ack_latency: only count acked envelopes whose created_at is in window
        # AND last_attempted is non-null. Latency = (last_attempted - created_at) ms.
        async with conn.execute(
            "SELECT created_at, last_attempted FROM envelopes "
            "WHERE state = 'acked' AND last_attempted IS NOT NULL AND created_at >= ?",
            (cutoff_iso,),
        ) as cur:
            ack_rows = await cur.fetchall()

        latencies_ms: list[float] = []
        for r in ack_rows:
            try:
                created = datetime.fromisoformat(r["created_at"])
                attempted = datetime.fromisoformat(r["last_attempted"])
            except (TypeError, ValueError):
                continue
            latencies_ms.append((attempted - created).total_seconds() * 1000.0)

        if len(latencies_ms) < ACK_LATENCY_MIN_SAMPLES:
            ack_latency_ms = None
        else:
            ack_latency_ms = _percentiles(latencies_ms, (50, 95, 99))

        return {
            "window": f"last_{window_hours}h",
            "total_envelopes": total_envelopes,
            "counts_by_kind": counts_by_kind,
            "counts_by_state": counts_by_state,
            "queue_depth_by_endpoint": queue_depth_by_endpoint,
            "ack_latency_ms": ack_latency_ms,
        }


def _percentiles(values: list[float], pcts: tuple[int, ...]) -> dict[str, int]:
    """Linear-interpolation percentiles, rounded to nearest int ms."""
    sorted_values = sorted(values)
    n = len(sorted_values)
    out: dict[str, int] = {}
    for p in pcts:
        if n == 1:
            out[f"p{p}"] = int(round(sorted_values[0]))
            continue
        rank = (p / 100.0) * (n - 1)
        lo = int(rank)
        hi = min(lo + 1, n - 1)
        frac = rank - lo
        v = sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac
        out[f"p{p}"] = int(round(v))
    return out


__all__ = ["PersistenceReader"]
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `uv run pytest packages/core/tests/test_persistence_reader.py -v`
Expected: 17 passed

- [ ] **Step 5: Update `bus_tail/__init__.py` to re-export the reader**

```python
# packages/core/src/agent_core/bus_tail/__init__.py
"""Read-only audit/tail surface for the bus.

See docs/superpowers/specs/2026-05-07-issue-16-bus-tail-audit-design.md.
"""

from agent_core.bus_tail.reader import PersistenceReader
from agent_core.bus_tail.summaries import SUMMARIZERS, summarize_payload

__all__ = ["PersistenceReader", "SUMMARIZERS", "summarize_payload"]
```

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/bus_tail/reader.py \
        packages/core/src/agent_core/bus_tail/__init__.py \
        packages/core/tests/test_persistence_reader.py
git commit -m "feat(bus_tail): PersistenceReader with tail/metrics queries

Read-only wrapper over the existing Persistence. Adds tail() (filtered,
clamped, newest-first) and metrics_snapshot() (last-24h aggregates with
percentile ack-latency). get_envelope and list_by_correlation delegate
to the existing Persistence methods.

Refs #16"
```

---

## Task 3: BusHandle.persistence() accessor

**Files:**
- Modify: `packages/core/src/agent_core/bus/handle.py`
- Test: `packages/core/tests/test_bus_handle_persistence.py` (new file)

**Why third:** The BusTailMCPEndpoint will resolve persistence via this accessor in its `start()` lifecycle. This is the smallest, most isolated change to the bus core — land it before pulling on the endpoint.

**Background:** `Bus._store` is created in `Bus.start()` *before* any `endpoint.start(handle)` calls. The accessor exposes that store via the BusHandle that endpoints already receive at start time. Returns the live `Persistence` instance so callers can wrap it (PersistenceReader does).

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/test_bus_handle_persistence.py
"""BusHandle.persistence() exposes Bus._store post-start."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig
from agent_core.bus.handle import BusHandle
from agent_core.bus.persistence import Persistence


@pytest.mark.asyncio
async def test_persistence_returns_store_after_bus_start(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    await bus.start()
    try:
        handle = BusHandle(bus, "any-name")
        store = handle.persistence()
        assert isinstance(store, Persistence)
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_persistence_raises_before_bus_start(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    handle = BusHandle(bus, "any-name")
    with pytest.raises(RuntimeError, match="not initialized"):
        handle.persistence()
```

- [ ] **Step 2: Run test and verify it fails**

Run: `uv run pytest packages/core/tests/test_bus_handle_persistence.py -v`
Expected: FAIL with `AttributeError: 'BusHandle' object has no attribute 'persistence'`

- [ ] **Step 3: Add the accessor to `BusHandle`**

Open `packages/core/src/agent_core/bus/handle.py` and add the new method at the end of the `BusHandle` class (after `endpoints()`):

```python
    def persistence(self) -> "Persistence":
        """Return the bus's persistence store.

        Available after Bus.start() has run. Raises RuntimeError if called
        before the bus's storage layer is initialized — endpoints should
        call this from start(handle) onward, not __init__.

        Exposed primarily for the read-only audit/tail surface (see
        agent_core.bus_tail). Most endpoints should publish/ack/nack via
        this BusHandle's other methods rather than touch persistence
        directly.
        """
        return self._bus._require_store()
```

Also add the import at the top of `handle.py`:

```python
if TYPE_CHECKING:
    from agent_core.bus.core import Bus
    from agent_core.bus.persistence import Persistence
```

- [ ] **Step 4: Run test and verify it passes**

Run: `uv run pytest packages/core/tests/test_bus_handle_persistence.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the full bus test suite to confirm no regressions**

Run: `uv run pytest packages/core/tests/ -k "bus and not bus_tail" -v`
Expected: all existing bus tests pass.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/bus/handle.py \
        packages/core/tests/test_bus_handle_persistence.py
git commit -m "feat(bus): BusHandle.persistence() accessor

Exposes the bus's Persistence store via the BusHandle that endpoints
already receive at start(). Used by the read-only bus-tail endpoint
to construct its PersistenceReader without going through RunnerServices.

Refs #16"
```

---

## Task 4: BusTailMCPEndpoint construction (no tools yet)

**Files:**
- Create: `packages/core/src/agent_core/bus_tail/endpoint.py`
- Test: `packages/core/tests/test_bus_tail_endpoint.py`

**Why fourth:** Builds the endpoint shell — name, mount, asgi_app, start (resolves reader), deliver/stop no-ops — before tools land in Task 5. Lets us verify the lifecycle in isolation.

**Background:** `BusTailMCPEndpoint` satisfies two protocols:
1. `Endpoint` (from `bus/protocol.py`): `name`, `start(bus)`, `deliver(envelope)`, `stop()`.
2. `MCPHostable` (from `bus/http_host.py`): `mount` attribute, `asgi_app()` method.

`deliver` is a no-op because nothing should be addressed to the tail endpoint in normal operation. The bus only calls `deliver(envelope)` for envelopes whose `to:` field matches this endpoint's `name`. If someone explicitly addresses bus-tail, we ack it immediately to keep the bus from getting stuck — see test below.

- [ ] **Step 1: Write failing tests**

```python
# packages/core/tests/test_bus_tail_endpoint.py
"""Lifecycle tests for BusTailMCPEndpoint (no tool calls yet)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_core.bus.core import Bus, BusConfig
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.handle import BusHandle
from agent_core.bus_tail.endpoint import BusTailMCPEndpoint


def test_endpoint_has_required_attributes():
    ep = BusTailMCPEndpoint(name="bus-tail", mount="/mcp/bus-tail")
    assert ep.name == "bus-tail"
    assert ep.mount == "/mcp/bus-tail"


def test_endpoint_default_mount_when_omitted():
    # Constructor allows omitting mount; defaults applied at runner level only.
    # If the runner passes mount=None we want a sane default.
    ep = BusTailMCPEndpoint(name="bus-tail")
    assert ep.mount == "/mcp/bus-tail"


def test_endpoint_asgi_app_returns_object():
    ep = BusTailMCPEndpoint(name="bus-tail", mount="/mcp/bus-tail")
    app = ep.asgi_app()
    assert app is not None
    assert callable(app)


@pytest.mark.asyncio
async def test_start_resolves_reader_via_bus_handle(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    await bus.start()
    try:
        ep = BusTailMCPEndpoint(name="bus-tail", mount="/mcp/bus-tail")
        handle = BusHandle(bus, "bus-tail")
        await ep.start(handle)
        assert ep._reader is not None
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_deliver_acks_immediately(tmp_path: Path):
    """Nothing should address bus-tail, but if it does, ack and don't crash."""
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    await bus.start()
    try:
        ep = BusTailMCPEndpoint(name="bus-tail", mount="/mcp/bus-tail")
        handle = BusHandle(bus, "bus-tail")
        await ep.start(handle)
        # Insert a fake envelope addressed to bus-tail.
        env = Envelope(
            id="env-1",
            correlation_id="corr-1",
            from_="someone",
            to="bus-tail",
            kind="TextMessage",
            payload=TextMessagePayload(text="hello"),
            created_at=datetime.now(UTC),
        )
        await bus._store.insert(env)
        # deliver should not raise; should ack the envelope.
        await ep.deliver(env)
        row = await bus._store.row("env-1")
        assert row is not None and row["state"] == "acked"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_stop_clears_reader():
    ep = BusTailMCPEndpoint(name="bus-tail", mount="/mcp/bus-tail")
    # No start, just construct + stop. Should be idempotent.
    await ep.stop()
    assert ep._reader is None
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest packages/core/tests/test_bus_tail_endpoint.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_core.bus_tail.endpoint'`

- [ ] **Step 3: Implement `BusTailMCPEndpoint` (lifecycle only — no tools yet)**

```python
# packages/core/src/agent_core/bus_tail/endpoint.py
"""BusTailMCPEndpoint — read-only MCP surface for bus-state debugging.

Standalone endpoint type (not auto-attached to agents). Hosts its own
FastMCP server, mounted on the bus's existing HTTPHost at its configured
path. Persistence is resolved via BusHandle.persistence() during start().

The four MCP tools (tail, get_envelope, trace_correlation, metrics) are
registered on the FastMCP server in this endpoint's __init__ via
register_bus_tail_tools — see bus_tail/mcp.py.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from agent_core.bus_tail.reader import PersistenceReader

if TYPE_CHECKING:
    from agent_core.bus.envelope import Envelope
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)

DEFAULT_MOUNT = "/mcp/bus-tail"


class BusTailMCPEndpoint:
    """Read-only MCP endpoint exposing bus state for debugging."""

    def __init__(self, *, name: str, mount: str | None = None) -> None:
        self.name = name
        self.mount = mount if mount is not None else DEFAULT_MOUNT
        self._reader: PersistenceReader | None = None
        self._handle: BusHandle | None = None
        self._mcp: FastMCP = FastMCP(
            name,
            instructions=(
                "Read-only audit/tail surface for the bus. Tools: tail (recent "
                "envelope listing with schema-summary previews), get_envelope "
                "(full payload of one envelope by id), trace_correlation (full "
                "chain by correlation_id), metrics (last-24h aggregates)."
            ),
        )
        # Tools are registered in Task 5.

    # --- Endpoint Protocol ---

    async def start(self, bus: "BusHandle") -> None:
        self._handle = bus
        self._reader = PersistenceReader(bus.persistence())
        log.info("BusTailMCPEndpoint(name=%s) started at mount=%s", self.name, self.mount)

    async def deliver(self, envelope: "Envelope") -> None:
        # Nothing should address bus-tail. If something does, ack to avoid
        # bus-side requeue/back-off. This is a defensive no-op.
        if self._handle is None:
            log.warning(
                "BusTailMCPEndpoint(name=%s) received envelope before start: %s",
                self.name,
                envelope.id,
            )
            return
        await self._handle.ack(envelope.id)
        log.debug(
            "BusTailMCPEndpoint(name=%s) auto-acked unexpected delivery: %s",
            self.name,
            envelope.id,
        )

    async def stop(self) -> None:
        self._reader = None
        self._handle = None
        log.info("BusTailMCPEndpoint(name=%s) stopped", self.name)

    # --- MCPHostable Protocol ---

    def asgi_app(self):
        """Return the ASGI app for this endpoint's FastMCP server."""
        return self._mcp.http_app(path="/")


__all__ = ["BusTailMCPEndpoint", "DEFAULT_MOUNT"]
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `uv run pytest packages/core/tests/test_bus_tail_endpoint.py -v`
Expected: 6 passed

- [ ] **Step 5: Update `bus_tail/__init__.py` to re-export the endpoint**

```python
# packages/core/src/agent_core/bus_tail/__init__.py
"""Read-only audit/tail surface for the bus.

See docs/superpowers/specs/2026-05-07-issue-16-bus-tail-audit-design.md.
"""

from agent_core.bus_tail.endpoint import BusTailMCPEndpoint, DEFAULT_MOUNT
from agent_core.bus_tail.reader import PersistenceReader
from agent_core.bus_tail.summaries import SUMMARIZERS, summarize_payload

__all__ = [
    "DEFAULT_MOUNT",
    "BusTailMCPEndpoint",
    "PersistenceReader",
    "SUMMARIZERS",
    "summarize_payload",
]
```

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/bus_tail/endpoint.py \
        packages/core/src/agent_core/bus_tail/__init__.py \
        packages/core/tests/test_bus_tail_endpoint.py
git commit -m "feat(bus_tail): BusTailMCPEndpoint lifecycle skeleton

Endpoint + MCPHostable Protocol implementation with no tools yet.
start(handle) resolves PersistenceReader via BusHandle.persistence();
deliver no-ops (nothing should address bus-tail); stop tears down.
Tools land in the next commit.

Refs #16"
```

---

## Task 5: Register the four MCP tools

**Files:**
- Create: `packages/core/src/agent_core/bus_tail/mcp.py`
- Modify: `packages/core/src/agent_core/bus_tail/endpoint.py` (call `register_bus_tail_tools` in `__init__`)
- Test: `packages/core/tests/test_bus_tail_mcp.py`

**Why fifth:** With the reader and endpoint shell in place, registering the four tools is mostly mechanical: each tool calls one reader method, formats the response, and returns. We test via FastMCP's in-memory client.

**Background:** Look at `packages/core/src/agent_core/mcp_audit/middleware.py` for an example of a `Middleware` instance, and `packages/agent-core-webcam/src/agent_core_webcam/mcp.py` for a `register_*_tools(mcp, endpoint)` pattern. We'll follow the latter.

`get_envelope` and `trace_correlation` return *full* envelope shapes; `tail` returns *summaries* with schema-summary payload previews.

- [ ] **Step 1: Write failing tests**

```python
# packages/core/tests/test_bus_tail_mcp.py
"""End-to-end tests for the four bus_tail MCP tools via FastMCP in-memory client."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastmcp import Client

from agent_core.bus.core import Bus, BusConfig
from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
from agent_core.bus.handle import BusHandle
from agent_core.bus_tail.endpoint import BusTailMCPEndpoint


async def _setup(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    await bus.start()
    ep = BusTailMCPEndpoint(name="bus-tail", mount="/mcp/bus-tail")
    handle = BusHandle(bus, "bus-tail")
    await ep.start(handle)
    return bus, ep


async def _insert(bus: Bus, **kwargs) -> Envelope:
    defaults = dict(
        id="e1",
        correlation_id=kwargs.get("id", "e1"),
        from_="alice",
        to="bob",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi there"),
        created_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    env = Envelope(**defaults)
    await bus._store.insert(env)
    return env


def _parse_text_content(call_result) -> object:
    """Extract JSON from FastMCP tool call result's TextContent block."""
    content = call_result.content[0]
    return json.loads(content.text)


@pytest.mark.asyncio
async def test_tail_tool_returns_envelope_summaries(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        await _insert(bus, id="e1")
        async with Client(ep._mcp) as client:
            result = await client.call_tool("tail", {"limit": 10})
        rows = _parse_text_content(result)
        assert isinstance(rows, list)
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == "e1"
        assert row["from"] == "alice"
        assert row["to"] == "bob"
        assert row["kind"] == "TextMessage"
        assert row["state"] == "pending"
        # Summary present, value-free.
        assert row["payload_summary"]["text_length"] == len("hi there")
        assert "hi there" not in json.dumps(row)
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_tail_tool_filters_by_kind(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        await _insert(bus, id="t1", kind="TextMessage")
        # Insert an Event-kind envelope.
        evt = Envelope(
            id="ev1",
            correlation_id="ev1",
            from_="alice",
            to="bob",
            kind="Event",
            payload=EventPayload(type="HandoffReady", data={"k": 1}),
            created_at=datetime.now(UTC),
        )
        await bus._store.insert(evt)
        async with Client(ep._mcp) as client:
            result = await client.call_tool("tail", {"kind": "Event"})
        rows = _parse_text_content(result)
        assert [r["id"] for r in rows] == ["ev1"]
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_get_envelope_tool_returns_full_payload(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        await _insert(bus, id="e1", payload=TextMessagePayload(text="full content"))
        async with Client(ep._mcp) as client:
            result = await client.call_tool("get_envelope", {"id": "e1"})
        env = _parse_text_content(result)
        assert env is not None
        assert env["id"] == "e1"
        # Full payload present, including the text.
        assert env["payload"]["text"] == "full content"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_get_envelope_tool_returns_null_for_missing(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        async with Client(ep._mcp) as client:
            result = await client.call_tool("get_envelope", {"id": "nope"})
        env = _parse_text_content(result)
        assert env is None
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_trace_correlation_orders_oldest_first(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
        cid = "corr-1"
        await _insert(bus, id="root", correlation_id=cid, created_at=base)
        await _insert(
            bus,
            id="reply",
            correlation_id=cid,
            in_reply_to="root",
            created_at=base + timedelta(seconds=1),
        )
        async with Client(ep._mcp) as client:
            result = await client.call_tool(
                "trace_correlation", {"correlation_id": cid}
            )
        chain = _parse_text_content(result)
        assert [e["id"] for e in chain] == ["root", "reply"]
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_trace_correlation_unknown_returns_empty_list(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        async with Client(ep._mcp) as client:
            result = await client.call_tool(
                "trace_correlation", {"correlation_id": "missing"}
            )
        chain = _parse_text_content(result)
        assert chain == []
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_metrics_tool_shape(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        await _insert(bus, id="e1")
        async with Client(ep._mcp) as client:
            result = await client.call_tool("metrics", {})
        snap = _parse_text_content(result)
        assert snap["window"] == "last_24h"
        assert "counts_by_kind" in snap
        assert "counts_by_state" in snap
        assert "queue_depth_by_endpoint" in snap
        assert "ack_latency_ms" in snap  # may be None
        assert snap["total_envelopes"] >= 1
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_tail_summary_value_free_for_text_message(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        await _insert(bus, id="e1", payload=TextMessagePayload(text="SECRET-PHRASE"))
        async with Client(ep._mcp) as client:
            result = await client.call_tool("tail", {})
        text = result.content[0].text
        assert "SECRET-PHRASE" not in text
    finally:
        await bus.stop()
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest packages/core/tests/test_bus_tail_mcp.py -v`
Expected: FAIL — tools `tail`, `get_envelope`, `trace_correlation`, `metrics` not found on the FastMCP server.

- [ ] **Step 3: Implement the tool registrar**

```python
# packages/core/src/agent_core/bus_tail/mcp.py
"""Register the four read-only bus-tail MCP tools on a FastMCP server.

Each tool wraps one PersistenceReader method, formats results as JSON,
and returns a single TextContent block. tail() returns summaries (with
value-free payload previews); get_envelope() and trace_correlation()
return full envelope shapes.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from mcp.types import TextContent

from agent_core.bus.envelope import Envelope
from agent_core.bus_tail.summaries import summarize_payload

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from agent_core.bus_tail.reader import PersistenceReader


def _envelope_to_summary(env: Envelope) -> dict[str, Any]:
    return {
        "id": env.id,
        "correlation_id": env.correlation_id,
        "in_reply_to": env.in_reply_to,
        "from": env.from_,
        "to": env.to,
        "kind": env.kind,
        "urgency": env.urgency,
        "created_at": env.created_at.isoformat(),
        "expires_at": env.expires_at.isoformat() if env.expires_at else None,
        "payload_summary": summarize_payload(env.payload),
        "metadata_keys": sorted(env.metadata.keys()),
    }


def _envelope_to_full(env: Envelope) -> dict[str, Any]:
    summary = _envelope_to_summary(env)
    summary["payload"] = env.payload.model_dump()
    summary["metadata"] = env.metadata
    return summary


def register_bus_tail_tools(*, mcp: "FastMCP", get_reader) -> None:
    """Register the four read-only tools.

    ``get_reader`` is a zero-arg callable returning the live
    ``PersistenceReader``. The endpoint passes a closure so tools resolve
    the reader at call time (not at registration time, which happens in
    __init__ before start()).
    """

    @mcp.tool(
        name="tail",
        description=(
            "Recent envelope listing with metadata + value-free payload "
            "summaries. Filterable by from/to/kind/urgency/state and bounded "
            "by since/before timestamps. Newest first. limit clamps to [1, 1000]."
        ),
    )
    async def _tail(
        limit: int = 50,
        since: str | None = None,
        before: str | None = None,
        from_endpoint: str | None = None,
        to_endpoint: str | None = None,
        kind: str | None = None,
        urgency: Literal["green", "yellow", "red"] | None = None,
        state: Literal["pending", "in_flight", "acked", "dead_letter", "expired"]
        | None = None,
    ) -> list[Any]:
        reader: PersistenceReader = get_reader()
        envs = await reader.tail(
            limit=limit,
            since=datetime.fromisoformat(since) if since else None,
            before=datetime.fromisoformat(before) if before else None,
            from_endpoint=from_endpoint,
            to_endpoint=to_endpoint,
            kind=kind,
            urgency=urgency,
            state=state,
        )
        rows = [_envelope_to_summary(e) for e in envs]
        return [TextContent(type="text", text=json.dumps(rows, default=str))]

    @mcp.tool(
        name="get_envelope",
        description=(
            "Return one envelope's full payload + metadata by id. Returns null "
            "if the id is not found. Use this after tail() to drill into a "
            "specific envelope's contents."
        ),
    )
    async def _get_envelope(id: str) -> list[Any]:
        reader: PersistenceReader = get_reader()
        env = await reader.get_envelope(id)
        body = _envelope_to_full(env) if env is not None else None
        return [TextContent(type="text", text=json.dumps(body, default=str))]

    @mcp.tool(
        name="trace_correlation",
        description=(
            "Return all envelopes sharing a correlation_id, oldest first, with "
            "full payloads. Use this to follow a conversation chain (request "
            "→ reply → ack) across endpoints."
        ),
    )
    async def _trace_correlation(correlation_id: str) -> list[Any]:
        reader: PersistenceReader = get_reader()
        envs = await reader.list_by_correlation(correlation_id)
        chain = [_envelope_to_full(e) for e in envs]
        return [TextContent(type="text", text=json.dumps(chain, default=str))]

    @mcp.tool(
        name="metrics",
        description=(
            "Bus aggregates over the last 24h: counts by kind, counts by state, "
            "current queue depth per endpoint (pending only, all-time), and "
            "ack-latency percentiles (null below 10 acked samples)."
        ),
    )
    async def _metrics() -> list[Any]:
        reader: PersistenceReader = get_reader()
        snap = await reader.metrics_snapshot()
        return [TextContent(type="text", text=json.dumps(snap, default=str))]


__all__ = ["register_bus_tail_tools"]
```

- [ ] **Step 4: Wire the registrar into `BusTailMCPEndpoint.__init__`**

Open `packages/core/src/agent_core/bus_tail/endpoint.py` and modify the `__init__` to register tools after the FastMCP construction. Add the import at the top:

```python
from agent_core.bus_tail.mcp import register_bus_tail_tools
```

Then add this block at the end of `__init__`, after the `self._mcp = FastMCP(...)` block:

```python
        # Tools resolve the reader at call time via this closure so they
        # work after start() populates self._reader. Calling a tool before
        # start() raises a clear error.
        def _get_reader() -> PersistenceReader:
            if self._reader is None:
                raise RuntimeError(
                    f"BusTailMCPEndpoint(name={self.name!r}) is not started; "
                    "call start(bus_handle) before invoking tools"
                )
            return self._reader

        register_bus_tail_tools(mcp=self._mcp, get_reader=_get_reader)
```

- [ ] **Step 5: Run tests and verify they pass**

Run: `uv run pytest packages/core/tests/test_bus_tail_mcp.py -v`
Expected: 8 passed

- [ ] **Step 6: Run the full bus_tail suite to confirm everything still works**

Run: `uv run pytest packages/core/tests/test_bus_tail_*.py packages/core/tests/test_persistence_reader.py -v`
Expected: 40 passed (9 + 17 + 6 + 8).

- [ ] **Step 7: Update `bus_tail/__init__.py` to re-export the registrar**

```python
# packages/core/src/agent_core/bus_tail/__init__.py
"""Read-only audit/tail surface for the bus.

See docs/superpowers/specs/2026-05-07-issue-16-bus-tail-audit-design.md.
"""

from agent_core.bus_tail.endpoint import BusTailMCPEndpoint, DEFAULT_MOUNT
from agent_core.bus_tail.mcp import register_bus_tail_tools
from agent_core.bus_tail.reader import PersistenceReader
from agent_core.bus_tail.summaries import SUMMARIZERS, summarize_payload

__all__ = [
    "DEFAULT_MOUNT",
    "BusTailMCPEndpoint",
    "PersistenceReader",
    "SUMMARIZERS",
    "register_bus_tail_tools",
    "summarize_payload",
]
```

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/agent_core/bus_tail/mcp.py \
        packages/core/src/agent_core/bus_tail/endpoint.py \
        packages/core/src/agent_core/bus_tail/__init__.py \
        packages/core/tests/test_bus_tail_mcp.py
git commit -m "feat(bus_tail): register tail/get_envelope/trace_correlation/metrics tools

Each tool wraps one PersistenceReader method and returns a TextContent
block with JSON. tail() returns value-free schema summaries; get_envelope
and trace_correlation return full payloads. Tools resolve the reader at
call time via a closure so they work after the endpoint's start().

Refs #16"
```

---

## Task 6: Runner integration

**Files:**
- Modify: `packages/core/src/agent_core/plugins/builtin_aliases.py`
- Test: `packages/core/tests/test_bus_tail_runner.py`

**Why last:** This stitches everything together — register `builtin.bus_tail_mcp` as an endpoint type so the runner can construct it from yaml. Once this lands, an end-to-end test exercises the entire path: yaml → endpoint → bus.start → HTTPHost mount.

**Background:** Built-in endpoint types are registered in `packages/core/src/agent_core/plugins/builtin_aliases.py` via the `_ENDPOINT_TYPES` dict and the `register_endpoint_types` hookimpl. This module is loaded via the `agent_core` entry-point in `packages/core/pyproject.toml`. We just add one line to the dict and one import.

- [ ] **Step 1: Write failing integration test**

```python
# packages/core/tests/test_bus_tail_runner.py
"""Runner integration tests for the bus_tail endpoint type."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.bus.runner import build_bus_from_config
from agent_core.bus_tail.endpoint import BusTailMCPEndpoint


def _write_yaml(tmp_path: Path, contents: str) -> Path:
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(contents, encoding="utf-8")
    return cfg


@pytest.mark.asyncio
async def test_runner_registers_bus_tail_mcp_from_yaml(tmp_path: Path):
    storage = (tmp_path / "bus.sqlite").as_posix()
    cfg = _write_yaml(
        tmp_path,
        f"""
bus:
  storage_path: {storage}
endpoints:
  - name: bus-tail
    type: builtin.bus_tail_mcp
    params:
      mount: /mcp/bus-tail
""",
    )
    bus, http_host = await build_bus_from_config(cfg)
    try:
        ep = bus._endpoints_by_name["bus-tail"].endpoint
        assert isinstance(ep, BusTailMCPEndpoint)
        assert ep.mount == "/mcp/bus-tail"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_runner_mounts_bus_tail_on_http_host(tmp_path: Path):
    storage = (tmp_path / "bus.sqlite").as_posix()
    cfg = _write_yaml(
        tmp_path,
        f"""
bus:
  storage_path: {storage}
endpoints:
  - name: bus-tail
    type: builtin.bus_tail_mcp
""",
    )
    bus, http_host = await build_bus_from_config(cfg)
    try:
        assert http_host is not None
        mount_paths = {m.mount for m in http_host._mounts}
        assert "/mcp/bus-tail" in mount_paths
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_runner_default_yaml_omits_bus_tail(tmp_path: Path):
    storage = (tmp_path / "bus.sqlite").as_posix()
    cfg = _write_yaml(
        tmp_path,
        f"""
bus:
  storage_path: {storage}
endpoints: []
""",
    )
    bus, http_host = await build_bus_from_config(cfg)
    try:
        names = list(bus._endpoints_by_name.keys())
        assert "bus-tail" not in names
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_runner_uses_default_mount_when_omitted(tmp_path: Path):
    storage = (tmp_path / "bus.sqlite").as_posix()
    cfg = _write_yaml(
        tmp_path,
        f"""
bus:
  storage_path: {storage}
endpoints:
  - name: bus-tail
    type: builtin.bus_tail_mcp
""",
    )
    bus, http_host = await build_bus_from_config(cfg)
    try:
        ep = bus._endpoints_by_name["bus-tail"].endpoint
        assert ep.mount == "/mcp/bus-tail"
    finally:
        await bus.stop()
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest packages/core/tests/test_bus_tail_runner.py -v`
Expected: FAIL with `BusBootError: unknown endpoint type: 'builtin.bus_tail_mcp'`

- [ ] **Step 3: Register the endpoint type in `builtin_aliases.py`**

Open `packages/core/src/agent_core/plugins/builtin_aliases.py`. Two changes:

1. Add the import alongside the other endpoint imports near the top of the file:

```python
from agent_core.bus_tail.endpoint import BusTailMCPEndpoint
```

2. Add the entry to the `_ENDPOINT_TYPES` dict (alphabetical by alias):

```python
_ENDPOINT_TYPES: dict[str, type[Any]] = {
    "builtin.bus_tail_mcp": BusTailMCPEndpoint,
    "builtin.claude_code_mcp": ClaudeCodeMCPEndpoint,
    "builtin.handoff_jobs": HandoffJobsEndpoint,
    "builtin.scheduler": SchedulerEndpoint,
    "builtin.stub": StubEndpoint,
}
```

The existing `register_endpoint_types` hookimpl reads from this dict, so no other code changes are needed.

- [ ] **Step 4: Run tests and verify they pass**

Run: `uv run pytest packages/core/tests/test_bus_tail_runner.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: all packages/core tests pass (no regressions in claude_code_mcp, mcp_audit, persistence, runner, etc.).

- [ ] **Step 6: Smoke-test the daemon end-to-end (manual, optional)**

Create a temporary yaml at `~/.agent-core/test-bus-tail.yaml`:

```yaml
bus:
  storage_path: ~/.agent-core/test-bus-tail.sqlite
endpoints:
  - name: bus-tail
    type: builtin.bus_tail_mcp
```

Run: `uv run agent-core bus run --config ~/.agent-core/test-bus-tail.yaml` (in another terminal).

In a third terminal: `curl http://127.0.0.1:8788/mcp/bus-tail/` — expect a non-404 response (FastMCP's HTTP MCP server responding). Stop with Ctrl-C.

Clean up:

```bash
rm ~/.agent-core/test-bus-tail.yaml ~/.agent-core/test-bus-tail.sqlite*
```

The integration tests in Step 4 cover the same path, so this manual smoke-test is optional. Skip if the integration tests passed cleanly.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/agent_core/plugins/builtin_aliases.py \
        packages/core/tests/test_bus_tail_runner.py
git commit -m "feat(bus_tail): register builtin.bus_tail_mcp endpoint type

Adds BusTailMCPEndpoint to the builtin_aliases registry so the runner
can construct it from yaml. End-to-end: yaml entry -> runner builds
endpoint -> bus registers it -> HTTPHost mounts it -> tools callable
at /mcp/bus-tail/.

Closes #16"
```

---

## Final review and PR

- [ ] **Step 1: Run the entire test suite once more**

Run: `uv run pytest packages/core/tests/ -v`
Expected: all green.

- [ ] **Step 2: Run ruff/lint**

Run: `uv run ruff check packages/core/src/agent_core/bus_tail/ packages/core/tests/test_bus_tail_*.py packages/core/tests/test_persistence_reader.py packages/core/tests/test_bus_handle_persistence.py`
Expected: no errors.

If there are import-ordering issues (`I001`), let ruff fix them: `uv run ruff check --fix <paths>`.

- [ ] **Step 3: Update the roadmap status**

Open `docs/superpowers/plans/2026-05-07-open-issues-cleanup-roadmap.md` and update the Phase 2 status row from "⬜ Not started" to "✅ Done" with started/completed dates and a brief note. Phase 2 contained #39 (already merged) and #16 (this PR); both are now done.

```bash
git add docs/superpowers/plans/2026-05-07-open-issues-cleanup-roadmap.md
git commit -m "docs(roadmap): mark Phase 2 (observability) complete"
```

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin feat/issue-16-bus-tail-audit-feed
gh pr create --base main --title "feat: read-only bus tail / audit feed (#16)" --body "$(cat <<'EOF'
## Summary

Adds `builtin.bus_tail_mcp` — a standalone, opt-in MCP endpoint type that exposes four read-only tools (`tail`, `get_envelope`, `trace_correlation`, `metrics`) over the bus's existing SQLite envelopes table, for cross-endpoint debugging.

- New `bus_tail/` package: `summaries.py` (per-kind value-free payload summaries), `reader.py` (PersistenceReader query layer), `endpoint.py` (BusTailMCPEndpoint), `mcp.py` (four tool registrations).
- `BusHandle.persistence()` accessor exposes the bus's store at endpoint-start time (no RunnerServices plumbing needed; persistence is bus-internal).
- Registered as a builtin endpoint type so operators wire it up via yaml when needed.
- Closes Phase 2 of the open-issues cleanup roadmap.

## Design

Spec: [docs/superpowers/specs/2026-05-07-issue-16-bus-tail-audit-design.md](docs/superpowers/specs/2026-05-07-issue-16-bus-tail-audit-design.md)
Plan: [docs/superpowers/plans/2026-05-07-issue-16-bus-tail-audit-feed.md](docs/superpowers/plans/2026-05-07-issue-16-bus-tail-audit-feed.md)

## Test plan

- [x] Unit: 9 summarizer tests (registry covers every envelope kind, value-free shapes)
- [x] Unit: 17 PersistenceReader tests (filters, pagination, clamping, metrics, percentiles)
- [x] Unit: 2 BusHandle.persistence() lifecycle tests
- [x] Unit: 6 BusTailMCPEndpoint lifecycle tests
- [x] Integration: 8 FastMCP in-memory client tests for the four tools
- [x] Integration: 4 runner-from-yaml tests (registration, mount, default-omitted, default-mount)
- [x] Manual smoke: daemon serves `/mcp/bus-tail/` end-to-end
- [x] No regressions in existing tests

Closes #16
EOF
)"
```

- [ ] **Step 5: Capture the PR URL and report**

Once the PR is open, report the URL back. The implementation plan is complete.
