"""SQLite-backed durable storage for the bus.

One table, one writer connection, WAL mode. Envelopes are immutable except
for delivery state columns (state, delivery_count, last_attempted, etc.).
The hot path never deletes; only state transitions.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent_core.bus.envelope import Envelope

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
        async with self._conn.execute("SELECT * FROM envelopes WHERE id = ?", (id_,)) as cur:
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
            (datetime.now(timezone.utc).isoformat(), in_flight_until.isoformat(), id_),
        )
        await self._conn.commit()

    async def mark_acked(self, id_: str) -> None:
        await self._conn.execute("UPDATE envelopes SET state = 'acked' WHERE id = ?", (id_,))
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
        await self._conn.execute("UPDATE envelopes SET state = 'expired' WHERE id = ?", (id_,))
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

    async def purge_dlq(self, *, older_than: datetime) -> int:
        """Delete dead_letter rows older than the cutoff.

        Uses last_attempted as the age signal, falling back to created_at for
        rows that never entered in_flight (e.g., dropped by pre_deliver hooks).
        Returns the number of rows deleted.
        """
        cur = await self._conn.execute(
            """DELETE FROM envelopes
               WHERE state = 'dead_letter'
                 AND COALESCE(last_attempted, created_at) < ?""",
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
