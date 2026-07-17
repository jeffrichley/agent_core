"""Tests for agent_core.bus.backup — snapshot, retention, and restore CLI."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import pytest
from typer.testing import CliRunner

from agent_core.bus import backup
from agent_core.cli import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_db(path: Path) -> None:
    """Create a minimal SQLite database with a test row."""
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE t (v TEXT)")
        await db.execute("INSERT INTO t VALUES ('hello')")
        await db.commit()


def _make_snapshot_files(
    backup_dir: Path, store_name: str, timestamps: list[datetime]
) -> list[Path]:
    """Create placeholder files with the snapshot naming convention (not real DBs)."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for dt in timestamps:
        ts = dt.strftime("%Y%m%d-%H%M%S")
        p = backup_dir / f"{store_name}-{ts}.sqlite"
        p.write_bytes(b"placeholder")
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# 1. snapshot_store — produces integrity-clean file containing committed rows
# ---------------------------------------------------------------------------


class TestSnapshotStore:
    async def test_produces_file_passing_integrity_check(self, tmp_path: Path) -> None:
        store = tmp_path / "bus.sqlite"
        await _make_db(store)
        backup_dir = tmp_path / "backups"

        dest = await backup.snapshot_store(store, backup_dir)

        assert dest.exists()
        async with aiosqlite.connect(dest) as db:
            async with db.execute("PRAGMA integrity_check") as cur:
                row = await cur.fetchone()
        assert row is not None and row[0] == "ok"

    async def test_snapshot_contains_committed_rows(self, tmp_path: Path) -> None:
        store = tmp_path / "bus.sqlite"
        await _make_db(store)
        backup_dir = tmp_path / "backups"

        dest = await backup.snapshot_store(store, backup_dir)

        async with aiosqlite.connect(dest) as db:
            async with db.execute("SELECT v FROM t") as cur:
                rows = await cur.fetchall()
        assert rows == [("hello",)]

    async def test_creates_backup_dir(self, tmp_path: Path) -> None:
        store = tmp_path / "bus.sqlite"
        await _make_db(store)
        backup_dir = tmp_path / "new" / "nested" / "backups"

        dest = await backup.snapshot_store(store, backup_dir)

        assert backup_dir.is_dir()
        assert dest.parent == backup_dir

    async def test_filename_uses_utc_timestamp(self, tmp_path: Path) -> None:
        store = tmp_path / "bus.sqlite"
        await _make_db(store)
        backup_dir = tmp_path / "backups"
        now = datetime(2026, 7, 17, 14, 0, 0, tzinfo=UTC)

        dest = await backup.snapshot_store(store, backup_dir, now=now)

        assert dest.name == "bus-20260717-140000.sqlite"

    async def test_returns_destination_path(self, tmp_path: Path) -> None:
        store = tmp_path / "bus.sqlite"
        await _make_db(store)
        backup_dir = tmp_path / "backups"
        now = datetime(2026, 1, 15, 8, 30, 0, tzinfo=UTC)

        dest = await backup.snapshot_store(store, backup_dir, now=now)

        expected = backup_dir / "bus-20260115-083000.sqlite"
        assert dest == expected
        assert dest.exists()


# ---------------------------------------------------------------------------
# 2. list_snapshots
# ---------------------------------------------------------------------------


class TestListSnapshots:
    def test_returns_matching_files_newest_first(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "backups"
        timestamps = [
            datetime(2026, 7, 17, 10, 0, 0),
            datetime(2026, 7, 17, 12, 0, 0),
            datetime(2026, 7, 17, 11, 0, 0),
        ]
        _make_snapshot_files(backup_dir, "bus", timestamps)

        result = backup.list_snapshots(backup_dir, "bus")

        names = [p.name for p in result]
        assert names == [
            "bus-20260717-120000.sqlite",
            "bus-20260717-110000.sqlite",
            "bus-20260717-100000.sqlite",
        ]

    def test_returns_empty_when_dir_missing(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "nonexistent"

        result = backup.list_snapshots(backup_dir, "bus")

        assert result == []

    def test_excludes_other_store_names(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "backups"
        _make_snapshot_files(backup_dir, "bus", [datetime(2026, 7, 17, 10, 0, 0)])
        _make_snapshot_files(backup_dir, "scheduler", [datetime(2026, 7, 17, 10, 0, 0)])

        result = backup.list_snapshots(backup_dir, "bus")

        assert all(p.name.startswith("bus-") for p in result)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 3. apply_retention — prunes to 24 hourly + 14 daily
# ---------------------------------------------------------------------------


class TestApplyRetention:
    def test_prunes_file_outside_both_windows(self, tmp_path: Path) -> None:
        """25th oldest file is not in top-24 hourly groups and is not the newest
        for its calendar day (the newest for that day is already in hourly keeps).
        It must be deleted."""
        backup_dir = tmp_path / "backups"
        store_name = "bus"
        # base = 2026-07-17 12:00:00 UTC
        base = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)

        # 24 files: one per hour, hours 0-23 (all distinct hours, kept by hourly)
        hourly_timestamps = [base - timedelta(hours=h) for h in range(24)]
        # h=13 → 2026-07-16 23:00 is the newest file for 2026-07-16
        hourly_paths = _make_snapshot_files(backup_dir, store_name, hourly_timestamps)

        # 25th file: base-24h = 2026-07-16 12:00
        # Day 2026-07-16 is already represented by h=13 (2026-07-16 23:00) in hourly keeps.
        # So this file is NOT the newest for its day → deleted by both policies.
        extra_paths = _make_snapshot_files(
            backup_dir, store_name, [base - timedelta(hours=24)]
        )

        deleted = backup.apply_retention(backup_dir, store_name)

        for p in hourly_paths:
            assert p.exists(), f"{p.name} should be kept"
        assert not extra_paths[0].exists(), f"{extra_paths[0].name} should be deleted"
        assert extra_paths[0] in deleted

    def test_keeps_only_newest_in_same_clock_hour(self, tmp_path: Path) -> None:
        """When two snapshots share the same clock-hour, only the newer is kept."""
        backup_dir = tmp_path / "backups"
        store_name = "bus"
        # Both in hour 12 of 2026-07-17
        newer = datetime(2026, 7, 17, 12, 30, 0)
        older = datetime(2026, 7, 17, 12, 0, 0)
        paths = _make_snapshot_files(backup_dir, store_name, [newer, older])

        backup.apply_retention(backup_dir, store_name)

        assert paths[0].exists()  # 12:30 kept
        assert not paths[1].exists()  # 12:00 deleted (same hour, older)

    def test_daily_retention_keeps_newest_for_old_day(self, tmp_path: Path) -> None:
        """A file beyond the 24-hour hourly window but within 14 days and the
        newest for its calendar day must be kept by the daily policy."""
        backup_dir = tmp_path / "backups"
        store_name = "bus"
        base = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)

        # 24 hourly files covering hours 0-23 (days 16 and 17)
        _make_snapshot_files(
            backup_dir, store_name, [base - timedelta(hours=h) for h in range(24)]
        )
        # One file for a distinct day 5 days ago — the only (and newest) file for that day.
        old_day_file = _make_snapshot_files(
            backup_dir, store_name, [base - timedelta(days=5)]
        )[0]

        backup.apply_retention(backup_dir, store_name)

        # old_day_file should survive because it's the newest for its unique day (within 14 days)
        assert old_day_file.exists(), f"{old_day_file.name} should be kept by daily policy"

    def test_non_matching_files_are_untouched(self, tmp_path: Path) -> None:
        """Files that don't match the snapshot naming pattern are left alone."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        unrelated = backup_dir / "some-other-file.sqlite"
        unrelated.write_bytes(b"not mine")
        readme = backup_dir / "README.md"
        readme.write_bytes(b"notes")

        backup.apply_retention(backup_dir, "bus")

        assert unrelated.exists()
        assert readme.exists()

    def test_returns_list_of_deleted_paths(self, tmp_path: Path) -> None:
        """apply_retention returns exactly the paths it deleted."""
        backup_dir = tmp_path / "backups"
        store_name = "bus"
        base = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)

        hourly_paths = _make_snapshot_files(
            backup_dir, store_name, [base - timedelta(hours=h) for h in range(24)]
        )
        extra = _make_snapshot_files(backup_dir, store_name, [base - timedelta(hours=24)])[0]

        deleted = backup.apply_retention(backup_dir, store_name)

        assert extra in deleted
        for p in hourly_paths:
            assert p not in deleted


# ---------------------------------------------------------------------------
# 4. run_backup — error isolation
# ---------------------------------------------------------------------------


class TestRunBackup:
    async def test_nonexistent_store_logs_warning_does_not_raise(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = tmp_path / "nonexistent.sqlite"
        backup_dir = tmp_path / "backups"

        import logging

        with caplog.at_level(logging.WARNING, logger="agent_core.bus.backup"):
            await backup.run_backup([store], backup_dir)  # must not raise

        assert any(
            "nonexistent" in r.message.lower() or "does not exist" in r.message.lower()
            for r in caplog.records
        )

    async def test_snapshot_error_logs_and_continues_to_next_store(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Simulated VACUUM INTO error on store1 must not prevent store2 from being backed up."""
        store1 = tmp_path / "store1.sqlite"
        store2 = tmp_path / "store2.sqlite"
        # Create both stores so run_backup doesn't skip them for non-existence
        async with aiosqlite.connect(store1) as db:
            await db.execute("CREATE TABLE x (v TEXT)")
            await db.commit()
        async with aiosqlite.connect(store2) as db:
            await db.execute("CREATE TABLE x (v TEXT)")
            await db.commit()
        backup_dir = tmp_path / "backups"

        original_snapshot = backup.snapshot_store

        async def fail_first(store_path: Path, bd: Path, now: datetime | None = None) -> Path:
            if store_path == store1:
                raise RuntimeError("simulated VACUUM INTO failure")
            return await original_snapshot(store_path, bd, now=now)

        import logging

        with patch("agent_core.bus.backup.snapshot_store", fail_first):
            with caplog.at_level(logging.ERROR, logger="agent_core.bus.backup"):
                await backup.run_backup([store1, store2], backup_dir)  # must not raise

        # store2 should have been backed up despite store1 failing
        snaps = backup.list_snapshots(backup_dir, "store2")
        assert len(snaps) == 1

        # An error should have been logged for store1
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, "Expected at least one ERROR log entry"


# ---------------------------------------------------------------------------
# 5. restore CLI
# ---------------------------------------------------------------------------


class TestRestoreCLI:
    def _make_valid_snapshot(self, path: Path) -> Path:
        """Create a valid SQLite snapshot file (synchronous, for sync test body)."""
        import sqlite3

        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('data')")
        conn.commit()
        conn.close()
        return path

    def test_restore_valid_snapshot_succeeds(self, tmp_path: Path) -> None:
        snapshot = self._make_valid_snapshot(tmp_path / "snap.sqlite")
        target = tmp_path / "target.sqlite"
        target.write_bytes(b"original")

        runner = CliRunner()
        result = runner.invoke(app, ["bus", "restore", str(snapshot), str(target), "--yes"])

        assert result.exit_code == 0, result.output
        # Target should now be the snapshot
        assert target.read_bytes() == snapshot.read_bytes()

    def test_restore_corrupt_snapshot_exits_1_before_overwrite(self, tmp_path: Path) -> None:
        snapshot = tmp_path / "corrupt.sqlite"
        snapshot.write_bytes(b"not a database file at all!")
        original_content = b"original database"
        target = tmp_path / "target.sqlite"
        target.write_bytes(original_content)

        runner = CliRunner()
        result = runner.invoke(app, ["bus", "restore", str(snapshot), str(target), "--yes"])

        assert result.exit_code == 1
        # Target must NOT have been overwritten
        assert target.read_bytes() == original_content

    def test_restore_nonexistent_snapshot_exits_1(self, tmp_path: Path) -> None:
        snapshot = tmp_path / "nosuchfile.sqlite"
        target = tmp_path / "target.sqlite"
        target.write_bytes(b"untouched")

        runner = CliRunner()
        result = runner.invoke(app, ["bus", "restore", str(snapshot), str(target), "--yes"])

        assert result.exit_code == 1
        assert target.read_bytes() == b"untouched"

    def test_restore_prints_success_message(self, tmp_path: Path) -> None:
        snapshot = self._make_valid_snapshot(tmp_path / "snap.sqlite")
        target = tmp_path / "target.sqlite"
        target.write_bytes(b"original")

        runner = CliRunner()
        result = runner.invoke(app, ["bus", "restore", str(snapshot), str(target), "--yes"])

        assert result.exit_code == 0
        assert "restored" in result.output.lower()

    def test_restore_integrity_check_non_ok_exits_1_before_overwrite(
        self, tmp_path: Path
    ) -> None:
        """Patch aiosqlite so integrity_check returns a non-ok string (without raising).
        The restore command must exit 1 and leave the target untouched."""
        from unittest.mock import AsyncMock

        snapshot = tmp_path / "snap.sqlite"
        snapshot.write_bytes(b"placeholder")  # file must exist for the existence check
        original_content = b"original database"
        target = tmp_path / "target.sqlite"
        target.write_bytes(original_content)

        # Build a mock aiosqlite context that returns ("corruption found",) from fetchone.
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=("corruption found",))
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock(return_value=None)

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        with patch("agent_core.bus.cli.aiosqlite.connect", return_value=mock_conn):
            runner = CliRunner()
            result = runner.invoke(app, ["bus", "restore", str(snapshot), str(target), "--yes"])

        assert result.exit_code == 1
        assert target.read_bytes() == original_content  # not overwritten

    def test_restore_prompts_confirm_without_yes_flag(self, tmp_path: Path) -> None:
        """Without --yes the user is prompted; answering 'y' completes the restore."""
        snapshot = self._make_valid_snapshot(tmp_path / "snap.sqlite")
        target = tmp_path / "target.sqlite"
        target.write_bytes(b"original")

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["bus", "restore", str(snapshot), str(target)],
            input="y\n",
        )

        assert result.exit_code == 0, result.output
        assert target.read_bytes() == snapshot.read_bytes()
