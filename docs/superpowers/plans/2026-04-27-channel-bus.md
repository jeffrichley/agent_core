# Channel Bus — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the bus core, persistence, hook pipeline, stub endpoint, runner, and CLI — a complete, runnable, end-to-end testable subsystem with no external dependencies.

**Architecture:** Single-process asyncio bus with SQLite-backed durable mailboxes. Endpoints implement a `@runtime_checkable` Protocol; the `BusHandle` they receive stamps `from:` immutably. Two hook stages (`pre_publish`, `pre_deliver`) and two periodic sweeps (TTL, redelivery) shape the operational behavior. Phase 1 ships the bus + a stub endpoint suitable for tests; Phase 2 will add the FastMCP-based Claude Code endpoint.

**Tech Stack:** Python 3.12, Pydantic v2, asyncio, aiosqlite (WAL mode), Typer, Rich, pytest, ruff (line-length 100), uv for package management.

**Spec:** [`docs/superpowers/specs/2026-04-27-channel-bus-design.md`](../specs/2026-04-27-channel-bus-design.md)

---

## Conventions to follow throughout

- Run tests with `uv run pytest <path> -v`
- Run linting with `uv run ruff check src/agent_core/bus tests/bus`
- Format with `uv run ruff format src/agent_core/bus tests/bus`
- Commit messages follow the repo's Conventional Commits style: `feat(bus):`, `test(bus):`, `chore(bus):`, etc.
- Test files use Pytest's class-based grouping (see `tests/test_pipeline.py` for the existing pattern).
- All bus code is async-first. Use `pytest-asyncio` (add as dev dep in Task 1).
- Time-dependent tests use `freezegun` or pass injected `now` callables. We add `freezegun` as a dev dep in Task 1.

---

## Task 1: Project scaffolding and dependencies

**Files:**
- Modify: `pyproject.toml`
- Create: `src/agent_core/bus/__init__.py`
- Create: `src/agent_core/endpoints/__init__.py`
- Create: `src/agent_core/bus_hooks/__init__.py`
- Create: `tests/bus/__init__.py`

- [ ] **Step 1: Add runtime and dev dependencies**

Edit `pyproject.toml`. Add `aiosqlite` to `dependencies`; add `pytest-asyncio` and `freezegun` to the `dev` group.

```toml
[project]
dependencies = [
    "claude-agent-sdk>=0.1.29",
    "python-dotenv>=1.0.0",
    "tzdata>=2024.1",
    "pydantic>=2.0",
    "typer>=0.12",
    "rich>=13.0",
    "pyyaml>=6.0",
    "desktop-notifier>=5.0",
    "mcp>=1.9.0",
    "agentmail>=0.4",
    "aiosqlite>=0.20",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "freezegun>=1.5",
    "ruff>=0.14",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
asyncio_mode = "auto"
```

(Note the new `asyncio_mode = "auto"` line — lets us write `async def test_*` without per-test markers.)

- [ ] **Step 2: Create empty package directories**

Create these files with the indicated content:

`src/agent_core/bus/__init__.py`:
```python
"""agent_core message bus — durable, in-process, named-endpoint routing."""
```

`src/agent_core/endpoints/__init__.py`:
```python
"""Built-in endpoint adapters."""
```

`src/agent_core/bus_hooks/__init__.py`:
```python
"""Built-in pre_publish / pre_deliver hooks."""
```

`tests/bus/__init__.py`:
```python
```
(empty)

- [ ] **Step 3: Sync deps and verify the existing test suite still passes**

```bash
uv sync
uv run pytest -v
```

Expected: all existing tests pass; aiosqlite, pytest-asyncio, freezegun installed.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock src/agent_core/bus src/agent_core/endpoints src/agent_core/bus_hooks tests/bus
git commit -m "chore(bus): scaffold bus/endpoints/bus_hooks packages and add deps"
```

---

## Task 2: Envelope models

**Files:**
- Create: `src/agent_core/bus/envelope.py`
- Create: `tests/bus/test_envelope.py`

- [ ] **Step 1: Write the failing test**

Create `tests/bus/test_envelope.py`:

```python
"""Tests for Envelope and payload models."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    CancellationPayload,
    EndpointInfo,
    Envelope,
    EventPayload,
    ProgressPayload,
    TextMessagePayload,
    ToolInvocationPayload,
)


def _now() -> datetime:
    return datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)


class TestEnvelopeRoundtrip:
    def test_text_message_roundtrip(self):
        env = Envelope(
            id="e1",
            correlation_id="c1",
            to="agent-pepper",
            kind="TextMessage",
            payload=TextMessagePayload(text="hello"),
            created_at=_now(),
        )
        data = env.model_dump(by_alias=True, mode="json")
        assert data["from"] == ""  # default; bus stamps later
        assert data["to"] == "agent-pepper"
        assert data["kind"] == "TextMessage"
        assert data["payload"]["kind"] == "TextMessage"
        assert data["payload"]["text"] == "hello"
        rebuilt = Envelope.model_validate(data)
        assert rebuilt.payload.text == "hello"

    def test_event_with_open_data(self):
        env = Envelope(
            id="e2",
            correlation_id="c2",
            to="events",
            kind="Event",
            payload=EventPayload(type="location", data={"lat": 38.9, "lon": -77.0}),
            created_at=_now(),
        )
        data = env.model_dump(by_alias=True, mode="json")
        assert data["payload"]["type"] == "location"
        assert data["payload"]["data"] == {"lat": 38.9, "lon": -77.0}
        rebuilt = Envelope.model_validate(data)
        assert rebuilt.payload.data["lat"] == 38.9

    def test_progress(self):
        env = Envelope(
            id="e3",
            correlation_id="c3",
            to="agent-pepper",
            kind="Progress",
            payload=ProgressPayload(status="working", percent=0.5),
            created_at=_now(),
        )
        assert env.payload.status == "working"
        assert env.payload.percent == 0.5

    def test_cancellation(self):
        env = Envelope(
            id="e4",
            correlation_id="c4",
            to="agent-deb",
            kind="Cancellation",
            payload=CancellationPayload(reason="user changed mind"),
            created_at=_now(),
        )
        assert env.payload.reason == "user changed mind"

    def test_tool_invocation(self):
        env = Envelope(
            id="e5",
            correlation_id="c5",
            to="scheduler",
            kind="ToolInvocation",
            payload=ToolInvocationPayload(tool="create_job", args={"name": "x"}),
            created_at=_now(),
        )
        assert env.payload.tool == "create_job"

    def test_acknowledgment(self):
        env = Envelope(
            id="e6",
            correlation_id="c6",
            to="agent-pepper",
            kind="Acknowledgment",
            payload=AcknowledgmentPayload(of="e5"),
            created_at=_now(),
        )
        assert env.payload.of == "e5"


class TestEnvelopeValidation:
    def test_kind_payload_must_match(self):
        # `kind: TextMessage` with an `EventPayload` must fail (discriminator mismatch).
        with pytest.raises(ValidationError):
            Envelope.model_validate(
                {
                    "id": "e1",
                    "correlation_id": "c1",
                    "from": "",
                    "to": "x",
                    "kind": "TextMessage",
                    "payload": {"kind": "Event", "type": "foo", "data": {}},
                    "created_at": _now().isoformat(),
                }
            )

    def test_from_alias(self):
        # JSON uses `from`, Python attribute is `from_`.
        env = Envelope(
            id="e1",
            correlation_id="c1",
            to="x",
            kind="TextMessage",
            payload=TextMessagePayload(text="hi"),
            created_at=_now(),
        )
        env.from_ = "agent-pepper"
        data = env.model_dump(by_alias=True, mode="json")
        assert data["from"] == "agent-pepper"
        assert "from_" not in data


class TestEndpointInfo:
    def test_construction(self):
        info = EndpointInfo(name="agent-deb", description="Research agent.")
        assert info.name == "agent-deb"
        assert info.description == "Research agent."

    def test_default_description(self):
        info = EndpointInfo(name="x")
        assert info.description == ""
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_envelope.py -v
```

Expected: ImportError / ModuleNotFoundError for `agent_core.bus.envelope`.

- [ ] **Step 3: Implement the envelope module**

Create `src/agent_core/bus/envelope.py`:

```python
"""Envelope wire format — Pydantic models for the bus's universal message shape.

Every message that crosses the bus is an Envelope. `kind` is a closed structural
discriminator; for kind=Event, the inner `Event.payload.type` is open-ended for
domain events.

The `from_` field defaults to "" because the bus stamps it at publish time
(see BusHandle in handle.py). Endpoints do not need to know their own name.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class TextMessagePayload(BaseModel):
    kind: Literal["TextMessage"] = "TextMessage"
    text: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class EventPayload(BaseModel):
    """Domain events. The `data` dict is intentionally open-ended; bus does not validate."""

    kind: Literal["Event"] = "Event"
    type: str
    schema_version: str = "1"
    data: dict[str, Any] = Field(default_factory=dict)


class ToolInvocationPayload(BaseModel):
    kind: Literal["ToolInvocation"] = "ToolInvocation"
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class CancellationPayload(BaseModel):
    kind: Literal["Cancellation"] = "Cancellation"
    reason: str | None = None


class ProgressPayload(BaseModel):
    kind: Literal["Progress"] = "Progress"
    status: Literal["working", "blocked", "complete"]
    note: str | None = None
    percent: float | None = None


class AcknowledgmentPayload(BaseModel):
    kind: Literal["Acknowledgment"] = "Acknowledgment"
    of: str
    note: str | None = None


EnvelopePayload = Annotated[
    Union[
        TextMessagePayload,
        EventPayload,
        ToolInvocationPayload,
        CancellationPayload,
        ProgressPayload,
        AcknowledgmentPayload,
    ],
    Field(discriminator="kind"),
]


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
    expires_at: datetime | None = None
    created_at: datetime


class EndpointInfo(BaseModel):
    """Directory entry exposed by BusHandle.endpoints()."""

    name: str
    description: str = ""
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_envelope.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/agent_core/bus tests/bus
uv run ruff format src/agent_core/bus tests/bus
git add src/agent_core/bus/envelope.py tests/bus/test_envelope.py
git commit -m "feat(bus): add Envelope and payload models with discriminated union"
```

---

## Task 3: Endpoint and BusHook protocols

**Files:**
- Create: `src/agent_core/bus/protocol.py`
- Create: `tests/bus/test_protocol.py`

- [ ] **Step 1: Write the failing test**

Create `tests/bus/test_protocol.py`:

```python
"""Tests for the Endpoint and BusHook protocols."""

from typing import Literal

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.protocol import BusHook, Endpoint, EndpointUnavailable


class _MinimalEndpoint:
    name = "stub"

    async def start(self, bus) -> None:
        pass

    async def deliver(self, envelope: Envelope) -> None:
        pass

    async def stop(self) -> None:
        pass


class _MinimalHook:
    async def execute(
        self, stage: Literal["pre_publish", "pre_deliver"], envelope: Envelope, params: dict
    ) -> Envelope | None:
        return envelope


class TestProtocols:
    def test_endpoint_runtime_check(self):
        ep = _MinimalEndpoint()
        assert isinstance(ep, Endpoint)

    def test_non_endpoint_fails_check(self):
        class NotAnEndpoint:
            pass

        assert not isinstance(NotAnEndpoint(), Endpoint)

    def test_hook_runtime_check(self):
        hook = _MinimalHook()
        assert isinstance(hook, BusHook)

    def test_endpoint_unavailable_is_exception(self):
        try:
            raise EndpointUnavailable("Discord disconnected")
        except EndpointUnavailable as exc:
            assert str(exc) == "Discord disconnected"
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_protocol.py -v
```

Expected: ImportError on `agent_core.bus.protocol`.

- [ ] **Step 3: Implement the protocol module**

Create `src/agent_core/bus/protocol.py`:

```python
"""Endpoint and BusHook protocols + EndpointUnavailable exception.

The Endpoint protocol is the minimal interface every adapter satisfies.
@runtime_checkable lets the bus verify Protocol conformance at load time.
"""

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from agent_core.bus.envelope import Envelope

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle


class EndpointUnavailable(Exception):
    """Raised by Endpoint.deliver() to signal a temporary failure.

    The bus will pause delivery to this endpoint, queue subsequent envelopes
    in the mailbox, and retry on a backoff. Any other exception is treated
    as terminal and the envelope moves to dead-letter.
    """


@runtime_checkable
class Endpoint(Protocol):
    """An addressable participant on the bus."""

    name: str

    async def start(self, bus: "BusHandle") -> None:
        """Bus is ready. Open connections, register listeners, start your loop."""

    async def deliver(self, envelope: Envelope) -> None:
        """Bus is delivering an envelope addressed to you.

        You MUST eventually call bus.ack(envelope.id) when handling completes.
        Raise EndpointUnavailable to signal temporary failure (bus will retry).
        Other exceptions are terminal — envelope moves to dead-letter.
        """

    async def stop(self) -> None:
        """Graceful shutdown. Close connections, flush state."""


@runtime_checkable
class BusHook(Protocol):
    """A hook that runs at the pre_publish or pre_deliver pipeline stage."""

    async def execute(
        self,
        stage: Literal["pre_publish", "pre_deliver"],
        envelope: Envelope,
        params: dict,
    ) -> Envelope | None:
        """Return the (possibly modified) envelope to continue.
        Return None to drop the envelope.
        Raising aborts the operation and surfaces the error to the caller.
        """
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_protocol.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/agent_core/bus tests/bus
git add src/agent_core/bus/protocol.py tests/bus/test_protocol.py
git commit -m "feat(bus): add Endpoint and BusHook protocols with EndpointUnavailable"
```

---

## Task 4: Persistence — schema and initialization

**Files:**
- Create: `src/agent_core/bus/persistence.py`
- Create: `tests/bus/test_persistence.py`

- [ ] **Step 1: Write the failing test**

Create `tests/bus/test_persistence.py`:

```python
"""Tests for the bus's SQLite persistence layer."""

import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.persistence import Persistence


def _now() -> datetime:
    return datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store(tmp_path: Path) -> Persistence:
    db = tmp_path / "bus.sqlite"
    p = Persistence(db)
    await p.connect()
    yield p
    await p.close()


class TestSchemaInit:
    async def test_creates_file(self, tmp_path: Path):
        db = tmp_path / "bus.sqlite"
        p = Persistence(db)
        await p.connect()
        await p.close()
        assert db.exists()

    async def test_init_is_idempotent(self, tmp_path: Path):
        db = tmp_path / "bus.sqlite"
        p1 = Persistence(db)
        await p1.connect()
        await p1.close()
        # Re-opening must not raise.
        p2 = Persistence(db)
        await p2.connect()
        await p2.close()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
    async def test_file_is_0600_on_posix(self, tmp_path: Path):
        db = tmp_path / "bus.sqlite"
        p = Persistence(db)
        await p.connect()
        await p.close()
        mode = stat.S_IMODE(os.stat(db).st_mode)
        assert mode == 0o600

    async def test_schema_has_envelopes_table(self, store: Persistence):
        async with store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='envelopes'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_persistence.py -v
```

Expected: ImportError on `agent_core.bus.persistence`.

- [ ] **Step 3: Implement the persistence module (init only)**

Create `src/agent_core/bus/persistence.py`:

```python
"""SQLite-backed durable storage for the bus.

One table, one writer connection, WAL mode. Envelopes are immutable except
for delivery state columns (state, delivery_count, last_attempted, etc.).
The hot path never deletes; only state transitions.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import aiosqlite

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


class Persistence:
    """Async SQLite wrapper for the bus's durable mailbox state."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        if not existed and sys.platform != "win32":
            os.chmod(self.path, 0o600)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_persistence.py -v
```

Expected: 4 tests pass (3 on Windows because of the skip).

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/bus/persistence.py tests/bus/test_persistence.py
git commit -m "feat(bus): add SQLite persistence layer with schema initialization"
```

---

## Task 5: Persistence — CRUD and state transitions

**Files:**
- Modify: `src/agent_core/bus/persistence.py`
- Modify: `tests/bus/test_persistence.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/bus/test_persistence.py`:

```python
def _envelope(id_: str = "e1", to: str = "agent-pepper", **overrides) -> Envelope:
    fields = dict(
        id=id_,
        correlation_id="c1",
        from_="discord",
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=_now(),
    )
    fields.update(overrides)
    return Envelope(**fields)


class TestPersistenceCRUD:
    async def test_insert_and_fetch(self, store: Persistence):
        env = _envelope()
        await store.insert(env)
        fetched = await store.get(env.id)
        assert fetched is not None
        assert fetched.id == env.id
        assert fetched.from_ == "discord"
        assert fetched.payload.text == "hi"

    async def test_get_missing(self, store: Persistence):
        assert await store.get("does-not-exist") is None

    async def test_list_pending_for_endpoint(self, store: Persistence):
        await store.insert(_envelope("e1", to="agent-pepper"))
        await store.insert(_envelope("e2", to="agent-pepper"))
        await store.insert(_envelope("e3", to="discord"))
        pending = await store.list_pending("agent-pepper")
        assert {e.id for e in pending} == {"e1", "e2"}

    async def test_count_pending_for_endpoint(self, store: Persistence):
        await store.insert(_envelope("e1", to="x"))
        await store.insert(_envelope("e2", to="x"))
        assert await store.count_pending("x") == 2

    async def test_state_transitions(self, store: Persistence):
        env = _envelope()
        await store.insert(env)
        assert (await store.get(env.id)).model_extra is None  # sanity
        await store.mark_in_flight(env.id, in_flight_until=_now())
        row = await store.row(env.id)
        assert row["state"] == "in_flight"
        assert row["delivery_count"] == 1
        await store.mark_acked(env.id)
        row = await store.row(env.id)
        assert row["state"] == "acked"

    async def test_mark_dead_letter(self, store: Persistence):
        env = _envelope()
        await store.insert(env)
        await store.mark_dead_letter(env.id, reason="boom")
        row = await store.row(env.id)
        assert row["state"] == "dead_letter"
        assert row["nack_reason"] == "boom"

    async def test_requeue_resets_state(self, store: Persistence):
        env = _envelope()
        await store.insert(env)
        await store.mark_in_flight(env.id, in_flight_until=_now())
        await store.requeue(env.id)
        row = await store.row(env.id)
        assert row["state"] == "pending"

    async def test_expire(self, store: Persistence):
        env = _envelope()
        await store.insert(env)
        await store.expire(env.id)
        assert (await store.row(env.id))["state"] == "expired"

    async def test_idempotent_ack(self, store: Persistence):
        env = _envelope()
        await store.insert(env)
        await store.mark_in_flight(env.id, in_flight_until=_now())
        await store.mark_acked(env.id)
        # Second ack must not raise.
        await store.mark_acked(env.id)
        assert (await store.row(env.id))["state"] == "acked"

    async def test_list_by_correlation(self, store: Persistence):
        await store.insert(_envelope("e1", to="x"))
        await store.insert(_envelope("e2", to="y", correlation_id="c1"))
        await store.insert(
            Envelope(
                id="e3",
                correlation_id="c2",
                to="x",
                kind="TextMessage",
                payload=TextMessagePayload(text="other"),
                created_at=_now(),
            )
        )
        thread = await store.list_by_correlation("c1")
        assert {e.id for e in thread} == {"e1", "e2"}

    async def test_list_dead_letter(self, store: Persistence):
        await store.insert(_envelope("e1"))
        await store.insert(_envelope("e2"))
        await store.mark_dead_letter("e1", reason="test")
        dlq = await store.list_dead_letter()
        assert [e.id for e in dlq] == ["e1"]

    async def test_expired_undelivered_lookup(self, store: Persistence):
        from datetime import timedelta

        past = _now() - timedelta(hours=1)
        env = _envelope("e1")
        env.expires_at = past
        await store.insert(env)
        # No expires_at → not in result
        await store.insert(_envelope("e2"))
        results = await store.find_expired(now=_now())
        assert {e.id for e in results} == {"e1"}

    async def test_in_flight_timeouts(self, store: Persistence):
        from datetime import timedelta

        env = _envelope("e1")
        await store.insert(env)
        past = _now() - timedelta(minutes=10)
        await store.mark_in_flight(env.id, in_flight_until=past)
        results = await store.find_in_flight_timeouts(now=_now())
        assert {e.id for e in results} == {"e1"}
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_persistence.py -v
```

Expected: AttributeError / unimplemented method errors.

- [ ] **Step 3: Implement CRUD on Persistence**

Add the following imports at the top of `src/agent_core/bus/persistence.py` (alongside the existing imports from Task 4):

```python
import json
from datetime import datetime
from typing import Any

from agent_core.bus.envelope import Envelope
```

Add this module-level helper above the `Persistence` class:

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
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
        }
    )
```

Add these methods to the existing `Persistence` class body (alongside `connect` and `close`):

```python
async def insert(self, env: Envelope) -> None:
    await self._conn.execute(
        """INSERT INTO envelopes
           (id, correlation_id, in_reply_to, from_endpoint, to_endpoint,
            kind, payload_json, metadata_json, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            env.id,
            env.correlation_id,
            env.in_reply_to,
            env.from_,
            env.to,
            env.kind,
            env.payload.model_dump_json(),
            json.dumps(env.metadata),
            env.expires_at.isoformat() if env.expires_at else None,
            env.created_at.isoformat(),
        ),
    )
    await self._conn.commit()

async def row(self, id_: str) -> dict[str, Any] | None:
    self._conn.row_factory = aiosqlite.Row
    async with self._conn.execute(
        "SELECT * FROM envelopes WHERE id = ?", (id_,)
    ) as cur:
        r = await cur.fetchone()
    return dict(r) if r else None

async def get(self, id_: str) -> Envelope | None:
    r = await self.row(id_)
    return _row_to_envelope(r) if r else None

async def list_pending(self, endpoint: str) -> list[Envelope]:
    self._conn.row_factory = aiosqlite.Row
    async with self._conn.execute(
        """SELECT * FROM envelopes
           WHERE to_endpoint = ? AND state = 'pending'
           ORDER BY created_at ASC""",
        (endpoint,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_envelope(dict(r)) for r in rows]

async def count_pending(self, endpoint: str) -> int:
    async with self._conn.execute(
        "SELECT COUNT(*) FROM envelopes WHERE to_endpoint = ? AND state = 'pending'",
        (endpoint,),
    ) as cur:
        row = await cur.fetchone()
    return row[0]

async def mark_in_flight(self, id_: str, in_flight_until: datetime) -> None:
    await self._conn.execute(
        """UPDATE envelopes
           SET state = 'in_flight',
               delivery_count = delivery_count + 1,
               last_attempted = ?,
               in_flight_until = ?
           WHERE id = ?""",
        (datetime.utcnow().isoformat(), in_flight_until.isoformat(), id_),
    )
    await self._conn.commit()

async def mark_acked(self, id_: str) -> None:
    await self._conn.execute(
        "UPDATE envelopes SET state = 'acked' WHERE id = ?", (id_,)
    )
    await self._conn.commit()

async def mark_dead_letter(self, id_: str, reason: str | None = None) -> None:
    await self._conn.execute(
        "UPDATE envelopes SET state = 'dead_letter', nack_reason = ? WHERE id = ?",
        (reason, id_),
    )
    await self._conn.commit()

async def requeue(self, id_: str) -> None:
    await self._conn.execute(
        "UPDATE envelopes SET state = 'pending', in_flight_until = NULL WHERE id = ?",
        (id_,),
    )
    await self._conn.commit()

async def expire(self, id_: str) -> None:
    await self._conn.execute(
        "UPDATE envelopes SET state = 'expired' WHERE id = ?", (id_,)
    )
    await self._conn.commit()

async def list_by_correlation(self, correlation_id: str) -> list[Envelope]:
    self._conn.row_factory = aiosqlite.Row
    async with self._conn.execute(
        "SELECT * FROM envelopes WHERE correlation_id = ? ORDER BY created_at ASC",
        (correlation_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_envelope(dict(r)) for r in rows]

async def list_dead_letter(self) -> list[Envelope]:
    self._conn.row_factory = aiosqlite.Row
    async with self._conn.execute(
        "SELECT * FROM envelopes WHERE state = 'dead_letter' ORDER BY last_attempted DESC"
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_envelope(dict(r)) for r in rows]

async def find_expired(self, *, now: datetime) -> list[Envelope]:
    self._conn.row_factory = aiosqlite.Row
    async with self._conn.execute(
        """SELECT * FROM envelopes
           WHERE expires_at IS NOT NULL
             AND expires_at < ?
             AND state IN ('pending', 'in_flight')""",
        (now.isoformat(),),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_envelope(dict(r)) for r in rows]

async def find_in_flight_timeouts(self, *, now: datetime) -> list[Envelope]:
    self._conn.row_factory = aiosqlite.Row
    async with self._conn.execute(
        """SELECT * FROM envelopes
           WHERE state = 'in_flight'
             AND in_flight_until IS NOT NULL
             AND in_flight_until < ?""",
        (now.isoformat(),),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_envelope(dict(r)) for r in rows]
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_persistence.py -v
```

Expected: all persistence tests pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/agent_core/bus tests/bus
git add src/agent_core/bus/persistence.py tests/bus/test_persistence.py
git commit -m "feat(bus): add Envelope CRUD and state transitions to persistence"
```

---

## Task 6: BusHandle — bound name and from-stamping

**Files:**
- Create: `src/agent_core/bus/handle.py`
- Create: `tests/bus/test_handle.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bus/test_handle.py`:

```python
"""Tests for BusHandle — the per-endpoint surface to the bus."""

from datetime import datetime, timezone

import pytest

from agent_core.bus.envelope import EndpointInfo, Envelope, TextMessagePayload
from agent_core.bus.handle import BusHandle


class _RecordingBus:
    def __init__(self):
        self.published: list[tuple[Envelope, str | list[str] | None]] = []
        self.acks: list[str] = []
        self.nacks: list[tuple[str, bool]] = []
        self.directory = [
            EndpointInfo(name="agent-pepper", description="P"),
            EndpointInfo(name="discord", description="D"),
        ]

    async def _enqueue(self, envelope: Envelope, to=None) -> None:
        self.published.append((envelope, to))

    async def _ack(self, envelope_id: str) -> None:
        self.acks.append(envelope_id)

    async def _nack(self, envelope_id: str, requeue: bool) -> None:
        self.nacks.append((envelope_id, requeue))

    def _endpoints(self) -> list[EndpointInfo]:
        return list(self.directory)


def _envelope(**overrides) -> Envelope:
    fields = dict(
        id="e1",
        correlation_id="c1",
        to="agent-pepper",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
    )
    fields.update(overrides)
    return Envelope(**fields)


class TestBusHandlePublish:
    async def test_stamps_from(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "agent-pepper")
        env = _envelope(from_="not-pepper")  # caller tries to spoof
        await handle.publish(env)
        published, _ = bus.published[0]
        assert published.from_ == "agent-pepper"  # bus overwrote

    async def test_stamps_when_unset(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "discord")
        env = _envelope()  # from_ defaults to ""
        await handle.publish(env)
        assert bus.published[0][0].from_ == "discord"

    async def test_passes_to_override(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "discord")
        env = _envelope()
        await handle.publish(env, to=["a", "b"])
        assert bus.published[0][1] == ["a", "b"]


class TestBusHandleAckNack:
    async def test_ack_delegates(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        await handle.ack("e1")
        assert bus.acks == ["e1"]

    async def test_nack_delegates_with_requeue_default(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        await handle.nack("e1")
        assert bus.nacks == [("e1", True)]

    async def test_nack_with_no_requeue(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        await handle.nack("e1", requeue=False)
        assert bus.nacks == [("e1", False)]


class TestBusHandleEndpoints:
    def test_endpoints_returns_directory_snapshot(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        infos = handle.endpoints()
        assert {i.name for i in infos} == {"agent-pepper", "discord"}

    def test_endpoints_is_independent_copy(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        infos = handle.endpoints()
        infos.clear()
        # Mutating the returned list must not affect later calls.
        assert len(handle.endpoints()) == 2
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_handle.py -v
```

Expected: ImportError on `agent_core.bus.handle`.

- [ ] **Step 3: Implement BusHandle**

Create `src/agent_core/bus/handle.py`:

```python
"""BusHandle — the per-endpoint surface for bus operations.

Every endpoint receives a fresh BusHandle bound to its registered name. The
handle stamps `from_` to that name on every publish, so endpoints cannot
spoof each other regardless of what they put in the envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_core.bus.envelope import EndpointInfo, Envelope

if TYPE_CHECKING:
    from agent_core.bus.core import Bus


class BusHandle:
    """A per-endpoint, identity-bound view of the bus.

    The endpoint's `name` is set at construction (by the bus, when registering
    the endpoint) and is overwritten onto every published envelope's `from_`.
    Endpoints never need to know their own name; the handle knows for them.
    """

    def __init__(self, bus: "Bus", endpoint_name: str):
        self._bus = bus
        self._endpoint_name = endpoint_name

    async def publish(
        self, envelope: Envelope, to: str | list[str] | None = None
    ) -> None:
        """Send an envelope. The bus stamps `from_` to this endpoint's name,
        runs pre_publish hooks, persists, then dispatches.

        If `to` is provided it overrides envelope.to (and may be a list to
        fan out to N recipients via N envelopes — handled by the bus)."""
        stamped = envelope.model_copy(update={"from_": self._endpoint_name})
        await self._bus._enqueue(stamped, to)

    async def ack(self, envelope_id: str) -> None:
        """Confirm successful handling. Idempotent."""
        await self._bus._ack(envelope_id)

    async def nack(self, envelope_id: str, requeue: bool = True) -> None:
        """Reject a delivered envelope. requeue=True schedules redelivery."""
        await self._bus._nack(envelope_id, requeue)

    def endpoints(self) -> list[EndpointInfo]:
        """Snapshot of currently-registered endpoints (name + description)."""
        return self._bus._endpoints()
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_handle.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/bus/handle.py tests/bus/test_handle.py
git commit -m "feat(bus): add BusHandle with from-stamping and directory access"
```

---

## Task 7: Bus core — registration and lifecycle

**Files:**
- Create: `src/agent_core/bus/core.py`
- Create: `tests/bus/test_core_lifecycle.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bus/test_core_lifecycle.py`:

```python
"""Tests for Bus registration, start/stop, and endpoint discovery."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload


class _LifecycleSpy:
    def __init__(self, name: str):
        self.name = name
        self.started = False
        self.stopped = False
        self.delivered: list[Envelope] = []
        self._handle = None

    async def start(self, bus) -> None:
        self.started = True
        self._handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        self.delivered.append(envelope)

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
async def bus(tmp_path: Path) -> Bus:
    cfg = BusConfig(storage_path=tmp_path / "bus.sqlite")
    b = Bus(cfg)
    yield b
    await b.stop()


class TestRegistration:
    async def test_register_and_start(self, bus: Bus):
        ep = _LifecycleSpy("agent-pepper")
        bus.register(EndpointSpec(endpoint=ep, description="A test agent."))
        await bus.start()
        assert ep.started is True
        assert ep._handle is not None  # received its BusHandle

    async def test_endpoints_directory(self, bus: Bus):
        a = _LifecycleSpy("a")
        b_ep = _LifecycleSpy("b")
        bus.register(EndpointSpec(endpoint=a, description="A!"))
        bus.register(EndpointSpec(endpoint=b_ep, description="B!"))
        await bus.start()
        infos = bus._endpoints()
        assert {(i.name, i.description) for i in infos} == {("a", "A!"), ("b", "B!")}

    async def test_stop_calls_endpoints(self, bus: Bus):
        ep = _LifecycleSpy("x")
        bus.register(EndpointSpec(endpoint=ep))
        await bus.start()
        await bus.stop()
        assert ep.stopped is True

    async def test_duplicate_registration_rejected(self, bus: Bus):
        bus.register(EndpointSpec(endpoint=_LifecycleSpy("x")))
        with pytest.raises(ValueError, match="already registered"):
            bus.register(EndpointSpec(endpoint=_LifecycleSpy("x")))
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_core_lifecycle.py -v
```

Expected: ImportError on `agent_core.bus.core`.

- [ ] **Step 3: Implement Bus skeleton with registration + lifecycle**

Create `src/agent_core/bus/core.py`:

```python
"""Bus core — endpoint registration, lifecycle, dispatch, sweeps.

Single asyncio event loop. Endpoints register before start; once started, the
bus drains pending envelopes from each endpoint's mailbox. publish() persists
then dispatches if the endpoint is live; otherwise mail queues durably.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_core.bus.envelope import EndpointInfo, Envelope
from agent_core.bus.handle import BusHandle
from agent_core.bus.persistence import Persistence
from agent_core.bus.protocol import Endpoint

log = logging.getLogger(__name__)


@dataclass
class BusConfig:
    storage_path: Path
    redelivery_timeout_seconds: int = 300
    max_delivery_attempts: int = 5
    ttl_sweep_seconds: int = 60
    redelivery_sweep_seconds: int = 10
    acked_retention_days: int = 14
    max_pending_per_endpoint: int = 10_000


@dataclass
class EndpointSpec:
    endpoint: Endpoint
    description: str = ""

    @property
    def name(self) -> str:
        return self.endpoint.name


class Bus:
    """In-process bus router."""

    def __init__(self, config: BusConfig):
        self.config = config
        self._endpoints_by_name: dict[str, EndpointSpec] = {}
        self._store: Persistence | None = None
        self._started = False

    def register(self, spec: EndpointSpec) -> None:
        if spec.name in self._endpoints_by_name:
            raise ValueError(f"Endpoint '{spec.name}' already registered")
        self._endpoints_by_name[spec.name] = spec

    async def start(self) -> None:
        if self._started:
            return
        self._store = Persistence(self.config.storage_path)
        await self._store.connect()
        for spec in self._endpoints_by_name.values():
            handle = BusHandle(self, spec.name)
            await spec.endpoint.start(handle)
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            if self._store is not None:
                await self._store.close()
            return
        for spec in reversed(list(self._endpoints_by_name.values())):
            try:
                await spec.endpoint.stop()
            except Exception:
                log.exception("error stopping endpoint %s", spec.name)
        if self._store is not None:
            await self._store.close()
        self._started = False

    # BusHandle-facing surface — implemented in Tasks 8/9
    async def _enqueue(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        raise NotImplementedError

    async def _ack(self, envelope_id: str) -> None:
        raise NotImplementedError

    async def _nack(self, envelope_id: str, requeue: bool) -> None:
        raise NotImplementedError

    def _endpoints(self) -> list[EndpointInfo]:
        return [
            EndpointInfo(name=spec.name, description=spec.description)
            for spec in self._endpoints_by_name.values()
        ]
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_core_lifecycle.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/bus/core.py tests/bus/test_core_lifecycle.py
git commit -m "feat(bus): add Bus class with registration and start/stop lifecycle"
```

---

## Task 8: Bus core — publish and dispatch flow

**Files:**
- Modify: `src/agent_core/bus/core.py`
- Create: `tests/bus/test_core_dispatch.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bus/test_core_dispatch.py`:

```python
"""Tests for Bus.publish, dispatch flow, and at-least-once delivery."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.protocol import EndpointUnavailable


class _Echo:
    """Endpoint that records deliveries and (optionally) auto-acks them."""

    def __init__(self, name: str, *, raise_unavailable: bool = False, raise_terminal: bool = False, auto_ack: bool = True):
        self.name = name
        self.delivered: list[Envelope] = []
        self.handle = None
        self.raise_unavailable = raise_unavailable
        self.raise_terminal = raise_terminal
        self.auto_ack = auto_ack

    async def start(self, bus) -> None:
        self.handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        if self.raise_unavailable:
            raise EndpointUnavailable("offline")
        if self.raise_terminal:
            raise RuntimeError("boom")
        self.delivered.append(envelope)
        if self.auto_ack:
            await self.handle.ack(envelope.id)

    async def stop(self) -> None:
        pass


def _envelope(id_="e1", to="x", **overrides) -> Envelope:
    fields = dict(
        id=id_,
        correlation_id="c1",
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
    )
    fields.update(overrides)
    return Envelope(**fields)


@pytest.fixture
async def bus(tmp_path: Path) -> Bus:
    b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    yield b
    await b.stop()


class TestPublish:
    async def test_round_trip_via_stub_endpoint(self, bus: Bus):
        echo = _Echo("agent")
        bus.register(EndpointSpec(endpoint=echo))
        await bus.start()
        env = _envelope(to="agent")
        await bus._enqueue(env)
        # Allow the dispatch task to run.
        await asyncio.sleep(0)
        assert len(echo.delivered) == 1
        assert echo.delivered[0].id == "e1"

    async def test_publish_to_unregistered_recipient_rejected(self, bus: Bus):
        await bus.start()
        with pytest.raises(ValueError, match="unregistered endpoint"):
            await bus._enqueue(_envelope(to="nobody"))

    async def test_publish_when_endpoint_offline_queues(self, bus: Bus):
        offline = _Echo("offline", raise_unavailable=True)
        bus.register(EndpointSpec(endpoint=offline))
        await bus.start()
        await bus._enqueue(_envelope(to="offline"))
        await asyncio.sleep(0)
        # Endpoint refused; envelope must be back in pending state.
        row = await bus._store.row("e1")
        assert row["state"] == "pending"
        assert offline.delivered == []

    async def test_terminal_exception_dead_letters(self, bus: Bus):
        bad = _Echo("bad", raise_terminal=True)
        bus.register(EndpointSpec(endpoint=bad))
        await bus.start()
        await bus._enqueue(_envelope(to="bad"))
        await asyncio.sleep(0)
        row = await bus._store.row("e1")
        assert row["state"] == "dead_letter"
        assert "boom" in (row["nack_reason"] or "")

    async def test_mailbox_cap_blocks_overflow(self, tmp_path: Path):
        cfg = BusConfig(
            storage_path=tmp_path / "bus.sqlite", max_pending_per_endpoint=2
        )
        b = Bus(cfg)
        # Register a non-running endpoint via spec (we never start the bus,
        # so deliveries pile up as 'pending').
        b.register(EndpointSpec(endpoint=_Echo("offline", raise_unavailable=True)))
        await b.start()
        await b._enqueue(_envelope("e1", to="offline"))
        await asyncio.sleep(0)
        await b._enqueue(_envelope("e2", to="offline"))
        await asyncio.sleep(0)
        from agent_core.bus.core import MailboxFull

        with pytest.raises(MailboxFull):
            await b._enqueue(_envelope("e3", to="offline"))
        await b.stop()

    async def test_drain_for_redelivers_pending(self, bus: Bus):
        # Endpoint refuses delivery initially; envelope queues as pending.
        # Then we flip it to accept and call drain_for() — must re-dispatch.
        flaky = _Echo("flaky", raise_unavailable=True)
        bus.register(EndpointSpec(endpoint=flaky))
        await bus.start()
        await bus._enqueue(_envelope("e1", to="flaky"))
        await asyncio.sleep(0)
        assert (await bus._store.row("e1"))["state"] == "pending"
        assert flaky.delivered == []

        # Endpoint comes online; drain pending mail.
        flaky.raise_unavailable = False
        await bus.drain_for("flaky")
        await asyncio.sleep(0)
        assert len(flaky.delivered) == 1
        assert flaky.delivered[0].id == "e1"


class TestPublishWithListTo:
    async def test_list_recipients_fans_out(self, bus: Bus):
        a = _Echo("a")
        b_ep = _Echo("b")
        bus.register(EndpointSpec(endpoint=a))
        bus.register(EndpointSpec(endpoint=b_ep))
        await bus.start()
        await bus._enqueue(_envelope("e1", to="a"), to=["a", "b"])
        await asyncio.sleep(0)
        assert len(a.delivered) == 1
        assert len(b_ep.delivered) == 1
        # Each got a distinct envelope id (bus minted N-1 fresh ids).
        assert a.delivered[0].id != b_ep.delivered[0].id
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_core_dispatch.py -v
```

Expected: NotImplementedError on `_enqueue`.

- [ ] **Step 3: Implement publish, dispatch, drain, and MailboxFull**

Edit `src/agent_core/bus/core.py`. Replace the `_enqueue` / `_ack` / `_nack` stubs with the full implementations:

```python
import contextlib
import uuid
from datetime import datetime, timedelta, timezone


class MailboxFull(Exception):
    """Raised when an endpoint's pending mailbox has reached max_pending_per_endpoint."""


class Bus:
    # ... __init__, register, start, stop unchanged ...

    async def _enqueue(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        # Determine recipient list. If `to` provided, override envelope.to.
        recipients: list[str]
        if to is None:
            recipients = [envelope.to]
        elif isinstance(to, str):
            recipients = [to]
        else:
            recipients = list(to)

        for i, recipient in enumerate(recipients):
            if recipient not in self._endpoints_by_name:
                raise ValueError(f"publish to unregistered endpoint '{recipient}'")
            # First recipient reuses the original id; rest get fresh ids.
            new_env = envelope.model_copy(
                update={"id": envelope.id if i == 0 else uuid.uuid4().hex, "to": recipient}
            )
            count = await self._store.count_pending(recipient)
            if count >= self.config.max_pending_per_endpoint:
                raise MailboxFull(
                    f"mailbox '{recipient}' full ({count} pending)"
                )
            await self._store.insert(new_env)
            await self._dispatch(new_env)

    async def _dispatch(self, envelope: Envelope) -> None:
        spec = self._endpoints_by_name.get(envelope.to)
        if spec is None:
            return  # shouldn't happen — caller already checked
        endpoint = spec.endpoint
        in_flight_until = datetime.now(timezone.utc) + timedelta(
            seconds=self.config.redelivery_timeout_seconds
        )
        await self._store.mark_in_flight(envelope.id, in_flight_until)
        try:
            await endpoint.deliver(envelope)
        except Exception as exc:
            from agent_core.bus.protocol import EndpointUnavailable

            if isinstance(exc, EndpointUnavailable):
                # Temporary failure — return to pending; sweep will retry.
                await self._store.requeue(envelope.id)
                log.info(
                    "endpoint %s unavailable; envelope %s requeued: %s",
                    envelope.to, envelope.id, exc,
                )
            else:
                # Terminal failure — dead-letter.
                await self._store.mark_dead_letter(envelope.id, reason=str(exc))
                log.exception(
                    "endpoint %s deliver() raised; dead-lettering envelope %s",
                    envelope.to, envelope.id,
                )

    async def drain_for(self, endpoint_name: str) -> None:
        """Drain persisted-but-pending envelopes addressed to this endpoint.

        Called after an endpoint comes online (start() returns, or a previously
        unavailable endpoint becomes available again).
        """
        pending = await self._store.list_pending(endpoint_name)
        for env in pending:
            await self._dispatch(env)

    async def _ack(self, envelope_id: str) -> None:
        raise NotImplementedError  # Task 9

    async def _nack(self, envelope_id: str, requeue: bool) -> None:
        raise NotImplementedError  # Task 9
```

Then update `start()` to drain after each endpoint comes online:

```python
async def start(self) -> None:
    if self._started:
        return
    self._store = Persistence(self.config.storage_path)
    await self._store.connect()
    for spec in self._endpoints_by_name.values():
        handle = BusHandle(self, spec.name)
        await spec.endpoint.start(handle)
        await self.drain_for(spec.name)
    self._started = True
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_core_dispatch.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/bus/core.py tests/bus/test_core_dispatch.py
git commit -m "feat(bus): add publish, dispatch, drain, and MailboxFull enforcement"
```

---

## Task 9: Bus core — ack and nack

**Files:**
- Modify: `src/agent_core/bus/core.py`
- Create: `tests/bus/test_core_ack.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bus/test_core_ack.py`:

```python
"""Tests for Bus.ack and Bus.nack."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload


class _Inert:
    """Endpoint that records but does not auto-ack."""

    def __init__(self, name: str):
        self.name = name
        self.delivered: list[Envelope] = []

    async def start(self, bus) -> None:
        pass

    async def deliver(self, envelope: Envelope) -> None:
        self.delivered.append(envelope)

    async def stop(self) -> None:
        pass


def _envelope(id_="e1", to="x") -> Envelope:
    return Envelope(
        id=id_, correlation_id="c1", to=to, kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
async def bus(tmp_path: Path) -> Bus:
    b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    yield b
    await b.stop()


class TestAck:
    async def test_ack_marks_acked(self, bus: Bus):
        ep = _Inert("x")
        bus.register(EndpointSpec(endpoint=ep))
        await bus.start()
        await bus._enqueue(_envelope())
        # State should be in_flight after dispatch.
        assert (await bus._store.row("e1"))["state"] == "in_flight"
        await bus._ack("e1")
        assert (await bus._store.row("e1"))["state"] == "acked"

    async def test_ack_idempotent(self, bus: Bus):
        ep = _Inert("x")
        bus.register(EndpointSpec(endpoint=ep))
        await bus.start()
        await bus._enqueue(_envelope())
        await bus._ack("e1")
        # Double ack must not raise.
        await bus._ack("e1")
        assert (await bus._store.row("e1"))["state"] == "acked"

    async def test_ack_unknown_id_silent(self, bus: Bus):
        await bus.start()
        # Acking a non-existent id is a no-op (doesn't raise).
        await bus._ack("never-existed")


class TestNack:
    async def test_nack_with_requeue_returns_to_pending(self, bus: Bus):
        ep = _Inert("x")
        bus.register(EndpointSpec(endpoint=ep))
        await bus.start()
        await bus._enqueue(_envelope())
        await bus._nack("e1", requeue=True)
        assert (await bus._store.row("e1"))["state"] == "pending"

    async def test_nack_no_requeue_dead_letters(self, bus: Bus):
        ep = _Inert("x")
        bus.register(EndpointSpec(endpoint=ep))
        await bus.start()
        await bus._enqueue(_envelope())
        await bus._nack("e1", requeue=False)
        assert (await bus._store.row("e1"))["state"] == "dead_letter"
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_core_ack.py -v
```

Expected: NotImplementedError on `_ack` / `_nack`.

- [ ] **Step 3: Implement `_ack` and `_nack`**

In `src/agent_core/bus/core.py` replace the stubs:

```python
async def _ack(self, envelope_id: str) -> None:
    # Idempotent: marking acked twice (or acking a missing id) is a no-op.
    await self._store.mark_acked(envelope_id)

async def _nack(self, envelope_id: str, requeue: bool) -> None:
    if requeue:
        await self._store.requeue(envelope_id)
    else:
        await self._store.mark_dead_letter(envelope_id, reason="nack")
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_core_ack.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/bus/core.py tests/bus/test_core_ack.py
git commit -m "feat(bus): add idempotent ack and nack with requeue/dead-letter"
```

---

## Task 10: Bus sweeps — TTL and redelivery

**Files:**
- Modify: `src/agent_core/bus/core.py`
- Create: `tests/bus/test_core_sweeps.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bus/test_core_sweeps.py`:

```python
"""Tests for the periodic TTL and redelivery sweeps."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload


class _Stub:
    def __init__(self, name="x"):
        self.name = name

    async def start(self, bus) -> None:
        pass

    async def deliver(self, envelope: Envelope) -> None:
        pass  # leaves state in_flight forever (no auto-ack)

    async def stop(self) -> None:
        pass


def _envelope(**kwargs) -> Envelope:
    fields = dict(
        id="e1",
        correlation_id="c1",
        to="x",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
    )
    fields.update(kwargs)
    return Envelope(**fields)


@pytest.fixture
async def bus(tmp_path: Path) -> Bus:
    cfg = BusConfig(
        storage_path=tmp_path / "bus.sqlite",
        redelivery_timeout_seconds=1,
        max_delivery_attempts=2,
    )
    b = Bus(cfg)
    b.register(EndpointSpec(endpoint=_Stub("x")))
    yield b
    await b.stop()


class TestTTLSweep:
    async def test_expired_pending_marked_expired(self, bus: Bus):
        await bus.start()
        env = _envelope(expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        # Persist directly without dispatch to keep state=pending.
        await bus._store.insert(env)
        await bus.run_ttl_sweep_once(now=datetime(2026, 4, 27, tzinfo=timezone.utc))
        assert (await bus._store.row("e1"))["state"] == "expired"

    async def test_unset_ttl_unaffected(self, bus: Bus):
        await bus.start()
        env = _envelope()  # no expires_at
        await bus._store.insert(env)
        await bus.run_ttl_sweep_once(now=datetime(2026, 4, 27, tzinfo=timezone.utc))
        assert (await bus._store.row("e1"))["state"] == "pending"


class TestRedeliverySweep:
    async def test_stale_in_flight_within_attempt_limit_requeues(self, bus: Bus):
        await bus.start()
        env = _envelope()
        await bus._enqueue(env)  # delivery_count = 1; state in_flight
        # Force in_flight_until into the past.
        await bus._store._conn.execute(
            "UPDATE envelopes SET in_flight_until = ? WHERE id = ?",
            (datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat(), env.id),
        )
        await bus._store._conn.commit()
        await bus.run_redelivery_sweep_once(
            now=datetime(2026, 4, 27, tzinfo=timezone.utc)
        )
        # delivery_count was 1; max is 2, so still re-dispatchable → pending.
        assert (await bus._store.row("e1"))["state"] in ("pending", "in_flight")
        # If pending: dispatch will run again next time. Either way, count <=2.
        assert (await bus._store.row("e1"))["delivery_count"] <= 2

    async def test_exhausted_attempts_dead_letter(self, bus: Bus):
        await bus.start()
        env = _envelope()
        await bus._enqueue(env)
        # Bump delivery_count to max and stale-out the in_flight row.
        await bus._store._conn.execute(
            """UPDATE envelopes
               SET delivery_count = ?, in_flight_until = ?
               WHERE id = ?""",
            (
                bus.config.max_delivery_attempts,
                datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat(),
                env.id,
            ),
        )
        await bus._store._conn.commit()
        await bus.run_redelivery_sweep_once(
            now=datetime(2026, 4, 27, tzinfo=timezone.utc)
        )
        assert (await bus._store.row("e1"))["state"] == "dead_letter"
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_core_sweeps.py -v
```

Expected: AttributeError on `run_ttl_sweep_once` / `run_redelivery_sweep_once`.

- [ ] **Step 3: Implement sweep methods**

Add to `src/agent_core/bus/core.py`:

```python
async def run_ttl_sweep_once(self, *, now: datetime | None = None) -> int:
    """Mark expired-and-undelivered envelopes as 'expired'. Returns count swept."""
    if self._store is None:
        return 0
    now = now or datetime.now(timezone.utc)
    expired = await self._store.find_expired(now=now)
    for env in expired:
        await self._store.expire(env.id)
        log.info("ttl swept envelope %s (to=%s)", env.id, env.to)
    return len(expired)

async def run_redelivery_sweep_once(self, *, now: datetime | None = None) -> int:
    """Find in_flight envelopes whose timeout has lapsed; requeue or dead-letter."""
    if self._store is None:
        return 0
    now = now or datetime.now(timezone.utc)
    stale = await self._store.find_in_flight_timeouts(now=now)
    moved = 0
    for env in stale:
        row = await self._store.row(env.id)
        if row["delivery_count"] >= self.config.max_delivery_attempts:
            await self._store.mark_dead_letter(
                env.id,
                reason=f"exceeded {self.config.max_delivery_attempts} delivery attempts",
            )
        else:
            await self._store.requeue(env.id)
            await self._dispatch(env)
        moved += 1
    return moved
```

(Note: in v1 the periodic asyncio task that calls these on a cadence is added in Task 14 — the `run` CLI command — to keep this task focused on the sweep mechanics.)

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_core_sweeps.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/bus/core.py tests/bus/test_core_sweeps.py
git commit -m "feat(bus): add TTL and redelivery sweeps with attempt-limit dead-letter"
```

---

## Task 11: Hook pipeline — pre_publish and pre_deliver

**Files:**
- Modify: `src/agent_core/bus/core.py`
- Create: `tests/bus/test_hooks.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bus/test_hooks.py`:

```python
"""Tests for the pre_publish and pre_deliver hook pipeline."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, BusHookSpec, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload


class _Echo:
    def __init__(self, name="x"):
        self.name = name
        self.delivered: list[Envelope] = []
        self._handle = None

    async def start(self, bus) -> None:
        self._handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        self.delivered.append(envelope)
        await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        pass


class _RecordingHook:
    def __init__(self, name: str):
        self.name = name
        self.calls: list[tuple[str, str]] = []  # (stage, envelope.id)

    async def execute(self, stage, envelope, params):
        self.calls.append((stage, envelope.id))
        return envelope


class _DropHook:
    async def execute(self, stage, envelope, params):
        return None  # drop


class _MutatingHook:
    async def execute(self, stage, envelope, params):
        return envelope.model_copy(update={"metadata": {**envelope.metadata, "tagged": True}})


def _envelope(id_="e1", to="x") -> Envelope:
    return Envelope(
        id=id_, correlation_id="c1", to=to, kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
async def make_bus(tmp_path: Path):
    async def _make(*, hooks_pre_publish=(), hooks_pre_deliver=()):
        b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
        b.register(EndpointSpec(endpoint=_Echo("x")))
        for h in hooks_pre_publish:
            b.register_hook("pre_publish", BusHookSpec(hook=h, params={}))
        for h in hooks_pre_deliver:
            b.register_hook("pre_deliver", BusHookSpec(hook=h, params={}))
        await b.start()
        return b

    yield _make


class TestHookPipeline:
    async def test_pre_publish_hook_fires(self, make_bus):
        rec = _RecordingHook("rec")
        bus = await make_bus(hooks_pre_publish=[rec])
        await bus._enqueue(_envelope())
        assert ("pre_publish", "e1") in rec.calls
        await bus.stop()

    async def test_pre_deliver_hook_fires(self, make_bus):
        rec = _RecordingHook("rec")
        bus = await make_bus(hooks_pre_deliver=[rec])
        await bus._enqueue(_envelope())
        assert ("pre_deliver", "e1") in rec.calls
        await bus.stop()

    async def test_drop_hook_skips_persist(self, make_bus):
        bus = await make_bus(hooks_pre_publish=[_DropHook()])
        await bus._enqueue(_envelope())
        # Dropped before persist — no row.
        assert await bus._store.row("e1") is None
        await bus.stop()

    async def test_mutating_hook_changes_envelope(self, make_bus):
        bus = await make_bus(hooks_pre_publish=[_MutatingHook()])
        await bus._enqueue(_envelope())
        env = await bus._store.get("e1")
        assert env.metadata == {"tagged": True}
        await bus.stop()

    async def test_from_stamping_runs_before_pre_publish(self, make_bus):
        # Verify that hooks see authenticated `from_`. We use a hook that
        # records the `from_` it sees.
        seen: list[str] = []

        class _SeeFrom:
            async def execute(self, stage, envelope, params):
                if stage == "pre_publish":
                    seen.append(envelope.from_)
                return envelope

        bus = await make_bus(hooks_pre_publish=[_SeeFrom()])
        # Publish via a BusHandle (the only legitimate path) using the registered name.
        # The endpoint's `start()` got a handle — reuse it.
        endpoint = bus._endpoints_by_name["x"].endpoint
        env = _envelope()
        env.from_ = "spoofed"  # try to spoof
        await endpoint._handle.publish(env)
        assert seen == ["x"]  # bus stamped "x" before hooks saw it
        await bus.stop()
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_hooks.py -v
```

Expected: ImportError on `BusHookSpec` / AttributeError on `register_hook`.

- [ ] **Step 3: Add hook plumbing to Bus**

In `src/agent_core/bus/core.py`:

```python
from typing import Literal

from agent_core.bus.protocol import BusHook


@dataclass
class BusHookSpec:
    hook: BusHook
    params: dict = field(default_factory=dict)


class Bus:
    def __init__(self, config: BusConfig):
        self.config = config
        self._endpoints_by_name: dict[str, EndpointSpec] = {}
        self._hooks: dict[str, list[BusHookSpec]] = {
            "pre_publish": [],
            "pre_deliver": [],
        }
        self._store: Persistence | None = None
        self._started = False

    def register_hook(
        self, stage: Literal["pre_publish", "pre_deliver"], spec: BusHookSpec
    ) -> None:
        if stage not in self._hooks:
            raise ValueError(f"unknown hook stage: {stage}")
        self._hooks[stage].append(spec)

    async def _run_hooks(
        self, stage: Literal["pre_publish", "pre_deliver"], envelope: Envelope
    ) -> Envelope | None:
        """Run hooks in registration order. Return the (possibly mutated)
        envelope, or None if any hook dropped it."""
        current = envelope
        for spec in self._hooks[stage]:
            result = await spec.hook.execute(stage, current, spec.params)
            if result is None:
                return None
            current = result
        return current
```

Update `_enqueue` to run pre_publish and `_dispatch` to run pre_deliver:

```python
async def _enqueue(self, envelope: Envelope, to=None) -> None:
    # `from_` was already stamped by BusHandle.publish before we got here,
    # so hooks see authenticated provenance.
    hooked = await self._run_hooks("pre_publish", envelope)
    if hooked is None:
        return  # dropped
    envelope = hooked

    recipients = (
        [envelope.to] if to is None
        else [to] if isinstance(to, str)
        else list(to)
    )
    for i, recipient in enumerate(recipients):
        if recipient not in self._endpoints_by_name:
            raise ValueError(f"publish to unregistered endpoint '{recipient}'")
        new_env = envelope.model_copy(
            update={"id": envelope.id if i == 0 else uuid.uuid4().hex, "to": recipient}
        )
        count = await self._store.count_pending(recipient)
        if count >= self.config.max_pending_per_endpoint:
            raise MailboxFull(f"mailbox '{recipient}' full ({count} pending)")
        await self._store.insert(new_env)
        await self._dispatch(new_env)

async def _dispatch(self, envelope: Envelope) -> None:
    hooked = await self._run_hooks("pre_deliver", envelope)
    if hooked is None:
        return  # dropped between persist and deliver — leave row in pending? No: mark expired-style?
    envelope = hooked
    spec = self._endpoints_by_name.get(envelope.to)
    if spec is None:
        return
    endpoint = spec.endpoint
    in_flight_until = datetime.now(timezone.utc) + timedelta(
        seconds=self.config.redelivery_timeout_seconds
    )
    await self._store.mark_in_flight(envelope.id, in_flight_until)
    try:
        await endpoint.deliver(envelope)
    except Exception as exc:
        from agent_core.bus.protocol import EndpointUnavailable

        if isinstance(exc, EndpointUnavailable):
            await self._store.requeue(envelope.id)
        else:
            await self._store.mark_dead_letter(envelope.id, reason=str(exc))
            log.exception("dead-lettering envelope %s", envelope.id)
```

(For "pre_deliver returns None": dropping at this stage marks the envelope `dead_letter` with reason `"dropped by pre_deliver hook"` — silent drops at pre_deliver leave behavior ambiguous. Update accordingly.)

Update the dropped-on-pre_deliver branch:

```python
if hooked is None:
    await self._store.mark_dead_letter(envelope.id, reason="dropped by pre_deliver hook")
    return
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_hooks.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/bus/core.py tests/bus/test_hooks.py
git commit -m "feat(bus): add pre_publish and pre_deliver hook pipeline"
```

---

## Task 12: Stub endpoint — for tests and dev

**Files:**
- Create: `src/agent_core/endpoints/stub.py`
- Create: `tests/bus/test_endpoints_stub.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bus/test_endpoints_stub.py`:

```python
"""Tests for the StubEndpoint — a simple echo/inbox adapter for testing."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.stub import StubEndpoint


def _envelope(id_="e1", to="stub") -> Envelope:
    return Envelope(
        id=id_, correlation_id="c1", to=to, kind="TextMessage",
        payload=TextMessagePayload(text="hello"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
async def bus(tmp_path: Path) -> Bus:
    b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    yield b
    await b.stop()


class TestStubEndpoint:
    async def test_inbox_collects_delivered_envelopes(self, bus: Bus):
        stub = StubEndpoint(name="stub")
        bus.register(EndpointSpec(endpoint=stub))
        await bus.start()
        await bus._enqueue(_envelope())
        assert len(stub.inbox) == 1
        assert stub.inbox[0].id == "e1"

    async def test_auto_acks_by_default(self, bus: Bus):
        stub = StubEndpoint(name="stub")
        bus.register(EndpointSpec(endpoint=stub))
        await bus.start()
        await bus._enqueue(_envelope())
        assert (await bus._store.row("e1"))["state"] == "acked"

    async def test_auto_ack_disabled(self, bus: Bus):
        stub = StubEndpoint(name="stub", auto_ack=False)
        bus.register(EndpointSpec(endpoint=stub))
        await bus.start()
        await bus._enqueue(_envelope())
        assert (await bus._store.row("e1"))["state"] == "in_flight"

    async def test_publish_helper_fans_out(self, bus: Bus):
        sender = StubEndpoint(name="sender")
        receiver = StubEndpoint(name="receiver")
        bus.register(EndpointSpec(endpoint=sender))
        bus.register(EndpointSpec(endpoint=receiver))
        await bus.start()
        await sender.send(
            to="receiver",
            kind="TextMessage",
            payload=TextMessagePayload(text="from sender"),
        )
        assert len(receiver.inbox) == 1
        assert receiver.inbox[0].from_ == "sender"  # bus-stamped
        assert receiver.inbox[0].payload.text == "from sender"
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_endpoints_stub.py -v
```

Expected: ImportError on `agent_core.endpoints.stub`.

- [ ] **Step 3: Implement StubEndpoint**

Create `src/agent_core/endpoints/stub.py`:

```python
"""StubEndpoint — a simple in-memory adapter for tests and development.

Records every envelope delivered to it on a `.inbox` list. Optionally
auto-acks. Provides a `.send()` helper for tests that want to publish from
the stub's identity.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from agent_core.bus.envelope import Envelope, EnvelopePayload

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle


class StubEndpoint:
    """A minimal Endpoint suitable for tests and as a dev-mode echo."""

    def __init__(self, name: str, *, auto_ack: bool = True):
        self.name = name
        self.auto_ack = auto_ack
        self.inbox: list[Envelope] = []
        self._handle: "BusHandle | None" = None

    async def start(self, bus: "BusHandle") -> None:
        self._handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        self.inbox.append(envelope)
        if self.auto_ack and self._handle is not None:
            await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        self._handle = None

    async def send(
        self,
        *,
        to: str,
        kind: str,
        payload: EnvelopePayload,
        correlation_id: str | None = None,
        in_reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        """Convenience: build and publish an envelope from this endpoint."""
        if self._handle is None:
            raise RuntimeError(f"endpoint '{self.name}' is not started")
        env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=correlation_id or uuid.uuid4().hex,
            in_reply_to=in_reply_to,
            to=to,
            kind=kind,  # type: ignore[arg-type]
            payload=payload,
            metadata=metadata or {},
            expires_at=expires_at,
            created_at=datetime.now(timezone.utc),
        )
        await self._handle.publish(env)
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_endpoints_stub.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/endpoints/stub.py tests/bus/test_endpoints_stub.py
git commit -m "feat(endpoints): add StubEndpoint for testing and dev"
```

---

## Task 13: Runner — load YAML and boot

**Files:**
- Create: `src/agent_core/bus/runner.py`
- Create: `tests/bus/test_runner.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bus/test_runner.py`:

```python
"""Tests for the Bus runner — load YAML, instantiate, start."""

from pathlib import Path

import pytest
import yaml

from agent_core.bus.runner import build_bus_from_config, BusBootError


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    config = {
        "bus": {
            "storage_path": str(tmp_path / "bus.sqlite"),
            "redelivery_timeout_seconds": 60,
            "max_delivery_attempts": 3,
            "max_pending_per_endpoint": 100,
        },
        "http": {"bind_host": "127.0.0.1", "bind_port": 18788},
        "endpoints": [
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "stub-a",
                "description": "First stub.",
                "params": {"auto_ack": True},
            },
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "stub-b",
                "description": "Second stub.",
                "params": {},
            },
        ],
        "bus_hooks": {"pre_publish": [], "pre_deliver": []},
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


class TestRunner:
    async def test_loads_endpoints(self, cfg_path: Path):
        bus = await build_bus_from_config(cfg_path)
        try:
            await bus.start()
            names = {info.name for info in bus._endpoints()}
            assert names == {"stub-a", "stub-b"}
            descs = {info.name: info.description for info in bus._endpoints()}
            assert descs["stub-a"] == "First stub."
        finally:
            await bus.stop()

    async def test_unknown_class_raises(self, tmp_path: Path):
        config = {
            "endpoints": [
                {
                    "class": "agent_core.endpoints.does_not_exist.Foo",
                    "name": "x",
                    "params": {},
                }
            ]
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(BusBootError):
            await build_bus_from_config(p)

    async def test_class_not_endpoint_protocol(self, tmp_path: Path):
        # Pick something that's importable but doesn't satisfy Endpoint.
        config = {
            "endpoints": [
                {"class": "datetime.datetime", "name": "x", "params": {}}
            ]
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(BusBootError, match="does not satisfy Endpoint"):
            await build_bus_from_config(p)

    async def test_non_loopback_bind_refused(self, tmp_path: Path):
        config = {
            "http": {"bind_host": "0.0.0.0", "bind_port": 8788},
            "endpoints": [],
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(BusBootError, match="loopback"):
            await build_bus_from_config(p)
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_runner.py -v
```

Expected: ImportError on `agent_core.bus.runner`.

- [ ] **Step 3: Implement runner**

Create `src/agent_core/bus/runner.py`:

```python
"""Boot sequence — load YAML, instantiate endpoints, register, start.

The runner is the only place that imports endpoint classes by string. It also
enforces v1 invariants: loopback-only bind unless an auth hook is configured
(BACKLOG: auth for non-loopback bind).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import yaml

from agent_core.bus.core import Bus, BusConfig, BusHookSpec, EndpointSpec
from agent_core.bus.protocol import BusHook, Endpoint


class BusBootError(Exception):
    """Raised when the runner cannot construct a valid Bus from the config."""


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _import_class(path: str) -> Any:
    module_path, _, class_name = path.rpartition(".")
    if not module_path:
        raise BusBootError(f"invalid class path: {path!r}")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise BusBootError(f"cannot import {module_path!r}: {exc}") from exc
    try:
        return getattr(module, class_name)
    except AttributeError as exc:
        raise BusBootError(f"{module_path!r} has no attribute {class_name!r}") from exc


def _validate_http(http_cfg: dict, has_auth_hook: bool) -> None:
    host = http_cfg.get("bind_host", "127.0.0.1")
    if host not in _LOOPBACK_HOSTS and not has_auth_hook:
        raise BusBootError(
            f"http.bind_host={host!r} is non-loopback but no auth hook is configured. "
            "v1 supports loopback only; see BACKLOG for the auth hook trigger."
        )


async def build_bus_from_config(path: Path) -> Bus:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    bus_cfg_raw = raw.get("bus", {})
    storage_path = Path(
        bus_cfg_raw.get("storage_path", "~/.agent-core/bus.sqlite")
    ).expanduser()
    cfg = BusConfig(
        storage_path=storage_path,
        redelivery_timeout_seconds=bus_cfg_raw.get("redelivery_timeout_seconds", 300),
        max_delivery_attempts=bus_cfg_raw.get("max_delivery_attempts", 5),
        ttl_sweep_seconds=bus_cfg_raw.get("ttl_sweep_seconds", 60),
        redelivery_sweep_seconds=bus_cfg_raw.get("redelivery_sweep_seconds", 10),
        acked_retention_days=bus_cfg_raw.get("acked_retention_days", 14),
        max_pending_per_endpoint=bus_cfg_raw.get("max_pending_per_endpoint", 10_000),
    )

    bus = Bus(cfg)

    # Hooks (no auth-aware filtering yet — Phase 2 will add it).
    has_auth_hook = False  # No auth hook in v1.
    for stage in ("pre_publish", "pre_deliver"):
        for entry in (raw.get("bus_hooks", {}) or {}).get(stage, []) or []:
            cls = _import_class(entry["class"])
            instance = cls(**entry.get("params", {}))
            if not isinstance(instance, BusHook):
                raise BusBootError(
                    f"{entry['class']!r} does not satisfy BusHook protocol"
                )
            bus.register_hook(stage, BusHookSpec(hook=instance, params=entry.get("params", {})))

    # HTTP guardrail.
    http_cfg = raw.get("http", {})
    _validate_http(http_cfg, has_auth_hook)

    # Endpoints.
    for entry in raw.get("endpoints", []) or []:
        cls = _import_class(entry["class"])
        params = entry.get("params", {})
        # Endpoint constructors typically take `name` and `**params` patterns.
        # Convention: every endpoint class accepts `name` as first arg.
        instance = cls(name=entry["name"], **params)
        if not isinstance(instance, Endpoint):
            raise BusBootError(
                f"{entry['class']!r} does not satisfy Endpoint protocol"
            )
        bus.register(EndpointSpec(endpoint=instance, description=entry.get("description", "")))

    return bus
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_runner.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/bus/runner.py tests/bus/test_runner.py
git commit -m "feat(bus): add runner that loads YAML, validates, and boots the bus"
```

---

## Task 14: CLI — `agent-core bus run`

**Files:**
- Create: `src/agent_core/bus/cli.py`
- Modify: `src/agent_core/cli.py`
- Create: `tests/bus/test_cli_run.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bus/test_cli_run.py`:

```python
"""Tests for `agent-core bus run` — the long-running entry point."""

import asyncio
import signal
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agent_core.cli import app


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    config = {
        "bus": {"storage_path": str(tmp_path / "bus.sqlite")},
        "endpoints": [
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "stub",
                "description": "test",
                "params": {},
            }
        ],
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


class TestBusRunCLI:
    def test_run_help(self):
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "run", "--help"])
        assert result.exit_code == 0
        assert "config" in result.output.lower()

    def test_run_with_invalid_config(self, tmp_path: Path):
        p = tmp_path / "missing.yaml"
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "run", "--config", str(p)])
        assert result.exit_code != 0

    @pytest.mark.skipif(sys.platform == "win32", reason="signal handling differs on Windows")
    async def test_run_starts_and_stops_on_sigint(self, cfg_path: Path):
        """End-to-end: start the bus subprocess, send SIGINT, verify graceful exit."""
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "agent_core.cli", "bus", "run",
            "--config", str(cfg_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.sleep(1.0)  # let it boot
        proc.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            pytest.fail("bus did not shut down on SIGINT")
        assert proc.returncode == 0
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_cli_run.py -v
```

Expected: registration errors / unknown command for `bus run`.

- [ ] **Step 3: Implement `bus run` and CLI wiring**

Create `src/agent_core/bus/cli.py`:

```python
"""CLI for `agent-core bus *` subcommands."""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

import typer
from rich.console import Console

from agent_core.bus.runner import BusBootError, build_bus_from_config

app = typer.Typer(help="Bus operations: run, status, mailbox, trace, dlq, replay.")
console = Console()
log = logging.getLogger(__name__)


@app.command()
def run(
    config: Path = typer.Option(
        Path("./agent_core.yaml"),
        "--config",
        "-c",
        help="Path to agent_core.yaml",
        exists=True,
        readable=True,
    ),
) -> None:
    """Start the bus and all configured endpoints. Runs until SIGINT/SIGTERM."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(_run_bus(config))
    except BusBootError as exc:
        console.print(f"[red]boot error:[/red] {exc}")
        raise typer.Exit(code=1)


async def _run_bus(config_path: Path) -> None:
    bus = await build_bus_from_config(config_path)
    await bus.start()
    console.print(
        f"[green]bus running[/green] — {len(bus._endpoints_by_name)} endpoint(s); "
        "press Ctrl+C to stop."
    )

    stop_event = asyncio.Event()

    def _shutdown(*_):
        stop_event.set()

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _shutdown)
        loop.add_signal_handler(signal.SIGTERM, _shutdown)
    except NotImplementedError:
        # Windows — fall through; SIGINT will raise KeyboardInterrupt.
        pass

    # Sweep tasks
    async def _ttl_loop():
        while not stop_event.is_set():
            try:
                await bus.run_ttl_sweep_once()
            except Exception:
                log.exception("TTL sweep failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=bus.config.ttl_sweep_seconds)
            except TimeoutError:
                pass

    async def _redelivery_loop():
        while not stop_event.is_set():
            try:
                await bus.run_redelivery_sweep_once()
            except Exception:
                log.exception("redelivery sweep failed")
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=bus.config.redelivery_sweep_seconds
                )
            except TimeoutError:
                pass

    sweeps = [asyncio.create_task(_ttl_loop()), asyncio.create_task(_redelivery_loop())]

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        for t in sweeps:
            t.cancel()
        await asyncio.gather(*sweeps, return_exceptions=True)
        await bus.stop()
        console.print("[yellow]bus stopped[/yellow]")
```

Modify `src/agent_core/cli.py` to register the `bus` subcommand group. Find the existing `app = typer.Typer(...)` definition and add:

```python
from agent_core.bus.cli import app as bus_app

app.add_typer(bus_app, name="bus")
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_cli_run.py -v
```

Expected: all tests pass (the SIGINT test is skipped on Windows).

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/bus/cli.py src/agent_core/cli.py tests/bus/test_cli_run.py
git commit -m "feat(cli): add 'agent-core bus run' with sweep loops and SIGINT shutdown"
```

---

## Task 15: CLI — `bus status` and `bus mailbox`

**Files:**
- Modify: `src/agent_core/bus/cli.py`
- Create: `tests/bus/test_cli_status.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bus/test_cli_status.py`:

```python
"""Tests for `agent-core bus status` and `agent-core bus mailbox`."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.persistence import Persistence
from agent_core.cli import app


def _write_config(tmp_path: Path) -> Path:
    config = {
        "bus": {"storage_path": str(tmp_path / "bus.sqlite")},
        "endpoints": [
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "stub-a",
                "description": "first",
                "params": {},
            },
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "stub-b",
                "description": "second",
                "params": {},
            },
        ],
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


def _seed(tmp_path: Path):
    """Pre-populate the SQLite store with envelopes."""
    import asyncio

    async def _go():
        store = Persistence(tmp_path / "bus.sqlite")
        await store.connect()
        for i in range(3):
            await store.insert(
                Envelope(
                    id=f"e{i}",
                    correlation_id="c1",
                    from_="stub-a",
                    to="stub-b",
                    kind="TextMessage",
                    payload=TextMessagePayload(text=f"msg {i}"),
                    created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
                )
            )
        await store.mark_dead_letter("e2", reason="test")
        await store.close()

    asyncio.run(_go())


class TestStatus:
    def test_status_lists_endpoints(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        _seed(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "status", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "stub-a" in result.output
        assert "stub-b" in result.output
        assert "dlq" in result.output.lower() or "dead" in result.output.lower()


class TestMailbox:
    def test_mailbox_lists_pending(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        _seed(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            app, ["bus", "mailbox", "stub-b", "--config", str(cfg)]
        )
        assert result.exit_code == 0
        # e0 and e1 are pending; e2 is dead-letter, should not appear.
        assert "e0" in result.output
        assert "e1" in result.output
        assert "e2" not in result.output

    def test_mailbox_unknown_endpoint(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        _seed(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            app, ["bus", "mailbox", "nobody", "--config", str(cfg)]
        )
        assert result.exit_code != 0
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_cli_status.py -v
```

Expected: unknown subcommand errors.

- [ ] **Step 3: Implement `status` and `mailbox`**

Append to `src/agent_core/bus/cli.py`:

```python
from rich.table import Table

from agent_core.bus.persistence import Persistence
from agent_core.bus.runner import build_bus_from_config


def _config_option():
    return typer.Option(
        Path("./agent_core.yaml"),
        "--config",
        "-c",
        exists=True,
        readable=True,
    )


@app.command()
def status(config: Path = _config_option()):
    """Show endpoints, in-flight count, and DLQ depth."""
    asyncio.run(_status(config))


async def _status(config_path: Path) -> None:
    bus = await build_bus_from_config(config_path)
    store = Persistence(bus.config.storage_path)
    await store.connect()
    try:
        # Endpoint table
        ep_table = Table(title="Endpoints")
        ep_table.add_column("name")
        ep_table.add_column("description")
        ep_table.add_column("pending")
        for spec in bus._endpoints_by_name.values():
            count = await store.count_pending(spec.name)
            ep_table.add_row(spec.name, spec.description, str(count))
        console.print(ep_table)

        # Aggregate counts
        async with store._conn.execute(
            "SELECT state, COUNT(*) FROM envelopes GROUP BY state"
        ) as cur:
            rows = await cur.fetchall()
        agg = Table(title="State counts")
        agg.add_column("state")
        agg.add_column("count")
        for state, count in rows:
            agg.add_row(state, str(count))
        console.print(agg)
    finally:
        await store.close()


@app.command()
def mailbox(
    endpoint: str = typer.Argument(..., help="Endpoint name to inspect"),
    config: Path = _config_option(),
):
    """List pending envelopes for an endpoint."""
    asyncio.run(_mailbox(endpoint, config))


async def _mailbox(endpoint: str, config_path: Path) -> None:
    bus = await build_bus_from_config(config_path)
    if endpoint not in bus._endpoints_by_name:
        console.print(f"[red]unknown endpoint:[/red] {endpoint}")
        raise typer.Exit(code=1)
    store = Persistence(bus.config.storage_path)
    await store.connect()
    try:
        pending = await store.list_pending(endpoint)
        if not pending:
            console.print(f"[dim]mailbox '{endpoint}' is empty[/dim]")
            return
        table = Table(title=f"Mailbox: {endpoint} ({len(pending)} pending)")
        table.add_column("id")
        table.add_column("from")
        table.add_column("kind")
        table.add_column("created_at")
        for env in pending:
            table.add_row(env.id, env.from_, env.kind, env.created_at.isoformat())
        console.print(table)
    finally:
        await store.close()
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_cli_status.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/bus/cli.py tests/bus/test_cli_status.py
git commit -m "feat(cli): add 'bus status' and 'bus mailbox <endpoint>' commands"
```

---

## Task 16: CLI — `bus trace`

**Files:**
- Modify: `src/agent_core/bus/cli.py`
- Create: `tests/bus/test_cli_trace.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bus/test_cli_trace.py`:

```python
"""Tests for `agent-core bus trace <correlation_id>`."""

from datetime import datetime, timezone
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.persistence import Persistence
from agent_core.cli import app


def _write_config(tmp_path: Path) -> Path:
    config = {
        "bus": {"storage_path": str(tmp_path / "bus.sqlite")},
        "endpoints": [
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "a",
                "params": {},
            }
        ],
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


def _seed_thread(tmp_path: Path):
    import asyncio

    async def _go():
        store = Persistence(tmp_path / "bus.sqlite")
        await store.connect()
        for i, eid in enumerate(["a1", "a2", "a3"]):
            await store.insert(
                Envelope(
                    id=eid,
                    correlation_id="thread-1",
                    from_="discord",
                    to="a",
                    kind="TextMessage",
                    payload=TextMessagePayload(text=f"msg {i}"),
                    created_at=datetime(2026, 4, 27, 12, i, 0, tzinfo=timezone.utc),
                )
            )
        # Unrelated thread
        await store.insert(
            Envelope(
                id="other",
                correlation_id="thread-2",
                from_="discord",
                to="a",
                kind="TextMessage",
                payload=TextMessagePayload(text="unrelated"),
                created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
            )
        )
        await store.close()

    asyncio.run(_go())


class TestTrace:
    def test_trace_returns_thread(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        _seed_thread(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "trace", "thread-1", "--config", str(cfg)])
        assert result.exit_code == 0
        for eid in ("a1", "a2", "a3"):
            assert eid in result.output
        assert "other" not in result.output
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_cli_trace.py -v
```

Expected: unknown subcommand `trace`.

- [ ] **Step 3: Implement `trace`**

Append to `src/agent_core/bus/cli.py`:

```python
@app.command()
def trace(
    correlation_id: str = typer.Argument(..., help="correlation_id to trace"),
    config: Path = _config_option(),
):
    """Show all envelopes in a correlation_id thread, in arrival order."""
    asyncio.run(_trace(correlation_id, config))


async def _trace(correlation_id: str, config_path: Path) -> None:
    bus = await build_bus_from_config(config_path)
    store = Persistence(bus.config.storage_path)
    await store.connect()
    try:
        thread = await store.list_by_correlation(correlation_id)
        if not thread:
            console.print(
                f"[dim]no envelopes found for correlation_id={correlation_id!r}[/dim]"
            )
            return
        table = Table(title=f"Thread: {correlation_id}")
        table.add_column("id")
        table.add_column("from")
        table.add_column("to")
        table.add_column("kind")
        table.add_column("created_at")
        for env in thread:
            table.add_row(env.id, env.from_, env.to, env.kind, env.created_at.isoformat())
        console.print(table)
    finally:
        await store.close()
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_cli_trace.py -v
```

Expected: 1 test passes.

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/bus/cli.py tests/bus/test_cli_trace.py
git commit -m "feat(cli): add 'bus trace <correlation_id>' command"
```

---

## Task 17: CLI — `bus dlq`, `replay`, `dlq purge`

**Files:**
- Modify: `src/agent_core/bus/cli.py`
- Modify: `src/agent_core/bus/persistence.py` (add `purge_dlq`)
- Create: `tests/bus/test_cli_dlq.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bus/test_cli_dlq.py`:

```python
"""Tests for `agent-core bus dlq`, `replay`, and `dlq purge`."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.persistence import Persistence
from agent_core.cli import app


def _write_config(tmp_path: Path) -> Path:
    config = {
        "bus": {"storage_path": str(tmp_path / "bus.sqlite")},
        "endpoints": [
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "stub",
                "params": {},
            }
        ],
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


def _seed_dlq(tmp_path: Path):
    import asyncio

    async def _go():
        store = Persistence(tmp_path / "bus.sqlite")
        await store.connect()
        for i in range(3):
            await store.insert(
                Envelope(
                    id=f"d{i}",
                    correlation_id="c",
                    from_="discord",
                    to="stub",
                    kind="TextMessage",
                    payload=TextMessagePayload(text=f"failure {i}"),
                    created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
                )
            )
            await store.mark_dead_letter(f"d{i}", reason=f"reason-{i}")
        # One healthy envelope
        await store.insert(
            Envelope(
                id="ok",
                correlation_id="c2",
                from_="discord",
                to="stub",
                kind="TextMessage",
                payload=TextMessagePayload(text="ok"),
                created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
            )
        )
        await store.close()

    asyncio.run(_go())


class TestDLQ:
    def test_dlq_lists_dead_letter_only(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        _seed_dlq(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "dlq", "--config", str(cfg)])
        assert result.exit_code == 0
        for eid in ("d0", "d1", "d2"):
            assert eid in result.output
        assert "ok" not in result.output

    def test_replay_resets_state(self, tmp_path: Path):
        import asyncio

        cfg = _write_config(tmp_path)
        _seed_dlq(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "replay", "d0", "--config", str(cfg)])
        assert result.exit_code == 0

        async def _check():
            store = Persistence(tmp_path / "bus.sqlite")
            await store.connect()
            row = await store.row("d0")
            await store.close()
            return row

        row = asyncio.run(_check())
        assert row["state"] == "pending"
        assert row["delivery_count"] == 0

    def test_replay_unknown_id(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "replay", "nope", "--config", str(cfg)])
        assert result.exit_code != 0

    def test_purge_older_than(self, tmp_path: Path):
        import asyncio

        cfg = _write_config(tmp_path)
        _seed_dlq(tmp_path)

        # Backdate one of the dead-letter rows.
        async def _backdate():
            store = Persistence(tmp_path / "bus.sqlite")
            await store.connect()
            past = (datetime(2026, 4, 27, tzinfo=timezone.utc) - timedelta(days=10)).isoformat()
            await store._conn.execute(
                "UPDATE envelopes SET last_attempted = ? WHERE id = 'd0'", (past,)
            )
            await store._conn.commit()
            await store.close()

        asyncio.run(_backdate())

        runner = CliRunner()
        result = runner.invoke(
            app, ["bus", "dlq", "purge", "--older-than", "7d", "--config", str(cfg)]
        )
        assert result.exit_code == 0

        async def _check():
            store = Persistence(tmp_path / "bus.sqlite")
            await store.connect()
            row = await store.row("d0")
            await store.close()
            return row

        # d0 was older than 7d → purged (row deleted)
        assert asyncio.run(_check()) is None
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
uv run pytest tests/bus/test_cli_dlq.py -v
```

Expected: unknown subcommand errors.

- [ ] **Step 3: Add `purge_dlq` to Persistence**

In `src/agent_core/bus/persistence.py`:

```python
async def purge_dlq(self, *, older_than: datetime) -> int:
    """Delete dead_letter rows whose last_attempted is older than the cutoff.
    Returns the number of rows deleted."""
    cur = await self._conn.execute(
        """DELETE FROM envelopes
           WHERE state = 'dead_letter'
             AND last_attempted IS NOT NULL
             AND last_attempted < ?""",
        (older_than.isoformat(),),
    )
    await self._conn.commit()
    return cur.rowcount

async def reset_for_replay(self, id_: str) -> bool:
    """Reset a dead_letter row to pending; reset delivery_count.
    Returns True if a row was changed."""
    cur = await self._conn.execute(
        """UPDATE envelopes
           SET state = 'pending',
               delivery_count = 0,
               in_flight_until = NULL,
               nack_reason = NULL
           WHERE id = ? AND state = 'dead_letter'""",
        (id_,),
    )
    await self._conn.commit()
    return cur.rowcount == 1
```

- [ ] **Step 4: Implement `dlq`, `replay`, `dlq purge` CLI commands**

We use a Typer sub-app for `dlq` so that both `bus dlq` (list) and
`bus dlq purge --older-than 7d` work without a separate top-level
`dlq-purge` command.

Append to `src/agent_core/bus/cli.py`:

```python
import re
from datetime import datetime, timedelta, timezone


# Sub-app for `bus dlq` and `bus dlq purge`.
dlq_app = typer.Typer(help="Dead-letter operations.", invoke_without_command=True)
app.add_typer(dlq_app, name="dlq")


@dlq_app.callback(invoke_without_command=True)
def _dlq_default(ctx: typer.Context, config: Path = _config_option()):
    """List dead-letter envelopes (when `bus dlq` is invoked with no subcommand)."""
    if ctx.invoked_subcommand is None:
        asyncio.run(_dlq_list(config))


async def _dlq_list(config_path: Path) -> None:
    bus = await build_bus_from_config(config_path)
    store = Persistence(bus.config.storage_path)
    await store.connect()
    try:
        rows = await store.list_dead_letter()
        if not rows:
            console.print("[dim]DLQ is empty[/dim]")
            return
        table = Table(title=f"Dead-Letter Queue ({len(rows)})")
        table.add_column("id")
        table.add_column("from")
        table.add_column("to")
        table.add_column("kind")
        table.add_column("reason")
        for env in rows:
            row = await store.row(env.id)
            table.add_row(env.id, env.from_, env.to, env.kind, row["nack_reason"] or "")
        console.print(table)
    finally:
        await store.close()


@app.command()
def replay(
    envelope_id: str = typer.Argument(..., help="Envelope id to replay"),
    config: Path = _config_option(),
):
    """Reset a dead-letter envelope to pending and re-queue."""
    asyncio.run(_replay(envelope_id, config))


async def _replay(envelope_id: str, config_path: Path) -> None:
    bus = await build_bus_from_config(config_path)
    store = Persistence(bus.config.storage_path)
    await store.connect()
    try:
        ok = await store.reset_for_replay(envelope_id)
        if not ok:
            console.print(f"[red]envelope {envelope_id!r} not found in DLQ[/red]")
            raise typer.Exit(code=1)
        console.print(f"[green]replayed:[/green] {envelope_id}")
    finally:
        await store.close()


_DURATION_RE = re.compile(r"^(\d+)([dhm])$")


def _parse_duration(s: str) -> timedelta:
    m = _DURATION_RE.match(s.strip().lower())
    if not m:
        raise typer.BadParameter(f"invalid duration: {s!r} (use e.g. '7d', '12h', '30m')")
    n, unit = int(m.group(1)), m.group(2)
    if unit == "d":
        return timedelta(days=n)
    if unit == "h":
        return timedelta(hours=n)
    return timedelta(minutes=n)


@dlq_app.command("purge")
def dlq_purge(
    older_than: str = typer.Option(..., "--older-than"),
    config: Path = _config_option(),
):
    """Delete dead-letter envelopes older than the given duration (e.g. 7d, 24h)."""
    asyncio.run(_dlq_purge(older_than, config))


async def _dlq_purge(older_than: str, config_path: Path) -> None:
    bus = await build_bus_from_config(config_path)
    store = Persistence(bus.config.storage_path)
    await store.connect()
    try:
        cutoff = datetime.now(timezone.utc) - _parse_duration(older_than)
        n = await store.purge_dlq(older_than=cutoff)
        console.print(f"[green]purged {n} envelope(s) older than {older_than}[/green]")
    finally:
        await store.close()
```

- [ ] **Step 5: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_cli_dlq.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/agent_core/bus/cli.py src/agent_core/bus/persistence.py tests/bus/test_cli_dlq.py
git commit -m "feat(cli): add 'bus dlq', 'bus replay', and 'bus dlq purge' commands"
```

---

## Task 18: End-to-end integration test

**Files:**
- Create: `tests/bus/test_integration.py`
- Modify: `agent_core.yaml` (add example bus config block alongside existing pipelines)

- [ ] **Step 1: Write the integration test**

Create `tests/bus/test_integration.py`:

```python
"""End-to-end integration test for Phase 1 — bus + stub endpoints + sweeps."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from agent_core.bus.envelope import TextMessagePayload
from agent_core.bus.runner import build_bus_from_config


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    config = {
        "bus": {
            "storage_path": str(tmp_path / "bus.sqlite"),
            "redelivery_timeout_seconds": 1,
            "max_delivery_attempts": 2,
            "ttl_sweep_seconds": 1,
            "redelivery_sweep_seconds": 1,
            "max_pending_per_endpoint": 1000,
        },
        "endpoints": [
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "alice",
                "description": "First test endpoint.",
                "params": {},
            },
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "bob",
                "description": "Second test endpoint.",
                "params": {},
            },
        ],
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


class TestE2E:
    async def test_alice_sends_to_bob(self, cfg_path: Path):
        bus = await build_bus_from_config(cfg_path)
        await bus.start()
        try:
            alice = bus._endpoints_by_name["alice"].endpoint
            bob = bus._endpoints_by_name["bob"].endpoint

            await alice.send(
                to="bob",
                kind="TextMessage",
                payload=TextMessagePayload(text="hello bob"),
            )
            assert len(bob.inbox) == 1
            assert bob.inbox[0].from_ == "alice"
            assert bob.inbox[0].payload.text == "hello bob"
        finally:
            await bus.stop()

    async def test_persistence_survives_restart(self, cfg_path: Path):
        # First run: queue an envelope to a (deliberately not-running) recipient.
        bus1 = await build_bus_from_config(cfg_path)
        await bus1.start()
        try:
            alice = bus1._endpoints_by_name["alice"].endpoint
            bob = bus1._endpoints_by_name["bob"].endpoint
            # Stop bob so deliveries queue.
            await bob.stop()
            del bus1._endpoints_by_name["bob"]
            await alice.send(
                to="alice",  # send to self instead — bob is no longer registered
                kind="TextMessage",
                payload=TextMessagePayload(text="for me"),
            )
        finally:
            await bus1.stop()

        # Second run: trace shows the envelope persisted.
        from agent_core.bus.persistence import Persistence
        cfg = yaml.safe_load(cfg_path.read_text())
        store = Persistence(Path(cfg["bus"]["storage_path"]))
        await store.connect()
        try:
            async with store._conn.execute(
                "SELECT COUNT(*) FROM envelopes"
            ) as cur:
                count = (await cur.fetchone())[0]
            assert count >= 1
        finally:
            await store.close()

    async def test_ttl_expires_unrouted_message(self, cfg_path: Path):
        bus = await build_bus_from_config(cfg_path)
        await bus.start()
        try:
            from agent_core.bus.envelope import Envelope
            import uuid

            past = datetime.now(timezone.utc) - timedelta(hours=1)
            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id="c",
                from_="alice",
                to="alice",
                kind="TextMessage",
                payload=TextMessagePayload(text="stale"),
                expires_at=past,
                created_at=datetime.now(timezone.utc),
            )
            await bus._store.insert(env)
            await bus.run_ttl_sweep_once()
            assert (await bus._store.row(env.id))["state"] == "expired"
        finally:
            await bus.stop()
```

- [ ] **Step 2: Update example `agent_core.yaml`**

Modify `agent_core.yaml` (existing file; preserve the `pipelines` block) to add bus config:

```yaml
# Example bus config for development. Real use overrides via per-agent
# config or env vars.
bus:
  storage_path: ~/.agent-core/bus.sqlite
  redelivery_timeout_seconds: 300
  max_delivery_attempts: 5
  ttl_sweep_seconds: 60
  redelivery_sweep_seconds: 10
  max_pending_per_endpoint: 10000

http:
  bind_host: 127.0.0.1
  bind_port: 8788

endpoints:
  - class: agent_core.endpoints.stub.StubEndpoint
    name: dev-stub
    description: "Dev/test stub endpoint."
    params: {}

bus_hooks:
  pre_publish: []
  pre_deliver: []

# Existing — Claude Code lifecycle hooks (untouched).
pipelines:
  SessionStart:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector
      params:
        format: "%A, %B %d, %Y %I:%M %p %Z"
        track_session: true
  UserPromptSubmit:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector
      params:
        format: "%A, %B %d, %Y %I:%M %p %Z"
        track_session: true
  SessionEnd:
    - tool: agent_core.hooks.tools.handoff_writer.HandoffWriter
      params:
        output_path: "E:\\workspaces\\ai\\agents\\agent_core\\tests\\hooks\\test-handoff.md"
        transcript_tail_lines: 200
        timezone: "US/Eastern"
        agent_name: "TestAgent"
```

- [ ] **Step 3: Run tests and confirm they pass**

```bash
uv run pytest tests/bus/test_integration.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 4: Run the entire bus test suite to verify no regressions**

```bash
uv run pytest tests/bus -v
```

Expected: every Phase 1 test passes (~50+ tests).

- [ ] **Step 5: Smoke test the CLI end-to-end**

```bash
uv run agent-core bus --help
uv run agent-core bus run --help
```

Expected: help text for the bus subcommand group renders, listing `run`, `status`, `mailbox`, `trace`, `dlq`, `replay`.

- [ ] **Step 6: Final lint and commit**

```bash
uv run ruff check src/agent_core/bus src/agent_core/endpoints src/agent_core/bus_hooks tests/bus
uv run ruff format src/agent_core/bus src/agent_core/endpoints src/agent_core/bus_hooks tests/bus
git add tests/bus/test_integration.py agent_core.yaml
git commit -m "test(bus): add end-to-end integration tests and update example config"
```

---

## Done — Phase 1 Complete

What you have at this point:

- A working in-process bus with durable SQLite mailboxes
- `Endpoint` and `BusHook` protocols with runtime conformance checking
- Bus-stamped `from:` (the v1 security primitive)
- TTL and redelivery sweeps (manual + ready for cadenced wiring)
- Dead-letter mailbox with replay and time-based purge
- A `StubEndpoint` for tests and dev exercises
- A full Typer CLI: `run`, `status`, `mailbox <ep>`, `trace <correlation_id>`, `dlq`, `replay <id>`, `dlq purge --older-than`
- An example `agent_core.yaml` showing the new bus config alongside existing hooks

What's NOT in Phase 1 (next plans):

- **Phase 2:** `ClaudeCodeMCPEndpoint` adapter using FastMCP/Starlette/Uvicorn, MCP tools (`send`, `list_endpoints`, `list_pending`, `handle`, `ack`, `nack`), HTTP host wiring, multi-agent path-based identity.
- **Phase 3+:** `DiscordEndpoint`, `SchedulerEndpoint`, `HTTPEndpoint` (inbound webhook), `TranscriptWriter` bus hook, `events` fanout endpoint.
- **BACKLOG security hooks:** ACL, redaction, rate-limit (each is a separate small plan when triggered).

Phase 2 and 3 are each their own focused plan, written when work begins on them.
