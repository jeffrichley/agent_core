# Responsive Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `ClaudeCodeMCPEndpoint`'s polling-only inbox with push-based wake notifications, urgency-aware mailbox ordering, and same-sender batching at read time.

**Architecture:** Three coordinated changes on the consumer side: (1) add `urgency` as a top-level field on `Envelope` and migrate the persistence column; (2) replace the existing `_SessionTracker` middleware with a `SessionRegistry` that captures the live `ServerSession` reference via FastMCP's `_subscription_task_group` pattern (mirrored from official `PingMiddleware`); (3) push `notifications/claude/channel` summaries via that captured session, with 50ms debounce to coalesce bursts. Polling via `list_pending` remains authoritative — push is best-effort.

**Tech Stack:** Python 3.12, FastMCP 3.x (middleware + session task group), MCP Python SDK (`ServerSession.send_message`), Pydantic v2, aiosqlite (schema migration), pytest + pytest-asyncio, anyio (matches FastMCP's primitives).

**Spec:** `docs/superpowers/specs/2026-04-29-responsive-inbox-design.md`

**Branch:** `feat/responsive-inbox`

---

## File Structure

**Modified:**
- `packages/core/src/agent_core/bus/envelope.py` — add `urgency` field
- `packages/core/src/agent_core/bus/persistence.py` — schema + ALTER TABLE migration; insert + roundtrip handling
- `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — replace `_SessionTracker` with `SessionRegistry`; rewrite `_notify_mail_arrived`; add debounce; update `instructions=`; sort + batch in `list_pending`
- `packages/agent-core-discord/src/agent_core_discord/endpoint.py` — apply urgency-red regex rule on inbound TextMessage publish
- `packages/agent-core-discord/src/agent_core_discord/access.py` — `AccessConfig` grows optional `urgency_red_regex` field

**New tests:**
- `packages/core/tests/test_envelope_urgency.py` — schema + default behavior
- `packages/core/tests/test_persistence_urgency_migration.py` — ALTER TABLE on existing DB; roundtrip
- `packages/core/tests/test_claude_code_mcp_urgency_ordering.py` — list_pending sort
- `packages/core/tests/test_claude_code_mcp_batching.py` — list_pending batch_window_seconds
- `packages/core/tests/test_session_registry.py` — middleware + registry mechanics
- `packages/core/tests/test_notify_mail_arrived.py` — push + debounce + failure-mode behaviors
- `packages/core/tests/test_bus_daemon_push_integration.py` — real `mcp` Client receives push
- `packages/agent-core-discord/tests/test_endpoint_urgency.py` — Discord regex rule

---

## Task ordering and rationale

1. **Schema first** (Tasks 1, 2). The `urgency` field touches every producer and consumer; everything else builds on it. SQLite migration must be in place before any test inserts envelopes.
2. **Read-side behavior** (Tasks 3, 4). Sort + batch at `list_pending` are independent of push and self-contained — easy to validate in unit tests with synthetic mailbox state.
3. **Session registry** (Task 5). Replaces the existing `_SessionTracker`. No external behavior change yet — just the foundation for push.
4. **Push notifications** (Task 6). Brings everything together; uses the schema (urgency in summary), the registry (session ref), and ties to the agent via instructions text.
5. **Producer rule** (Task 7). Discord endpoint applies the red-urgency regex.
6. **Integration + validation** (Tasks 8, 9). Real-MCP-client integration test, then live testbot 5-step validation.

Each task is one focused commit. Tests-first throughout.

---

## Task 1: Add `urgency` field to Envelope model

**Files:**
- Modify: `packages/core/src/agent_core/bus/envelope.py`
- Test: `packages/core/tests/test_envelope_urgency.py` (new)

- [ ] **Step 1: Write the failing test file**

Create `packages/core/tests/test_envelope_urgency.py`:

```python
"""Tests for the urgency field on Envelope."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_core.bus.envelope import Envelope, TextMessagePayload


def _make_envelope(**overrides):
    base = dict(
        id="e1",
        correlation_id="c1",
        from_="src",
        to="dst",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return Envelope(**base)


def test_envelope_urgency_defaults_to_green():
    env = _make_envelope()
    assert env.urgency == "green"


def test_envelope_urgency_accepts_yellow_and_red():
    assert _make_envelope(urgency="yellow").urgency == "yellow"
    assert _make_envelope(urgency="red").urgency == "red"


def test_envelope_urgency_rejects_unknown_value():
    with pytest.raises(ValidationError):
        _make_envelope(urgency="blue")


def test_envelope_urgency_rejects_empty_string():
    with pytest.raises(ValidationError):
        _make_envelope(urgency="")


def test_envelope_urgency_roundtrips_via_json():
    env = _make_envelope(urgency="red")
    payload = env.model_dump_json()
    restored = Envelope.model_validate_json(payload)
    assert restored.urgency == "red"


def test_envelope_urgency_default_when_absent_from_input_json():
    """Old persisted rows won't carry the field — Pydantic must use the default."""
    raw = {
        "id": "e1",
        "correlation_id": "c1",
        "from": "src",
        "to": "dst",
        "kind": "TextMessage",
        "payload": {"kind": "TextMessage", "text": "hi"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    env = Envelope.model_validate(raw)
    assert env.urgency == "green"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_envelope_urgency.py -v`
Expected: 6 errors, all `AttributeError` or `ValidationError` complaining the model has no `urgency` field.

- [ ] **Step 3: Add the field**

Modify `packages/core/src/agent_core/bus/envelope.py`. Find the `Envelope` class. Add `urgency` between `metadata` and `expires_at`:

```python
class Envelope(BaseModel):
    """The bus's universal wire format."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    correlation_id: str
    in_reply_to: str | None = None
    from_: str = Field(default="", alias="from")
    to: str
    kind: Literal[
        "TextMessage",
        "Event",
        "ToolInvocation",
        "Cancellation",
        "Progress",
        "Acknowledgment",
    ]
    payload: EnvelopePayload
    metadata: dict[str, Any] = Field(default_factory=dict)
    urgency: Literal["green", "yellow", "red"] = "green"
    expires_at: datetime | None = None
    created_at: datetime

    @model_validator(mode="after")
    def validate_kind_matches_payload(self) -> "Envelope":
        ...  # unchanged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_envelope_urgency.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run the full core suite to confirm no regressions**

Run: `uv run pytest packages/core/tests/ -v`
Expected: all existing tests still pass + 6 new from this task.

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check packages/core/ && uv run ruff format packages/core/`
Expected: All checks passed; format unchanged or minor reformat applied.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/agent_core/bus/envelope.py packages/core/tests/test_envelope_urgency.py
git commit -m "feat(envelope): add urgency field (green|yellow|red, default green)"
```

---

## Task 2: Persist urgency column with ALTER TABLE migration

**Files:**
- Modify: `packages/core/src/agent_core/bus/persistence.py`
- Test: `packages/core/tests/test_persistence_urgency_migration.py` (new)

The schema has a column-per-field pattern; Envelope's new `urgency` field needs a column. Existing test/operator DBs (e.g., `~/.agent-core/bus.sqlite` from prior PRs) need a migration. SQLite supports `ALTER TABLE ADD COLUMN` with `DEFAULT 'green'` cleanly.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_persistence_urgency_migration.py`:

```python
"""Tests that the persistence layer roundtrips urgency and migrates legacy DBs."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.persistence import Persistence


def _make_envelope(eid: str, urgency: str = "green") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="src",
        to="dst",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        urgency=urgency,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_persistence_roundtrips_urgency_field(tmp_path):
    db = tmp_path / "bus.sqlite"
    p = Persistence(db)
    await p.connect()
    try:
        env_red = _make_envelope("r1", urgency="red")
        env_yellow = _make_envelope("y1", urgency="yellow")
        env_green = _make_envelope("g1")  # default green
        await p.insert(env_red)
        await p.insert(env_yellow)
        await p.insert(env_green)
        rows = await p.list_pending("dst")
        by_id = {e.id: e for e in rows}
        assert by_id["r1"].urgency == "red"
        assert by_id["y1"].urgency == "yellow"
        assert by_id["g1"].urgency == "green"
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_persistence_alter_table_migrates_legacy_db(tmp_path):
    """A DB written before the urgency column existed should be migrated by connect()."""
    db = tmp_path / "legacy.sqlite"
    # Create a legacy schema by hand — same as today's _SCHEMA but without urgency.
    legacy_schema = """
    CREATE TABLE envelopes (
        id TEXT PRIMARY KEY,
        correlation_id TEXT NOT NULL,
        in_reply_to TEXT,
        from_endpoint TEXT NOT NULL,
        to_endpoint TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        expires_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending',
        delivery_count INTEGER NOT NULL DEFAULT 0,
        last_attempted TIMESTAMP,
        in_flight_until TIMESTAMP,
        nack_reason TEXT
    );
    """
    conn = sqlite3.connect(db)
    conn.executescript(legacy_schema)
    # Insert a row with no urgency.
    conn.execute(
        """INSERT INTO envelopes(id, correlation_id, from_endpoint, to_endpoint,
            kind, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            "legacy-1",
            "c1",
            "src",
            "dst",
            "TextMessage",
            json.dumps({"kind": "TextMessage", "text": "hi"}),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    # Now open with current Persistence — connect() must migrate.
    p = Persistence(db)
    await p.connect()
    try:
        rows = await p.list_pending("dst")
        assert len(rows) == 1
        assert rows[0].id == "legacy-1"
        # Default urgency on rows that pre-date the column.
        assert rows[0].urgency == "green"
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_persistence_alter_table_idempotent(tmp_path):
    """Running connect() twice on a freshly-migrated DB must not raise."""
    db = tmp_path / "bus.sqlite"
    p1 = Persistence(db)
    await p1.connect()
    await p1.close()
    # Reconnect — the ALTER TABLE step must skip (column already exists).
    p2 = Persistence(db)
    await p2.connect()
    await p2.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_persistence_urgency_migration.py -v`
Expected: failures around missing `urgency` column / `OperationalError: no such column: urgency`.

- [ ] **Step 3: Update the schema and add the migration step**

Modify `packages/core/src/agent_core/bus/persistence.py`. Update `_SCHEMA` to include the column (for fresh DBs):

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS envelopes (
    id              TEXT PRIMARY KEY,
    correlation_id  TEXT NOT NULL,
    in_reply_to     TEXT,
    from_endpoint   TEXT NOT NULL,
    to_endpoint     TEXT NOT NULL,
    kind            TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    urgency         TEXT NOT NULL DEFAULT 'green'
        CHECK (urgency IN ('green','yellow','red')),
    expires_at      TIMESTAMP,
    created_at      TIMESTAMP NOT NULL,

    state           TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending','in_flight','acked','dead_letter','expired')),
    delivery_count  INTEGER NOT NULL DEFAULT 0,
    last_attempted  TIMESTAMP,
    in_flight_until TIMESTAMP,
    nack_reason     TEXT
);

CREATE INDEX IF NOT EXISTS idx_envelopes_to_state
    ON envelopes(to_endpoint, state, created_at);
CREATE INDEX IF NOT EXISTS idx_envelopes_correlation
    ON envelopes(correlation_id);
CREATE INDEX IF NOT EXISTS idx_envelopes_expires
    ON envelopes(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_envelopes_in_flight
    ON envelopes(in_flight_until) WHERE state='in_flight';
"""
```

Update `_row_to_envelope` to pass urgency through:

```python
def _row_to_envelope(row: dict[str, Any]) -> Envelope:
    return Envelope.model_validate(
        {
            "id": row["id"],
            "correlation_id": row["correlation_id"],
            "in_reply_to": row["in_reply_to"],
            "from": row["from_endpoint"],
            "to": row["to_endpoint"],
            "kind": row["kind"],
            "payload": json.loads(row["payload_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "urgency": row["urgency"] if "urgency" in row.keys() else "green",
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
        }
    )
```

In `Persistence.connect()`, after running `_SCHEMA`, add an idempotent ALTER for legacy DBs:

```python
async def connect(self) -> None:
    self.path.parent.mkdir(parents=True, exist_ok=True)
    existed = self.path.exists()
    self._conn = await aiosqlite.connect(self.path)
    self._conn.row_factory = aiosqlite.Row
    await self._conn.executescript(_SCHEMA)
    # Migrate legacy DBs that pre-date the urgency column. Using a generic
    # PRAGMA-driven check keeps this idempotent without try/except OperationalError.
    cur = await self._conn.execute("PRAGMA table_info(envelopes)")
    cols = {row["name"] async for row in cur}
    await cur.close()
    if "urgency" not in cols:
        await self._conn.execute(
            "ALTER TABLE envelopes ADD COLUMN urgency TEXT NOT NULL DEFAULT 'green'"
        )
        await self._conn.commit()
    # ... rest of connect() unchanged
```

Find the `insert` method and add `urgency` to the column list and parameter tuple:

```python
async def insert(self, envelope: Envelope) -> None:
    assert self._conn is not None
    await self._conn.execute(
        """INSERT INTO envelopes(
            id, correlation_id, in_reply_to,
            from_endpoint, to_endpoint, kind,
            payload_json, metadata_json, urgency,
            expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            envelope.id,
            envelope.correlation_id,
            envelope.in_reply_to,
            envelope.from_,
            envelope.to,
            envelope.kind,
            envelope.payload.model_dump_json(),
            json.dumps(envelope.metadata),
            envelope.urgency,
            envelope.expires_at,
            envelope.created_at,
        ),
    )
    await self._conn.commit()
```

(If the existing `insert` body differs in detail, preserve the existing structure and only add the urgency column + parameter.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_persistence_urgency_migration.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run all persistence tests**

Run: `uv run pytest packages/core/tests/ -k persistence -v`
Expected: all existing tests still pass + 3 new.

- [ ] **Step 6: Run the full core suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: green.

- [ ] **Step 7: Migrate the live `~/.agent-core/bus.sqlite`**

The dev DB will auto-migrate on next daemon start (via the new ALTER step), but verify the migration runs without error. With the daemon stopped:

Run:
```bash
uv run python -c "
import asyncio
from pathlib import Path
from agent_core.bus.persistence import Persistence
async def main():
    p = Persistence(Path.home() / '.agent-core' / 'bus.sqlite')
    await p.connect()
    await p.close()
    print('migration ok')
asyncio.run(main())
"
```
Expected: `migration ok` printed; no exceptions.

- [ ] **Step 8: Lint and format**

Run: `uv run ruff check packages/core/ && uv run ruff format packages/core/`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add packages/core/src/agent_core/bus/persistence.py packages/core/tests/test_persistence_urgency_migration.py
git commit -m "feat(persistence): add urgency column with ALTER TABLE migration"
```

---

## Task 3: Sort `list_pending` by urgency tier

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py`
- Test: `packages/core/tests/test_claude_code_mcp_urgency_ordering.py` (new)

`ClaudeCodeMCPEndpoint.list_pending` is the MCP tool the agent calls to read its mailbox. The mailbox is `self._pending: list[Envelope]` (a list, ordered by insertion). Sort the *returned* view by urgency tier (red, yellow, green) — FIFO within tier, breaking ties by `created_at`.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_claude_code_mcp_urgency_ordering.py`:

```python
"""list_pending returns envelopes sorted by urgency tier, FIFO within tier."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


def _env(eid: str, urgency: str, age_seconds: int) -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="src",
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text="x"),
        urgency=urgency,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )


def _ids(rows: list[dict]) -> list[str]:
    return [row["id"] for row in rows]


def _make_endpoint() -> ClaudeCodeMCPEndpoint:
    return ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")


@pytest.mark.asyncio
async def test_list_pending_red_first_then_yellow_then_green():
    ep = _make_endpoint()
    ep._pending = [
        _env("g1", "green", age_seconds=10),
        _env("y1", "yellow", age_seconds=8),
        _env("r1", "red", age_seconds=6),
        _env("g2", "green", age_seconds=4),
    ]
    rows = await ep._call_list_pending()
    assert _ids(rows) == ["r1", "y1", "g1", "g2"]


@pytest.mark.asyncio
async def test_list_pending_fifo_within_same_tier():
    ep = _make_endpoint()
    ep._pending = [
        _env("r1", "red", age_seconds=30),  # oldest
        _env("r2", "red", age_seconds=20),
        _env("r3", "red", age_seconds=10),  # newest
    ]
    rows = await ep._call_list_pending()
    assert _ids(rows) == ["r1", "r2", "r3"]


@pytest.mark.asyncio
async def test_list_pending_tier_wins_over_arrival_time():
    """A newer red beats an older green."""
    ep = _make_endpoint()
    ep._pending = [
        _env("g_old", "green", age_seconds=100),
        _env("r_new", "red", age_seconds=1),
    ]
    rows = await ep._call_list_pending()
    assert _ids(rows) == ["r_new", "g_old"]


@pytest.mark.asyncio
async def test_list_pending_empty_returns_empty():
    ep = _make_endpoint()
    rows = await ep._call_list_pending()
    assert rows == []
```

The tests call `ep._call_list_pending()` because the `list_pending` MCP tool is wrapped by FastMCP and not directly addressable. Add a small private async helper that the MCP tool delegates to. This keeps the sort logic testable without spinning up a FastMCP test client.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_claude_code_mcp_urgency_ordering.py -v`
Expected: 4 errors, all `AttributeError: 'ClaudeCodeMCPEndpoint' object has no attribute '_call_list_pending'`.

- [ ] **Step 3: Refactor `list_pending` to delegate to the testable helper, with sort**

In `packages/core/src/agent_core/endpoints/claude_code_mcp.py`, find `_register_tools` and the inner `list_pending` function. Add a class-level helper `_call_list_pending` and have the MCP tool wrapper call it. Keep all existing field shaping intact:

```python
class ClaudeCodeMCPEndpoint:
    # ... existing __init__ unchanged for now ...

    _URGENCY_RANK = {"red": 0, "yellow": 1, "green": 2}

    async def _call_list_pending(self) -> list[dict]:
        """Sorted by (urgency_rank, created_at) — red first, FIFO within tier."""
        sorted_pending = sorted(
            self._pending,
            key=lambda e: (self._URGENCY_RANK[e.urgency], e.created_at),
        )
        return [self._envelope_to_dict(env) for env in sorted_pending]

    @staticmethod
    def _envelope_to_dict(env: Envelope) -> dict:
        return {
            "id": env.id,
            "from": env.from_,
            "to": env.to,
            "kind": env.kind,
            "correlation_id": env.correlation_id,
            "in_reply_to": env.in_reply_to,
            "payload": env.payload.model_dump(),
            "metadata": env.metadata,
            "urgency": env.urgency,
            "created_at": env.created_at.isoformat(),
        }
```

In `_register_tools`, replace the body of the `list_pending` MCP tool function:

```python
        @self._mcp.tool()
        async def list_pending() -> list[dict]:
            """Return a snapshot of envelopes in this agent's pickup queue,
            sorted by urgency (red first, then yellow, then green) with FIFO
            within tier."""
            return await self._call_list_pending()
```

(The previous body that hand-built the dict from `self._pending` directly goes away — `_call_list_pending` and `_envelope_to_dict` are the new home.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_claude_code_mcp_urgency_ordering.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run the full core suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: green; no regressions in existing list_pending tests (they should continue to pass; envelope shape is unchanged, only ordering and the inclusion of the new `urgency` key).

If any existing test asserts on the field set returned by `list_pending` and now fails because `urgency` is in the dict, update those assertions to allow the new field (don't remove it).

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check packages/core/ && uv run ruff format packages/core/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/tests/test_claude_code_mcp_urgency_ordering.py
git commit -m "feat(claude-mcp): sort list_pending by urgency tier (red → yellow → green)"
```

---

## Task 4: Same-sender batching at `list_pending`

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py`
- Test: `packages/core/tests/test_claude_code_mcp_batching.py` (new)

Add a parameter `batch_window_seconds: int = 0` to the `list_pending` MCP tool. When `> 0`, consecutive envelopes (after urgency sort) from the same `from_` whose `created_at` are within the window collapse into one batched group. Default 0 preserves today's flat-list behavior.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_claude_code_mcp_batching.py`:

```python
"""list_pending optionally batches consecutive same-sender envelopes within window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


def _env(eid: str, frm: str, age_seconds: int, urgency: str = "green") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_=frm,
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=eid),
        urgency=urgency,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )


@pytest.mark.asyncio
async def test_list_pending_batch_window_zero_returns_flat_list():
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    ep._pending = [
        _env("a", "alice", age_seconds=10),
        _env("b", "alice", age_seconds=8),
        _env("c", "alice", age_seconds=6),
    ]
    rows = await ep._call_list_pending(batch_window_seconds=0)
    # Default 0: no batching — flat list of envelope dicts.
    assert all(row.get("type") in (None, "single") or "envelope" not in row for row in rows)
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_list_pending_batches_three_same_sender_within_window():
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    ep._pending = [
        _env("a", "alice", age_seconds=20),
        _env("b", "alice", age_seconds=10),
        _env("c", "alice", age_seconds=5),
    ]
    rows = await ep._call_list_pending(batch_window_seconds=30)
    assert len(rows) == 1
    group = rows[0]
    assert group["type"] == "batch"
    assert group["from"] == "alice"
    assert group["kind"] == "TextMessage"
    assert [e["id"] for e in group["envelopes"]] == ["a", "b", "c"]
    assert group["total_age_seconds"] >= 15  # spans a → c


@pytest.mark.asyncio
async def test_list_pending_does_not_batch_different_senders():
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    ep._pending = [
        _env("a", "alice", age_seconds=10),
        _env("b", "bob", age_seconds=8),
        _env("c", "alice", age_seconds=6),
    ]
    rows = await ep._call_list_pending(batch_window_seconds=30)
    assert len(rows) == 3
    assert all(r["type"] == "single" for r in rows)
    assert [r["envelope"]["id"] for r in rows] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_list_pending_batches_only_within_window():
    """Two old, one fresh — old two batch together; fresh one does not."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    ep._pending = [
        _env("a", "alice", age_seconds=120),
        _env("b", "alice", age_seconds=110),
        _env("c", "alice", age_seconds=5),
    ]
    rows = await ep._call_list_pending(batch_window_seconds=30)
    assert len(rows) == 2
    assert rows[0]["type"] == "batch"
    assert [e["id"] for e in rows[0]["envelopes"]] == ["a", "b"]
    assert rows[1]["type"] == "single"
    assert rows[1]["envelope"]["id"] == "c"


@pytest.mark.asyncio
async def test_list_pending_batches_respect_urgency_grouping():
    """Different urgency tiers do NOT batch together even from same sender."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    ep._pending = [
        _env("r1", "alice", age_seconds=10, urgency="red"),
        _env("g1", "alice", age_seconds=5, urgency="green"),
    ]
    rows = await ep._call_list_pending(batch_window_seconds=30)
    # Red comes first (tier sort), green second; they're not in the same group
    # because urgency differs.
    assert len(rows) == 2
    assert rows[0]["type"] == "single"
    assert rows[0]["envelope"]["id"] == "r1"
    assert rows[1]["type"] == "single"
    assert rows[1]["envelope"]["id"] == "g1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_claude_code_mcp_batching.py -v`
Expected: 5 errors. The current `_call_list_pending` doesn't accept `batch_window_seconds`.

- [ ] **Step 3: Update `_call_list_pending` and the wrapping MCP tool**

In `packages/core/src/agent_core/endpoints/claude_code_mcp.py`, replace `_call_list_pending`:

```python
    async def _call_list_pending(self, batch_window_seconds: int = 0) -> list[dict]:
        """Mailbox view sorted by urgency, optionally batched by sender.

        When batch_window_seconds == 0: returns a flat list of envelope dicts
        (today's behavior). When > 0: consecutive envelopes (within urgency
        tier and same `from_`) whose created_at fall within the window collapse
        into one {"type": "batch", ...} entry; standalone entries are wrapped
        as {"type": "single", "envelope": {...}}.
        """
        sorted_pending = sorted(
            self._pending,
            key=lambda e: (self._URGENCY_RANK[e.urgency], e.created_at),
        )
        if batch_window_seconds <= 0:
            return [self._envelope_to_dict(env) for env in sorted_pending]

        from datetime import timedelta

        window = timedelta(seconds=batch_window_seconds)
        groups: list[dict] = []
        i = 0
        while i < len(sorted_pending):
            head = sorted_pending[i]
            j = i + 1
            run = [head]
            while j < len(sorted_pending):
                cand = sorted_pending[j]
                if (
                    cand.from_ == head.from_
                    and cand.urgency == head.urgency
                    and cand.kind == head.kind
                    and (cand.created_at - run[-1].created_at) <= window
                ):
                    run.append(cand)
                    j += 1
                else:
                    break
            if len(run) == 1:
                groups.append({"type": "single", "envelope": self._envelope_to_dict(head)})
            else:
                first_arrival = run[0].created_at
                last_arrival = run[-1].created_at
                groups.append(
                    {
                        "type": "batch",
                        "from": head.from_,
                        "kind": head.kind,
                        "urgency": head.urgency,
                        "envelopes": [self._envelope_to_dict(e) for e in run],
                        "first_arrival": first_arrival.isoformat(),
                        "total_age_seconds": int((last_arrival - first_arrival).total_seconds()),
                    }
                )
            i = j
        return groups
```

In `_register_tools`, update the MCP tool to accept the parameter:

```python
        @self._mcp.tool()
        async def list_pending(batch_window_seconds: int = 0) -> list[dict]:
            """Return a snapshot of envelopes in this agent's pickup queue,
            sorted by urgency (red → yellow → green) with FIFO within tier.

            When batch_window_seconds > 0, consecutive same-sender same-urgency
            same-kind envelopes whose arrival times fall within the window are
            collapsed into a single {"type": "batch", ...} entry. Each
            underlying envelope retains its own id and ack semantics — call
            handle(envelope_id) per envelope.
            """
            return await self._call_list_pending(batch_window_seconds=batch_window_seconds)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_claude_code_mcp_batching.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run the full core suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: green.

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check packages/core/ && uv run ruff format packages/core/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/tests/test_claude_code_mcp_batching.py
git commit -m "feat(claude-mcp): same-sender batching at list_pending (batch_window_seconds param)"
```

---

## Task 5: Replace `_SessionTracker` with `SessionRegistry` middleware

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py`
- Test: `packages/core/tests/test_session_registry.py` (new)

Replace the existing `_SessionTracker` (which only flips a flag on initialize) with `SessionRegistry`, which captures the live `ServerSession` ref via the FastMCP `_subscription_task_group` pattern (mirrored from `PingMiddleware`'s implementation). Keeps the existing `_session_active` flag for the `EndpointUnavailable` raise but adds a typed session reference for push.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_session_registry.py`:

```python
"""Session registry middleware: captures and releases the active ServerSession."""

from __future__ import annotations

import anyio
import pytest

from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _FakeTaskGroup:
    """Mimics anyio.TaskGroup just enough for the middleware test."""

    def __init__(self):
        self.spawned: list[tuple] = []

    def start_soon(self, fn, *args, name: str | None = None) -> None:
        # Capture; we'll drive it by hand in tests.
        self.spawned.append((fn, args))


class _FakeSession:
    def __init__(self):
        self._subscription_task_group = _FakeTaskGroup()
        # Test helper to run the spawned _claim_session body manually.

    @property
    def task_group(self) -> _FakeTaskGroup:
        return self._subscription_task_group


class _FakeFastMCPContext:
    def __init__(self, session: _FakeSession):
        self.session = session
        self.request_context = object()  # truthy


class _FakeMiddlewareContext:
    def __init__(self, session: _FakeSession):
        self.fastmcp_context = _FakeFastMCPContext(session)


@pytest.mark.asyncio
async def test_register_session_captures_reference():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _FakeSession()
    ep._register_session(session)
    assert ep._active_session is session
    assert ep._session_active is True


@pytest.mark.asyncio
async def test_unregister_session_clears_reference():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _FakeSession()
    ep._register_session(session)
    ep._unregister_session(session)
    assert ep._active_session is None
    assert ep._session_active is False


@pytest.mark.asyncio
async def test_unregister_session_only_clears_if_same_session():
    """Defensive: unregistering session A while session B is active does NOT clear B."""
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session_a = _FakeSession()
    session_b = _FakeSession()
    ep._register_session(session_a)
    ep._unregister_session(session_b)  # different session — should be a no-op
    assert ep._active_session is session_a
    assert ep._session_active is True


@pytest.mark.asyncio
async def test_register_second_concurrent_session_raises():
    """Collision guard: second session with no prior unregister is refused."""
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session_a = _FakeSession()
    session_b = _FakeSession()
    ep._register_session(session_a)
    with pytest.raises(RuntimeError, match="already has an active session"):
        ep._register_session(session_b)
    # Original is preserved.
    assert ep._active_session is session_a


@pytest.mark.asyncio
async def test_register_same_session_twice_is_idempotent():
    """Re-registering the SAME session ref is a no-op (same identity)."""
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _FakeSession()
    ep._register_session(session)
    ep._register_session(session)  # no raise
    assert ep._active_session is session
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_session_registry.py -v`
Expected: 5 errors, all `AttributeError`-class on `_register_session` / `_unregister_session` / `_active_session`.

- [ ] **Step 3: Add the registry methods and replace `_SessionTracker`**

In `packages/core/src/agent_core/endpoints/claude_code_mcp.py`:

Replace the entire `_SessionTracker` class with `SessionRegistry`:

```python
class SessionRegistry(Middleware):
    """Middleware that captures the connected ServerSession on first message.

    Mirrors FastMCP's official PingMiddleware pattern: spawn a long-lived
    coroutine into session._subscription_task_group; the coroutine registers
    the session with the endpoint, awaits forever, and runs cleanup in
    finally: when the session task group is cancelled (which fires when the
    SSE stream closes).
    """

    def __init__(self, endpoint: "ClaudeCodeMCPEndpoint") -> None:
        self._endpoint = endpoint
        self._known: set[int] = set()
        self._lock = anyio.Lock()

    async def on_message(self, context: MiddlewareContext, call_next) -> Any:
        if (
            context.fastmcp_context is None
            or context.fastmcp_context.request_context is None
        ):
            return await call_next(context)

        session = context.fastmcp_context.session
        sid = id(session)

        async with self._lock:
            if sid not in self._known:
                tg = getattr(session, "_subscription_task_group", None)
                if tg is not None:
                    self._known.add(sid)
                    tg.start_soon(self._claim_session, session, sid)

        return await call_next(context)

    async def _claim_session(self, session, sid: int) -> None:
        try:
            self._endpoint._register_session(session)
            await anyio.sleep_forever()
        except RuntimeError:
            # Collision rejected — caller already holds the slot. Do not
            # cancel the existing one; just exit this lifetime task.
            log.warning(
                "endpoint '%s': refused concurrent session %d (slot held)",
                self._endpoint.name,
                sid,
            )
        finally:
            self._endpoint._unregister_session(session)
            self._known.discard(sid)
```

Update imports at the top of the file. Add `import anyio` if not present.

In `ClaudeCodeMCPEndpoint.__init__`:

```python
    def __init__(self, *, name: str, mount: str):
        self.name = name
        self.mount = mount
        self._mcp: FastMCP = FastMCP(name)
        self._handle: "BusHandle | None" = None
        self._pending: list[Envelope] = []
        self._session_active: bool = False
        self._active_session: Any = None  # ServerSession, when connected
        self._mcp.add_middleware(SessionRegistry(self))
        self._register_tools()
```

Add the new methods on `ClaudeCodeMCPEndpoint`:

```python
    def _register_session(self, session: Any) -> None:
        """Capture the active ServerSession; refuse a different concurrent one."""
        if self._active_session is not None and self._active_session is not session:
            raise RuntimeError(
                f"endpoint '{self.name}' already has an active session; "
                f"refusing concurrent connection"
            )
        self._active_session = session
        self._session_active = True
        log.debug("endpoint '%s' captured active session", self.name)

    def _unregister_session(self, session: Any) -> None:
        """Clear the slot if the session matches the one we hold."""
        if self._active_session is session:
            self._active_session = None
            self._session_active = False
            log.debug("endpoint '%s' released active session", self.name)
```

In `stop()`, ensure cleanup zeroes both fields:

```python
    async def stop(self) -> None:
        self._handle = None
        self._session_active = False
        self._active_session = None
        log.info("ClaudeCodeMCPEndpoint(name=%s) stopped", self.name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_session_registry.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run the full core suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: green. The existing `_SessionTracker` was tested via the bus-daemon integration test and the `_session_active` semantics; nothing should break since `_session_active` is still flipped to True on first message via the new `SessionRegistry` (which calls `_register_session` → flip).

If the `bus-daemon-integration` test fails (it likely uses real HTTP), inspect and adjust — the initial-message → flag-flip behavior should still hold.

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check packages/core/ && uv run ruff format packages/core/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/tests/test_session_registry.py
git commit -m "refactor(claude-mcp): replace _SessionTracker with SessionRegistry capturing live session ref"
```

---

## Task 6: Real push notifications — `_notify_mail_arrived` + send tool urgency + instructions

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py`
- Test: `packages/core/tests/test_notify_mail_arrived.py` (new)
- Test: `packages/core/tests/test_send_tool_urgency.py` (new)

Replace the no-op `_notify_mail_arrived` with a real push that:
1. Returns silently if no session connected (mailbox is authoritative; agent picks up via `list_pending` on next connect).
2. Debounces: 50ms coalescing window so a burst of arrivals fires one summary.
3. Builds a summary describing current pending state (count, urgency_max, by_sender).
4. Sends `notifications/claude/channel` via the captured session.
5. On send failure, clears `_active_session` and logs WARN. No retry; the envelope is in the mailbox.
6. The endpoint's `instructions=` text is updated to teach the agent to call `list_pending` on receipt of the notification.
7. The `send` MCP tool gains an optional `urgency: Literal["green", "yellow", "red"] = "green"` parameter so agents can mark outbound envelopes (otherwise an agent could read inbound urgency but never set it on its own publishes — Task 1 added the schema field but didn't wire it into the publishing surface).

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_notify_mail_arrived.py`:

```python
"""Tests for _notify_mail_arrived push behavior: debounce, summary shape, failures."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _RecordingSession:
    """Minimal stand-in for ServerSession that records send_message calls."""

    def __init__(self, fail_with: Exception | None = None):
        self.sent: list[Any] = []
        self._fail_with = fail_with

    async def send_message(self, message) -> None:
        if self._fail_with is not None:
            raise self._fail_with
        self.sent.append(message)


def _env(eid: str, frm: str = "src", urgency: str = "green") -> Envelope:
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


def _extract_method(message) -> str:
    """Pull the JSON-RPC method off the SessionMessage."""
    return message.message.root.method


def _extract_params(message) -> dict:
    return message.message.root.params


@pytest.mark.asyncio
async def test_notify_drops_silently_when_no_session():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    # No session registered.
    await ep._notify_mail_arrived("e1")
    # Drain any debounce task so we don't hold a pending coroutine.
    await asyncio.sleep(0.1)
    # No assertion to make — must not raise.


@pytest.mark.asyncio
async def test_notify_pushes_summary_when_session_active():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [_env("e1", urgency="green")]

    await ep._notify_mail_arrived("e1")
    await asyncio.sleep(0.1)  # let debounce fire

    assert len(session.sent) == 1
    assert _extract_method(session.sent[0]) == "notifications/claude/channel"
    params = _extract_params(session.sent[0])
    assert "content" in params
    assert "meta" in params
    assert params["meta"]["count"] == 1
    assert params["meta"]["endpoint"] == "a"
    assert params["meta"]["urgency_max"] == "green"
    assert params["meta"]["urgency_counts"] == {"red": 0, "yellow": 0, "green": 1}


@pytest.mark.asyncio
async def test_notify_debounces_burst_into_one_push():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [_env(f"e{i}") for i in range(3)]

    # Fire three arrivals back-to-back.
    await ep._notify_mail_arrived("e0")
    await ep._notify_mail_arrived("e1")
    await ep._notify_mail_arrived("e2")
    await asyncio.sleep(0.1)  # let debounce fire

    assert len(session.sent) == 1
    params = _extract_params(session.sent[0])
    assert params["meta"]["count"] == 3


@pytest.mark.asyncio
async def test_notify_summary_reports_max_urgency():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [
        _env("e1", urgency="green"),
        _env("e2", urgency="yellow"),
        _env("e3", urgency="red"),
    ]
    await ep._notify_mail_arrived("e3")
    await asyncio.sleep(0.1)

    params = _extract_params(session.sent[0])
    assert params["meta"]["urgency_max"] == "red"
    assert params["meta"]["urgency_counts"] == {"red": 1, "yellow": 1, "green": 1}


@pytest.mark.asyncio
async def test_notify_summary_groups_by_sender():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [
        _env("e1", frm="alice"),
        _env("e2", frm="alice"),
        _env("e3", frm="bob"),
    ]
    await ep._notify_mail_arrived("e1")
    await asyncio.sleep(0.1)

    params = _extract_params(session.sent[0])
    by_sender = {entry["from"]: entry["count"] for entry in params["meta"]["by_sender"]}
    assert by_sender == {"alice": 2, "bob": 1}


@pytest.mark.asyncio
async def test_notify_clears_session_slot_on_send_failure():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _RecordingSession(fail_with=ConnectionError("stream closed"))
    ep._register_session(session)
    ep._pending = [_env("e1")]

    await ep._notify_mail_arrived("e1")
    await asyncio.sleep(0.1)

    # Slot must have been cleared so future deliveries fall back to polling.
    assert ep._active_session is None
    assert ep._session_active is False


@pytest.mark.asyncio
async def test_endpoint_instructions_describe_notifications():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    instructions = ep._mcp.instructions or ""
    # Must contain the notification namespace and what to do.
    assert "notifications/claude/channel" in instructions
    assert "list_pending" in instructions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_notify_mail_arrived.py -v`
Expected: most tests fail. Some `AttributeError`-flavored on missing helpers; the no-session case may pass since the current `_notify_mail_arrived` does nothing.

- [ ] **Step 3: Implement `_notify_mail_arrived` with debounce, summary builder, send wrapper**

In `packages/core/src/agent_core/endpoints/claude_code_mcp.py`:

Add to top of file imports if missing:

```python
import asyncio
from collections import Counter
from typing import Any

import mcp.types
from mcp.shared.session import SessionMessage  # for wrapping the JSONRPCNotification
from mcp.types import JSONRPCMessage, JSONRPCNotification
```

Add to `__init__`:

```python
        self._notify_debounce_seconds: float = 0.05
        self._debounce_task: asyncio.Task | None = None
```

Update FastMCP construction in `__init__` to include the new instructions text:

```python
        self._mcp: FastMCP = FastMCP(
            name,
            instructions=(
                "You are agent '{name}'. The bus pushes you notifications with method "
                '"notifications/claude/channel" when envelopes arrive in your mailbox. '
                'Each notification\'s params contain "content" (a brief summary) and '
                '"meta" (count, urgency_max, urgency_counts, by_sender, endpoint, '
                'fired_at). On receipt: call list_pending() to read the actual '
                'envelopes (set batch_window_seconds=30 to fold human-paced bursts '
                'from the same sender), process them, then call handle(envelope_id) '
                'on each to ack and remove from the queue. Send replies via the '
                "send tool. Treat the notification's content as a hint, not the "
                "message itself — list_pending is authoritative."
            ).format(name=name),
        )
```

Add the helper methods:

```python
    _URGENCY_ORDER = ["red", "yellow", "green"]

    def _build_summary(self) -> dict:
        """Snapshot the current mailbox into a notification summary."""
        pending = list(self._pending)
        count = len(pending)
        # urgency counts
        urg_counts = Counter(e.urgency for e in pending)
        urg_full = {tier: int(urg_counts.get(tier, 0)) for tier in self._URGENCY_ORDER}
        # urgency_max — highest tier present
        urgency_max = "green"
        for tier in self._URGENCY_ORDER:
            if urg_full[tier] > 0:
                urgency_max = tier
                break
        # by_sender
        sender_index: dict[str, dict] = {}
        for env in pending:
            entry = sender_index.setdefault(
                env.from_, {"from": env.from_, "count": 0, "kinds": []}
            )
            entry["count"] += 1
            if env.kind not in entry["kinds"]:
                entry["kinds"].append(env.kind)
        by_sender = list(sender_index.values())
        # Headline content — terse, useful for triage.
        if count == 0:
            content = f"INBOX: 0 pending"
        else:
            sender_summary = ", ".join(
                f"{e['count']} from {e['from']} ({'/'.join(e['kinds'])})"
                for e in by_sender
            )
            content = f"INBOX: {count} pending — {sender_summary}"
        return {
            "content": content,
            "meta": {
                "count": count,
                "urgency_max": urgency_max,
                "urgency_counts": urg_full,
                "by_sender": by_sender,
                "endpoint": self.name,
                "fired_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    def _make_channel_notification(self, summary: dict) -> SessionMessage:
        """Wrap the summary into a JSON-RPC notification SessionMessage."""
        notification = JSONRPCNotification(
            jsonrpc="2.0",
            method="notifications/claude/channel",
            params=summary,
        )
        return SessionMessage(message=JSONRPCMessage(notification))

    async def _notify_mail_arrived(self, envelope_id: str) -> None:
        """Coalesce arrivals via 50ms debounce, then push one summary."""
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._fire_after_debounce())

    async def _fire_after_debounce(self) -> None:
        try:
            await asyncio.sleep(self._notify_debounce_seconds)
        except asyncio.CancelledError:
            return
        session = self._active_session
        if session is None:
            return  # mailbox is authoritative; agent picks up on connect
        summary = self._build_summary()
        try:
            message = self._make_channel_notification(summary)
            await session.send_message(message)
        except Exception:
            log.warning(
                "endpoint '%s': push to active session failed; clearing slot",
                self.name,
                exc_info=True,
            )
            # Best-effort cleanup; the session is presumed dead.
            if self._active_session is session:
                self._active_session = None
                self._session_active = False
```

In `stop()`, cancel the debounce task during shutdown to prevent dangling pushes:

```python
    async def stop(self) -> None:
        self._handle = None
        self._session_active = False
        self._active_session = None
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except (asyncio.CancelledError, Exception):
                pass
        self._debounce_task = None
        log.info("ClaudeCodeMCPEndpoint(name=%s) stopped", self.name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_notify_mail_arrived.py -v`
Expected: 7 passed.

- [ ] **Step 5: Run the full core suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: green; existing tests do not regress. The new instructions text is internal — only a new test asserts on it, no other test should break.

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check packages/core/ && uv run ruff format packages/core/`
Expected: clean.

- [ ] **Step 7: Add urgency parameter to the `send` MCP tool**

Find the `send` tool inside `_register_tools`. Update its signature and Envelope construction:

```python
        @self._mcp.tool()
        async def send(
            to: str,
            kind: str,
            payload: dict[str, Any],
            correlation_id: str | None = None,
            in_reply_to: str | None = None,
            metadata: dict[str, Any] | None = None,
            urgency: str = "green",
            expires_at: str | None = None,
        ) -> dict:
            """Publish an envelope. Bus stamps `from:` to this endpoint's name.

            urgency: 'green' (default), 'yellow', or 'red'. Schema-validated.
            """
            if self._handle is None:
                raise RuntimeError(f"endpoint '{self.name}' is not started")
            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id=correlation_id or uuid.uuid4().hex,
                in_reply_to=in_reply_to,
                to=to,
                kind=kind,  # type: ignore[arg-type]
                payload=payload,  # type: ignore[arg-type]  # discriminated by kind
                metadata=metadata or {},
                urgency=urgency,  # type: ignore[arg-type]  # validated by Pydantic
                expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
                created_at=datetime.now(timezone.utc),
            )
            await self._handle.publish(env)
            return {"status": "published", "id": env.id}
```

- [ ] **Step 8: Write failing test for the send-tool urgency wiring**

Create `packages/core/tests/test_send_tool_urgency.py`:

```python
"""The send MCP tool accepts an urgency parameter and threads it through to Envelope."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastmcp import Client

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _CapturingHandle:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...

    def endpoints(self):
        return []


@pytest.mark.asyncio
async def test_send_tool_default_urgency_is_green():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    handle = _CapturingHandle()
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            await client.call_tool(
                "send",
                {
                    "to": "stub",
                    "kind": "TextMessage",
                    "payload": {"kind": "TextMessage", "text": "hi"},
                },
            )
        assert handle.published[0].urgency == "green"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_tool_red_urgency_propagates():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    handle = _CapturingHandle()
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            await client.call_tool(
                "send",
                {
                    "to": "stub",
                    "kind": "TextMessage",
                    "payload": {"kind": "TextMessage", "text": "alert"},
                    "urgency": "red",
                },
            )
        assert handle.published[0].urgency == "red"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_tool_invalid_urgency_raises():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    handle = _CapturingHandle()
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            with pytest.raises(Exception):
                # The tool surface validates via Pydantic; "blue" is rejected.
                await client.call_tool(
                    "send",
                    {
                        "to": "stub",
                        "kind": "TextMessage",
                        "payload": {"kind": "TextMessage", "text": "x"},
                        "urgency": "blue",
                    },
                )
    finally:
        await ep.stop()
```

- [ ] **Step 9: Run the new send-tool tests**

Run: `uv run pytest packages/core/tests/test_send_tool_urgency.py -v`
Expected: 3 passed.

- [ ] **Step 10: Run the full core suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: green.

- [ ] **Step 11: Lint and format**

Run: `uv run ruff check packages/core/ && uv run ruff format packages/core/`
Expected: clean.

- [ ] **Step 12: Commit**

```bash
git add packages/core/src/agent_core/endpoints/claude_code_mcp.py packages/core/tests/test_notify_mail_arrived.py packages/core/tests/test_send_tool_urgency.py
git commit -m "feat(claude-mcp): real push notifications + urgency on send tool + agent instructions"
```

---

## Task 7: Discord endpoint applies urgency-red regex rule on inbound

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/access.py`
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Test: `packages/agent-core-discord/tests/test_endpoint_urgency.py` (new)

`AccessConfig` grows an optional `urgency_red_regex` (string regex). `DiscordEndpoint.on_message`, after the gate passes, checks the message content against the regex; if it matches, the published `TextMessage` envelope's `urgency` is `"red"`. Default regex matches common explicit cues (`(?i)\b(urgent|now|stop)\b`); operator-overridable via `access.json` field `urgencyRedRegex`.

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-discord/tests/test_endpoint_urgency.py`:

```python
"""DiscordEndpoint applies urgency-red regex rule on inbound TextMessage envelopes."""

from __future__ import annotations

import json

import pytest

from agent_core.bus.envelope import EndpointInfo, Envelope
from agent_core_discord.endpoint import DiscordEndpoint
from tests.conftest import _FakeChannel, _FakeDiscordClient, _FakeMessage, _FakeUser


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]:
        return []


async def _start(monkeypatch, access_path=None) -> tuple[DiscordEndpoint, _Recording, _FakeDiscordClient]:
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="d",
        target="agent",
        token_env="X_TOK",
        access_config_path=access_path,
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    return ep, handle, fake


def _msg(content: str) -> _FakeMessage:
    msg = _FakeMessage(id="m1", channel_id="200", content=content)
    msg.author = _FakeUser(id="100", name="user", display_name="User")
    msg.guild = type("G", (), {"id": "guild-1"})()
    msg.channel = _FakeChannel(id="200")
    msg.attachments = []
    return msg


@pytest.mark.asyncio
async def test_inbound_default_urgency_is_green(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("hello world")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "green"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_inbound_urgent_keyword_promotes_to_red(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("URGENT please look at this")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "red"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_inbound_now_keyword_case_insensitive(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("can you reply Now please")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "red"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_inbound_substring_does_not_match(monkeypatch):
    """'now' inside 'snowfall' must not promote — \\b boundaries enforced."""
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("the snowfall is heavy today")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "green"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_custom_regex_via_access_config(monkeypatch, tmp_path):
    access = tmp_path / "access.json"
    access.write_text(
        json.dumps({"urgencyRedRegex": r"(?i)\bfire\b"}), encoding="utf-8"
    )
    ep, handle, fake = await _start(monkeypatch, access_path=str(access))
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("the server is on fire")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "red"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_disabled_regex_via_empty_string(monkeypatch, tmp_path):
    """Empty urgencyRedRegex disables the rule — all inbound is green."""
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"urgencyRedRegex": ""}), encoding="utf-8")
    ep, handle, fake = await _start(monkeypatch, access_path=str(access))
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("URGENT URGENT URGENT")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "green"
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-discord/tests/test_endpoint_urgency.py -v`
Expected: failures — published envelopes don't carry urgency yet, or the field is always green because the rule isn't applied.

- [ ] **Step 3: Add `urgency_red_regex` to `AccessConfig` and `load_access_config`**

In `packages/agent-core-discord/src/agent_core_discord/access.py`:

```python
@dataclass
class AccessConfig:
    """Validated access policy for a single Discord bot."""

    dm_policy: DmPolicy = "open"
    allow_from: list[str] = field(default_factory=list)
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    ack_reaction: str = "👀"
    # Compiled regex; None means "rule disabled". Default applied in load_access_config.
    urgency_red_regex: str = r"(?i)\b(urgent|now|stop)\b"
```

In `load_access_config`, surface the new field. Update only the relevant section:

```python
    # ... existing dm_policy handling ...
    return AccessConfig(
        dm_policy=dm_policy,
        allow_from=list(raw.get("allowFrom", [])),
        channels=dict(raw.get("channels", {})),
        ack_reaction=raw.get("ackReaction", "👀"),
        urgency_red_regex=raw.get(
            "urgencyRedRegex", r"(?i)\b(urgent|now|stop)\b"
        ),
    )
```

- [ ] **Step 4: Apply the regex in `DiscordEndpoint.on_message`**

In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, update the on_message handler factory. Find the spot where the `Envelope(...)` is built (look for `kind="TextMessage"`). Compute urgency before the call:

```python
            # 6. Build and publish the envelope.
            urgency = "green"
            regex = self._access.urgency_red_regex
            if regex:
                import re
                try:
                    if re.search(regex, message.content or ""):
                        urgency = "red"
                except re.error:
                    log.warning(
                        "discord(%s): invalid urgency_red_regex %r — skipping",
                        self.name,
                        regex,
                    )

            metadata: dict[str, Any] = {
                "discord": {
                    # ... existing metadata fields ...
                },
            }
            if attachments:
                metadata["attachments"] = attachments

            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id=uuid.uuid4().hex,
                to=self.target,
                kind="TextMessage",
                payload=TextMessagePayload(text=message.content or ""),
                metadata=metadata,
                urgency=urgency,
                created_at=datetime.now(timezone.utc),
            )
```

(Preserve the existing `metadata` construction; only `urgency=urgency` is added to the `Envelope(...)` call. The `import re` inside the function is fine — endpoint module already imports nothing from re; importing inside the hot path keeps regex compilation localized. For real perf, future versions can pre-compile in `start()`. Not a v1 issue.)

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest packages/agent-core-discord/tests/test_endpoint_urgency.py -v`
Expected: 6 passed.

- [ ] **Step 6: Run the full discord suite**

Run: `uv run pytest packages/agent-core-discord/tests/ -v`
Expected: green; the existing tests (80 passed previously) still pass.

- [ ] **Step 7: Lint and format**

Run: `uv run ruff check packages/agent-core-discord/ && uv run ruff format packages/agent-core-discord/`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/ packages/agent-core-discord/tests/test_endpoint_urgency.py
git commit -m "feat(discord): apply urgencyRedRegex rule on inbound TextMessage envelopes"
```

---

## Task 8: Real-MCP integration test for push delivery

**Files:**
- Test: `packages/core/tests/test_bus_daemon_push_integration.py` (new)

End-to-end: spin up a real bus + `ClaudeCodeMCPEndpoint` over the FastMCP HTTP host; connect a real `mcp.client.streamable_http` client; publish an envelope; assert the client receives the `notifications/claude/channel` notification on the SSE stream within ~1s.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_bus_daemon_push_integration.py`:

```python
"""End-to-end: real bus dispatches to ClaudeCodeMCPEndpoint, real MCP client
receives notifications/claude/channel on the SSE stream."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core.endpoints.stub import StubEndpoint


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_push_notification_arrives_on_real_mcp_session(tmp_path: Path):
    """Realistic flow: real bus, real FastMCP HTTP host, real mcp.client connection."""

    pytest_importorskip = pytest.importorskip
    pytest_importorskip("uvicorn")
    streamable_client_mod = pytest_importorskip("mcp.client.streamable_http")
    mcp_client_mod = pytest_importorskip("mcp")

    from mcp.client.session import ClientSession  # type: ignore

    port = _free_port()
    config = BusConfig(storage_path=tmp_path / "bus.sqlite")
    bus = Bus(config)
    endpoint = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    stub = StubEndpoint(name="probe", description="probe")
    bus.register(EndpointSpec(endpoint=endpoint))
    bus.register(EndpointSpec(endpoint=stub))

    # Boot the HTTP host on the free port. The real daemon does this via
    # HTTPHost; here we stand up an equivalent ASGI app + uvicorn manually.
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    asgi = Starlette(routes=[Mount("/mcp/agent", endpoint.asgi_app())])
    config_obj = uvicorn.Config(asgi, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config_obj)
    server_task = asyncio.create_task(server.serve())

    try:
        await asyncio.sleep(0.2)  # let uvicorn bind the port
        await bus.start()

        async with streamable_client_mod.streamablehttp_client(
            f"http://127.0.0.1:{port}/mcp/agent/"
        ) as (read, write, _close_session):
            received_notifications: list = []

            async def collector():
                async for message in read:
                    received_notifications.append(message)

            collect_task = asyncio.create_task(collector())
            try:
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    # Wait until SessionRegistry has captured the session.
                    for _ in range(20):
                        if endpoint._active_session is not None:
                            break
                        await asyncio.sleep(0.05)
                    assert endpoint._active_session is not None, \
                        "session not captured by SessionRegistry middleware"

                    # Publish an envelope to the agent via stub's BusHandle.
                    env = Envelope(
                        id="push-1",
                        correlation_id="c1",
                        to="agent",
                        kind="TextMessage",
                        payload=TextMessagePayload(text="hello"),
                        urgency="green",
                        created_at=datetime.now(timezone.utc),
                    )
                    await stub._handle.publish(env)

                    # Wait up to 2s for the notification to arrive on the
                    # SSE stream.
                    deadline = asyncio.get_event_loop().time() + 2.0
                    while asyncio.get_event_loop().time() < deadline:
                        for msg in received_notifications:
                            method = getattr(getattr(msg, "root", msg), "method", None)
                            if method == "notifications/claude/channel":
                                # Pull params off and assert summary shape.
                                params = getattr(getattr(msg, "root", msg), "params", {})
                                assert params["meta"]["count"] >= 1
                                assert params["meta"]["endpoint"] == "agent"
                                return
                        await asyncio.sleep(0.05)
                    pytest.fail(
                        f"no notifications/claude/channel received within 2s; "
                        f"saw: {[getattr(getattr(m, 'root', m), 'method', None) for m in received_notifications]}"
                    )
            finally:
                collect_task.cancel()
                with contextlib.suppress(Exception):
                    await collect_task
    finally:
        await bus.stop()
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server_task, timeout=2.0)
```

(If the existing test in `test_bus_daemon_integration.py` already sets up a host on a free port via a different pattern, prefer reusing that pattern over duplicating uvicorn boot. Read it before implementing this test.)

- [ ] **Step 2: Run test to verify it fails (or skip gracefully if uvicorn missing)**

Run: `uv run pytest packages/core/tests/test_bus_daemon_push_integration.py -v`
Expected: it should run and either pass (if everything was correct in earlier tasks) or fail with a clear assertion. Failures here often indicate that an earlier task didn't wire something correctly.

If `uvicorn` or `mcp.client.streamable_http` aren't installed, the test skips (the `importorskip` calls handle this gracefully).

- [ ] **Step 3: If failing, debug — common causes:**

- Session not captured: `SessionRegistry` not added to the FastMCP server (`__init__` issue) — re-run Task 5 verification
- Notification never sent: `_notify_mail_arrived` not invoked from `deliver()` — confirm Task 6 changes preserved the call site
- Notification arrives but params are wrong: `_make_channel_notification` is constructing the wrong shape — log the actual message and compare

- [ ] **Step 4: Run the full core suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: green.

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check packages/core/ && uv run ruff format packages/core/`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add packages/core/tests/test_bus_daemon_push_integration.py
git commit -m "test(claude-mcp): real-MCP integration test for push notification delivery"
```

---

## Task 9: Live testbot validation

**Files:**
- (No code changes; daemon config + validation prompt only.)

Mirror the discipline used in PR #7 (scheduler) and PR #8 (Discord): drop config, restart daemon, drive a 5-step validation in the testbot Claude Code session. Each step has explicit pass criteria.

- [ ] **Step 1: Confirm the daemon's bus.sqlite migrates cleanly**

With the daemon stopped:

Run: `uv run agent-core daemon status`
Expected: `daemon is not running`

If running, stop it: `uv run agent-core daemon stop`

- [ ] **Step 2: Start the daemon and verify all endpoints come up**

Run: `uv run agent-core daemon start`
Expected: `daemon started (PID: <N>)`

Then: `tail -20 ~/.agent-core/daemon.log`
Expected log lines (in order):
- `StreamableHTTP session manager started`
- `ClaudeCodeMCPEndpoint(name=agent-testbot) started`
- `Scheduler started` and `SchedulerEndpoint(name=scheduler) started`
- `DiscordEndpoint(name=discord-testbot) started`
- No `ALTER TABLE` errors

Verify the urgency column was added to the existing DB:

Run:
```bash
uv run python -c "
import sqlite3
from pathlib import Path
conn = sqlite3.connect(Path.home() / '.agent-core' / 'bus.sqlite')
cols = [r[1] for r in conn.execute('PRAGMA table_info(envelopes)').fetchall()]
assert 'urgency' in cols, f'urgency missing: {cols}'
print('urgency column present')
conn.close()
"
```
Expected: `urgency column present`

- [ ] **Step 3: Drive Step 1 of the validation in the testbot session**

Open the testbot Claude Code session (`~/.testbot/`). Paste this prompt:

```
Validate the responsive-inbox upgrades to agent-testbot. There is no new endpoint name; the existing agent-testbot has been upgraded in place. Run these 5 validation steps and report PASS/FAIL with evidence at each.

STEP 1 — Push wakes the agent on a single envelope:
Send a single ToolInvocation to the stub endpoint, addressed back to agent-testbot:
  Call mcp__agent-core__send with:
    to: "stub"
    kind: "TextMessage"
    payload: { "kind": "TextMessage", "text": "wake-up test" }
  Then immediately call mcp__agent-core__send with:
    to: "agent-testbot"
    kind: "TextMessage"
    payload: { "kind": "TextMessage", "text": "self-ping" }

Wait up to 5 seconds. You should receive a notification (notifications/claude/channel) and see your turn become active. When that fires, call mcp__agent-core__list_pending and confirm at least one new envelope is in your inbox. Handle/clear the envelope.

PASS criteria:
- A notification arrived (you saw a new turn fire automatically, not by my prompt).
- list_pending shows the envelope you sent yourself.
- The envelope's urgency field is "green".

Report PASS/FAIL.
```

Wait for testbot's report.

- [ ] **Step 4: Drive Step 2 of the validation — burst coalescing**

In the testbot session:

```
STEP 2 — Burst arrivals coalesce into one notification:
Within 100ms, send 5 ToolInvocation envelopes to yourself (agent-testbot) — call mcp__agent-core__send 5 times rapidly with payloads {"kind": "TextMessage", "text": f"burst-{i}"} for i in 0..4.

Wait briefly. You should receive ONE notification (debounced summary), not 5.

Then call list_pending. Confirm 5 envelopes in your inbox. Handle each.

PASS criteria:
- Exactly one notification fired (you can tell because only one new turn started even though 5 envelopes were sent).
- list_pending returns 5 envelopes.

Report PASS/FAIL.
```

- [ ] **Step 5: Drive Step 3 — urgency ordering**

In the testbot session:

```
STEP 3 — list_pending sorts by urgency tier:
Send three envelopes to yourself in this order:
  1. green: call mcp__agent-core__send with to="agent-testbot", kind="TextMessage",
     payload={"kind":"TextMessage","text":"green-msg"}, urgency="green"
  2. yellow: same but urgency="yellow", text="yellow-msg"
  3. red: same but urgency="red", text="red-msg"

Send them in green-then-yellow-then-red order. Then call list_pending. Confirm
the response is ordered red → yellow → green regardless of arrival order
(urgency tier wins over arrival time).

PASS criteria:
- Three envelopes returned by list_pending.
- First entry is the red-msg one, second is yellow-msg, third is green-msg.

Report PASS/FAIL.
```

- [ ] **Step 6: Drive Step 4 — same-sender batching**

In the testbot session:

```
STEP 4 — list_pending(batch_window_seconds=30) groups same-sender bursts:
Within ~5 seconds (well within the 30s window), send three envelopes from the same source. Use the discord-testbot endpoint as the source by manually constructing envelopes that LOOK like they came from there:
  - Actually no, the stamp is set by the bus. Instead: send three from yourself (agent-testbot → agent-testbot) in quick succession.

Then call list_pending(batch_window_seconds=30). Confirm the response collapses the three into one batched group (type="batch", envelopes array with 3 entries).

Then call list_pending() (default batch_window=0). Confirm the response is flat — three separate entries.

PASS criteria:
- list_pending(batch_window_seconds=30) returns 1 entry of type="batch" containing 3 envelopes.
- list_pending() (default) returns 3 entries (flat).

Report PASS/FAIL.
```

- [ ] **Step 7: Drive Step 5 — disconnect/reconnect; mailbox is authoritative**

In the testbot session:

```
STEP 5 — Push fails silently when no session; mailbox catches up on reconnect:
This step requires me (Jeff) to do the disconnect side. Tell me when you're ready.

Procedure:
  a. Tell me when ready. I'll close your Claude Code session.
  b. While disconnected, I'll send a TextMessage envelope to agent-testbot from the daemon's perspective (using the existing testbot validation script or a manual python send).
  c. I'll restart your session.
  d. On reconnect, you should see a notification arrive within 1-2 seconds (replay the queued state) — OR if the new code only fires push on new arrivals, the push won't fire automatically. In that case, call list_pending immediately on reconnect; confirm the envelope sent during your absence is present.

PASS criteria:
- list_pending after reconnect shows the envelope that was sent while you were disconnected. (Polling is authoritative even if push didn't fire.)
- No data loss.

Report PASS/FAIL when I tell you the cycle is complete.
```

- [ ] **Step 8: Compile final validation report**

After all 5 steps PASS, write a brief report:

```
Responsive Inbox Validation — Final Report

Step 1 (push wakes single envelope): PASS
Step 2 (burst coalescing): PASS
Step 3 (urgency ordering): PASS / BLOCKED
Step 4 (same-sender batching): PASS
Step 5 (mailbox authoritative on reconnect): PASS

Daemon log inspection: no ALTER TABLE errors, no notification-related stack traces.

Ship it: YES / NO
```

- [ ] **Step 9: Commit the validation evidence (optional — only if any test config / files were added)**

If no files changed during validation (which is the expected case — this step is purely runtime exercise), there's nothing to commit. If issues surfaced and required fixes, commit those fixes as their own focused commits with messages like `fix(claude-mcp): <specific issue from validation>`.

---

## Final wrap

After all tasks complete and Task 9's validation is PASS:

```bash
git push -u origin feat/responsive-inbox
gh pr create --title "feat(claude-mcp): responsive inbox — sub-project I" --body "$(cat <<'EOF'
## Summary
- Adds `urgency` field on `Envelope` (green|yellow|red, default green) with SQLite ALTER TABLE migration
- `list_pending` now sorts by urgency tier (red → yellow → green, FIFO within tier) and accepts `batch_window_seconds` for same-sender batching
- Replaces `_SessionTracker` with `SessionRegistry` middleware that captures the live `ServerSession` via FastMCP's `_subscription_task_group` (mirrors PingMiddleware pattern)
- `_notify_mail_arrived` now sends real `notifications/claude/channel` summaries with 50ms debounce; polling remains authoritative
- DiscordEndpoint applies `urgencyRedRegex` rule on inbound TextMessage (default: `\b(urgent|now|stop)\b`)

## Test plan
- [ ] All ~25 new unit tests pass + existing 80+ from prior PRs
- [ ] Real-MCP integration test confirms push delivery on SSE stream
- [ ] Live testbot 5-step validation: PASS on all five
- [ ] Daemon migrates `~/.agent-core/bus.sqlite` cleanly on first boot

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

After merge: update `docs/ROADMAP.md` to mark sub-project I 🟢 shipped (mirrors the PR #8 pattern).
