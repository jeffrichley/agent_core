"""Tests that the persistence layer roundtrips urgency and migrates legacy DBs."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

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
        created_at=datetime.now(UTC),
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
            datetime.now(UTC).isoformat(),
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
