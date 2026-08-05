"""The scheduler's SQLite engine must tolerate concurrent access.

Regression guard for the crash found 2026-08-05 on windows CI: APScheduler's
``acquire_jobs`` hit ``sqlite3.OperationalError: database is locked``, the
exception escaped ``_process_jobs``, unwound the scheduler's task group and
logged "Scheduler crashed". Nothing restarts that task, so every scheduled job
stops firing while the endpoint still reports as started.

Each test there had its own ``tmp_path`` database, so this was a single
scheduler locking its own file — exactly the shape the daemon runs in, where
the ``acquire_jobs`` loop competes with bus-driven job mutations.

These tests assert the *effect* on a real connection rather than the presence
of the configuration, and each carries a negative control showing the
assertion can fail.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agent_core.endpoints.scheduler import (
    _SQLITE_BUSY_TIMEOUT_SECONDS,
    create_scheduler_engine,
)


async def _pragma(engine, name: str):
    """Read a PRAGMA back off a real connection from this engine."""
    async with engine.connect() as conn:
        result = await conn.execute(text(f"PRAGMA {name}"))
        return result.scalar()


@pytest.mark.asyncio
async def test_scheduler_engine_uses_wal_journal(tmp_path):
    """WAL lets the acquire_jobs reader run while a mutation writes."""
    engine = create_scheduler_engine(tmp_path / "sched.db")
    try:
        assert (await _pragma(engine, "journal_mode")).lower() == "wal"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_plain_engine_does_not_use_wal(tmp_path):
    """Negative control: the default engine is what crashed the scheduler.

    Without this, `test_scheduler_engine_uses_wal_journal` would still pass if
    WAL were SQLite's default and `create_scheduler_engine` did nothing.
    A fresh path is required — journal_mode is persistent in the database file,
    so reusing the other test's file would report wal no matter what.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'plain.db'}")
    try:
        assert (await _pragma(engine, "journal_mode")).lower() != "wal"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scheduler_engine_sets_busy_timeout(tmp_path):
    """A blocked connection must wait for the lock, not fail instantly.

    WAL alone does not cover write-write contention; the busy timeout does.
    """
    engine = create_scheduler_engine(tmp_path / "sched.db")
    try:
        # PRAGMA busy_timeout reports milliseconds.
        assert await _pragma(engine, "busy_timeout") == int(_SQLITE_BUSY_TIMEOUT_SECONDS * 1000)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_plain_engine_has_no_busy_timeout(tmp_path):
    """Negative control for the busy timeout."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'plain.db'}")
    try:
        assert await _pragma(engine, "busy_timeout") != int(_SQLITE_BUSY_TIMEOUT_SECONDS * 1000)
    finally:
        await engine.dispose()


# Deliberately NOT tested here: an end-to-end "reader survives a concurrent
# writer" case. The obvious version — hold an uncommitted INSERT on one
# connection and read from another — passes with and without WAL, because
# SQLite's rollback journal only takes a RESERVED lock for an open write
# transaction and still admits readers; the exclusive lock exists only during
# the commit itself. Verified by neutering create_scheduler_engine and watching
# that test pass anyway, while the two pragma tests above failed.
#
# Reproducing the real crash needs two processes racing a commit, which is
# timing-dependent — precisely the property that made the original failure a CI
# flake. Trading a deterministic pragma assertion for a racy end-to-end one
# would reintroduce the class of problem this change exists to remove.
