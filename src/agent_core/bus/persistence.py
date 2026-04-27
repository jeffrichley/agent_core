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
