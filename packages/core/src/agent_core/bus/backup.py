"""Bus backup — scheduled VACUUM INTO snapshots with generational retention.

Provides:
  snapshot_store   — create a VACUUM INTO snapshot of one SQLite store
  list_snapshots   — enumerate existing snapshots, newest-first
  apply_retention  — prune to 24-hourly + 14-daily generational policy
  run_backup       — orchestrate snapshot + retention for a list of stores,
                     absorbing all errors so callers never crash
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

# Snapshot filename timestamp format: <store_stem>-YYYYMMDD-HHMMSS.sqlite
_TS_FORMAT = "%Y%m%d-%H%M%S"
_HOURLY_KEEP = 24
_DAILY_KEEP = 14


async def snapshot_store(
    store_path: Path,
    backup_dir: Path,
    now: datetime | None = None,
) -> Path:
    """Create a VACUUM INTO snapshot of *store_path* into *backup_dir*.

    The destination filename is ``<store_stem>-YYYYMMDD-HHMMSS.sqlite`` using
    the UTC timestamp in *now* (defaults to ``datetime.now(UTC)``).

    Creates *backup_dir* (and any missing parents) if it does not exist.
    Returns the path of the newly created snapshot file.
    """
    if now is None:
        now = datetime.now(UTC)
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = now.strftime(_TS_FORMAT)
    dest = backup_dir / f"{store_path.stem}-{ts}.sqlite"
    async with aiosqlite.connect(store_path) as db:
        await db.execute(f"VACUUM INTO '{dest}'")
    return dest


def list_snapshots(backup_dir: Path, store_name: str) -> list[Path]:
    """Return all ``<store_name>-*.sqlite`` files in *backup_dir*, sorted newest-first.

    Sorting is lexicographic on filename, which is equivalent to chronological
    order because the timestamp portion is ISO-like (YYYYMMDD-HHMMSS).
    Returns an empty list when *backup_dir* does not exist.
    """
    if not backup_dir.exists():
        return []
    files = list(backup_dir.glob(f"{store_name}-*.sqlite"))
    files.sort(key=lambda p: p.name, reverse=True)
    return files


def apply_retention(backup_dir: Path, store_name: str) -> list[Path]:
    """Delete snapshots not covered by the 24-hourly + 14-daily retention policy.

    **Policy (from the spec):**
    Keep the newest snapshot per *clock-hour* for the 24 most recent distinct
    clock-hours, and the newest snapshot per *calendar-day* for the 14 most
    recent distinct calendar-days.  The union of those two sets survives;
    every other matching snapshot is deleted.

    Files whose names don't match the ``<store_name>-YYYYMMDD-HHMMSS.sqlite``
    pattern are left untouched.

    Returns the list of deleted ``Path`` objects.
    """
    snapshots = list_snapshots(backup_dir, store_name)
    stem_pattern = f"{store_name}-{_TS_FORMAT}.sqlite"

    # Parse timestamps; skip non-matching files (they come back from list_snapshots
    # if they happen to match the glob but not the exact strptime format).
    parsed: list[tuple[datetime, Path]] = []
    for path in snapshots:
        try:
            dt = datetime.strptime(path.name, stem_pattern)
        except ValueError:
            continue  # non-matching — leave untouched
        parsed.append((dt, path))

    # parsed is newest-first (from list_snapshots sort).
    keep: set[Path] = set()

    # --- 24 most-recent distinct clock-hours (one file each, the newest) ---
    seen_hourly: set[tuple[int, int, int, int]] = set()
    for dt, path in parsed:
        key = (dt.year, dt.month, dt.day, dt.hour)
        if key not in seen_hourly and len(seen_hourly) < _HOURLY_KEEP:
            seen_hourly.add(key)
            keep.add(path)

    # --- 14 most-recent distinct calendar-days (one file each, the newest) ---
    seen_daily: set[tuple[int, int, int]] = set()
    daily_kept = 0
    for dt, path in parsed:
        day_key = (dt.year, dt.month, dt.day)
        if day_key not in seen_daily and daily_kept < _DAILY_KEEP:
            seen_daily.add(day_key)
            keep.add(path)
            daily_kept += 1

    # Delete everything in the parsed set that is not in keep.
    deleted: list[Path] = []
    for _, path in parsed:
        if path not in keep:
            path.unlink()
            deleted.append(path)

    return deleted


async def run_backup(
    store_paths: list[Path],
    backup_dir: Path,
    now: datetime | None = None,
) -> None:
    """Snapshot each store and apply retention.  Logs loudly on any error but
    does not raise, so the caller's event loop can never be crashed by a backup
    failure.
    """
    for store_path in store_paths:
        if not store_path.exists():
            log.warning("backup: store %s does not exist, skipping", store_path)
            continue
        try:
            await snapshot_store(store_path, backup_dir, now=now)
            apply_retention(backup_dir, store_path.stem)
        except Exception:
            log.error("backup: snapshot failed for store %s", store_path, exc_info=True)
