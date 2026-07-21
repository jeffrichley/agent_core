"""Tests for the bus's SQLite persistence layer."""

import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.persistence import Persistence


def _now() -> datetime:
    return datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)


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


class TestBackoffPersistence:
    async def test_requeue_with_backoff_stores_timestamp(self, store: Persistence):
        from datetime import timedelta

        env = _envelope("e1")
        await store.insert(env)
        future = _now() + timedelta(seconds=30)
        await store.requeue_with_backoff(env.id, future)
        row = await store.row(env.id)
        assert row["state"] == "pending"
        assert row["in_flight_until"] is None
        # next_attempt_at must be stored; SQLite returns ISO string or datetime
        assert row["next_attempt_at"] is not None
        assert future.isoformat() in (row["next_attempt_at"] or "")

    async def test_list_pending_filters_future_next_attempt_at(self, store: Persistence):
        from datetime import timedelta

        env = _envelope("e1", to="agent-pepper")
        await store.insert(env)
        future = _now() + timedelta(seconds=30)
        await store.requeue_with_backoff(env.id, future)
        # now=_now() → future backoff not yet due
        results = await store.list_pending("agent-pepper", now=_now())
        assert results == []

    async def test_list_pending_includes_past_next_attempt_at(self, store: Persistence):
        from datetime import timedelta

        env = _envelope("e1", to="agent-pepper")
        await store.insert(env)
        past = _now() - timedelta(seconds=10)
        await store.requeue_with_backoff(env.id, past)
        # now=_now() → past backoff is due
        results = await store.list_pending("agent-pepper", now=_now())
        assert {e.id for e in results} == {"e1"}

    async def test_list_pending_no_now_returns_all(self, store: Persistence):
        from datetime import timedelta

        env = _envelope("e1", to="agent-pepper")
        await store.insert(env)
        future = _now() + timedelta(hours=1)
        await store.requeue_with_backoff(env.id, future)
        # No now kwarg → all pending returned regardless of next_attempt_at
        results = await store.list_pending("agent-pepper")
        assert {e.id for e in results} == {"e1"}

    async def test_requeue_clears_next_attempt_at(self, store: Persistence):
        from datetime import timedelta

        env = _envelope("e1")
        await store.insert(env)
        future = _now() + timedelta(seconds=30)
        # Set a backoff, then plain-requeue (e.g. explicit nack requeue)
        await store.requeue_with_backoff(env.id, future)
        await store.requeue(env.id)
        row = await store.row(env.id)
        assert row["state"] == "pending"
        assert row["next_attempt_at"] is None


async def test_connect_is_atomic_and_closes_connection_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """connect() must close the connection if any post-open step raises.

    Otherwise the aiosqlite worker thread leaks; across daemon restarts and the
    test suite these accumulate until the event loop chokes and a random test
    times out. Regression sibling of the scheduler-engine leak (agent_core #468).
    The autouse leak guard additionally asserts no aiosqlite thread survives.
    """
    import sqlite3

    import agent_core.bus.persistence as persist_mod

    # Force the first post-open step (schema apply) to fail.
    monkeypatch.setattr(persist_mod, "_SCHEMA", "THIS IS NOT VALID SQL;")
    p = Persistence(tmp_path / "bus.sqlite")

    with pytest.raises(sqlite3.OperationalError):
        await p.connect()

    assert p._conn is None  # connection was closed and cleared on failure
