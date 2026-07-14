"""Migration tests for the next_attempt_at column (issue #274).

These tests simulate an existing database created before the column was
added, then verify that Persistence.connect() migrates it idempotently.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from agent_core.bus.persistence import Persistence

# The schema that existed before next_attempt_at was introduced (no next_attempt_at).
_LEGACY_SCHEMA = """
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
"""

_NOW = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)


async def _create_legacy_db(path: Path) -> None:
    """Create a SQLite DB with the old schema (no next_attempt_at) and one row."""
    async with aiosqlite.connect(path) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript(_LEGACY_SCHEMA)
        await conn.execute(
            """INSERT INTO envelopes
               (id, correlation_id, from_endpoint, to_endpoint, kind,
                payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("e1", "c1", "src", "dst", "TextMessage", '{"text":"hi"}', _NOW.isoformat()),
        )
        await conn.commit()


class TestNextAttemptAtMigration:
    async def test_next_attempt_at_migration_adds_column(self, tmp_path: Path):
        """Opening a legacy DB via Persistence.connect() adds next_attempt_at."""
        db = tmp_path / "legacy.sqlite"
        await _create_legacy_db(db)

        # Verify the column is absent in the raw DB before migration.
        async with aiosqlite.connect(db) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("PRAGMA table_info(envelopes)")
            pre_cols = {row["name"] async for row in cur}
        assert "next_attempt_at" not in pre_cols

        # Now open via Persistence — the migration must add the column.
        p = Persistence(db)
        await p.connect()
        try:
            p._conn.row_factory = aiosqlite.Row
            cur = await p._conn.execute("PRAGMA table_info(envelopes)")
            post_cols = {row["name"] async for row in cur}
            assert "next_attempt_at" in post_cols

            # Existing row must still be readable and next_attempt_at defaults to NULL.
            row = await p.row("e1")
            assert row is not None
            assert row["next_attempt_at"] is None
        finally:
            await p.close()

    async def test_next_attempt_at_migration_idempotent(self, tmp_path: Path):
        """Connecting twice to the same DB must not raise (migration is idempotent)."""
        db = tmp_path / "bus.sqlite"
        p1 = Persistence(db)
        await p1.connect()
        await p1.close()

        # Second connect must succeed without raising OperationalError.
        p2 = Persistence(db)
        await p2.connect()
        await p2.close()
