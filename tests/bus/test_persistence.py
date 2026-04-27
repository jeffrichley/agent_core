"""Tests for the bus's SQLite persistence layer."""

import os
import stat
import sys
from pathlib import Path

import pytest

from agent_core.bus.persistence import Persistence


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
