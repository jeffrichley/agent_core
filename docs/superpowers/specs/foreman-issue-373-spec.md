# Spec: backup/restore — scheduled VACUUM INTO snapshots + retention + restore CLI (issue #373)

## Goal

Implement the Eβ-1 backup/restore sub-system described in `docs/superpowers/specs/2026-07-15-data-durability-design.md`: take hourly `VACUUM INTO` snapshots of both `bus.sqlite` and `scheduler.db` into an off-volume directory, enforce generational retention (24 hourly + 14 daily), and provide a `restore` CLI command that validates and swaps a snapshot into place. Closes the total-loss risk from a single-volume failure (issue #373).

## Acceptance criteria

- `packages/core/src/agent_core/bus/backup.py` exists and exports:
  - `async def snapshot_store(store_path: Path, backup_dir: Path, now: datetime | None = None) -> Path` — opens `store_path` via `aiosqlite`, executes `VACUUM INTO '<dest>'`, returns the destination path. `dest` filename is `<store_stem>-YYYYMMDD-HHMMSS.sqlite` using the UTC timestamp in `now` (defaults to `datetime.now(UTC)`). Creates `backup_dir` if it does not exist.
  - `def list_snapshots(backup_dir: Path, store_name: str) -> list[Path]` — returns all `<store_name>-*.sqlite` files in `backup_dir`, sorted newest-first.
  - `def apply_retention(backup_dir: Path, store_name: str) -> list[Path]` — deletes snapshots not covered by the 24-hourly + 14-daily policy (see Approach); returns list of deleted paths.
  - `async def run_backup(store_paths: list[Path], backup_dir: Path, now: datetime | None = None) -> None` — for each `store_path`, calls `snapshot_store` then `apply_retention`; logs loudly on any error but does not raise.
- `BusConfig` in `packages/core/src/agent_core/bus/core.py` has two new fields:
  - `backup_dir: Path | None = None` — `None` means backup is disabled; non-`None` is the target directory (must be on a different volume from the stores in production).
  - `backup_interval_seconds: int = 3600` — cadence of the backup loop.
- `build_bus_from_config` in `packages/core/src/agent_core/bus/runner.py` reads `bus.backup_dir` (optional, path-expanded) and `bus.backup_interval_seconds` (default 3600) from the YAML and passes them to `BusConfig`.
- `_run_bus` in `packages/core/src/agent_core/bus/cli.py` spawns a `_backup_loop` asyncio task (alongside the existing `_ttl_loop` and `_redelivery_loop`) when `bus.config.backup_dir` is not `None`. The loop calls `run_backup([bus.config.storage_path, <scheduler_db>], bus.config.backup_dir)` every `bus.config.backup_interval_seconds` seconds, where `<scheduler_db>` is `bus.config.storage_path.parent / "scheduler.db"` (included only if the file exists). Exceptions from `run_backup` are caught + logged (matching the existing `"TTL sweep failed"` pattern); the loop continues.
- `agent-core bus restore <snapshot> <target>` CLI command exists in `packages/core/src/agent_core/bus/cli.py`:
  - Validates `snapshot` exists; exits with code 1 if not.
  - Opens `snapshot` with `aiosqlite` and runs `PRAGMA integrity_check`; exits with code 1 if the result is not `"ok"` or if the connection fails.
  - Copies `snapshot` over `target` using `shutil.copy2`.
  - Accepts `--yes` / `-y` flag to skip the interactive confirmation prompt; without it, `typer.confirm` asks the operator to confirm the overwrite.
  - Prints success/failure via `rich.console.Console`.
- `packages/core/tests/bus/test_backup.py` exists and covers:
  - `snapshot_store` produces a file that passes `PRAGMA integrity_check` and contains the committed rows from the source (insert a row before snapshot; verify it appears in the snapshot via a direct `aiosqlite` query).
  - `apply_retention` prunes such that exactly the newest snapshot per clock-hour (for the 24 most recent distinct hours) and the newest snapshot per calendar-day (for the 14 most recent distinct days) are kept; snapshots outside both windows are deleted.
  - `run_backup` on a non-existent `store_path` logs a warning and does not raise; on a VACUUM INTO error it logs an error and continues to the next store.
  - `restore` CLI: `CliRunner`-invoked restore of a valid snapshot over a target succeeds; restore of a corrupt snapshot (truncated file) exits with code 1 before overwriting the target.
- `just check` passes (ruff + full suite, coverage ≥ 85%).

## Approach

No GoF pattern fits cleanly. This is straightforward SRP decomposition: one new module (`backup.py`) owns the snapshot, retention, and error-buffering concerns; `BusConfig` gains two new fields; the CLI loop wires them together; the `restore` command is a thin async wrapper around aiosqlite + `shutil`.

**VACUUM INTO as the snapshot primitive.** SQLite's `VACUUM INTO '<path>'` produces a consistent, compacted copy of the live database — including all committed WAL frames — in a single atomic operation. It is the correct primitive for a live WAL-mode database: a naive `shutil.copy2` on a WAL DB can produce a torn copy if the WAL is being flushed concurrently. `aiosqlite` runs the `VACUUM INTO` call in a thread-pool executor so it does not block the asyncio event loop. No new dependency is needed: `aiosqlite` is already in `packages/core/pyproject.toml`.

**Snapshot filename and retention.** Files are named `<store_stem>-YYYYMMDD-HHMMSS.sqlite` (UTC, e.g. `bus-20260717-140000.sqlite`). `apply_retention` parses timestamps from filenames with `datetime.strptime`, groups them by `(year, month, day, hour)` (for hourly) and `(year, month, day)` (for daily), takes the newest snapshot per group (groups are already newest-first via `list_snapshots` sort), then slices to 24 hourly groups and 14 daily groups. The union of those two sets is kept; every other snapshot is `path.unlink()`-ed. Files whose names don't match the pattern are left untouched.

**Scheduler DB discovery.** The default `scheduler.db` lives at `~/.agent-core/scheduler.db` — adjacent to `bus.sqlite` (see `SchedulerEndpoint._default_db_path()` in `packages/core/src/agent_core/endpoints/scheduler.py:183`). The backup loop checks `bus.config.storage_path.parent / "scheduler.db"` and includes it only when it exists. This is YAGNI — a future config key can add arbitrary extra paths if the scheduler DB ever moves.

**Config integration.** `backup_dir` is read from `bus.backup_dir` in the YAML (same block as `storage_path`). `None` (key absent) disables backup entirely so deployments without an off-volume drive don't spin up a no-op task. The YAML key uses a string path that `runner.py` expands with `.expanduser()`.

**`restore` CLI safety.** Integrity check before swap ensures the operator isn't restoring a corrupt snapshot. `shutil.copy2` on a VACUUM-created snapshot is safe: the snapshot is a clean, WAL-free SQLite file (no `-wal` or `-shm` sidecar). The `--yes` flag bypasses confirmation for scripted use. The command explicitly requires the daemon to be stopped — documented in the command help string, not enforced in code (since detecting "is the daemon running?" is fragile and cross-platform).

**Backup dir on a different volume.** The spec does not hardcode `E:\agent-core-backups`. The YAML key is the operator's responsibility — the spec documents the intent (off-volume) in the YAML comment added to the default `agent_core.yaml`.

## Sub-requests (topologically sorted)

1. **Create `packages/core/src/agent_core/bus/backup.py`** — `snapshot_store`, `list_snapshots`, `apply_retention`, `run_backup` (see Acceptance criteria for exact signatures and behaviour).

2. **Extend `BusConfig` in `packages/core/src/agent_core/bus/core.py`** — add `backup_dir: Path | None = None` and `backup_interval_seconds: int = 3600` fields (after `watchdog_timeout_seconds`).

3. **Update `build_bus_from_config` in `packages/core/src/agent_core/bus/runner.py`** — read `bus.backup_dir` (call `.expanduser()` when non-None) and `bus.backup_interval_seconds` from the YAML and forward to `BusConfig`.

4. **Add `_backup_loop` to `_run_bus` in `packages/core/src/agent_core/bus/cli.py`** — parallel asyncio task (alongside `_ttl_loop`/`_redelivery_loop`); active only when `bus.config.backup_dir is not None`; calls `run_backup` every `backup_interval_seconds` seconds; exceptions logged + swallowed.

5. **Add `restore` command to `packages/core/src/agent_core/bus/cli.py`** — `agent-core bus restore <snapshot> <target> [--yes]`; integrity check → confirm → `shutil.copy2`.

6. **Create `packages/core/tests/bus/test_backup.py`** — four test groups covering snapshot integrity, retention counts, error isolation in `run_backup`, and the restore CLI (see Acceptance criteria).

7. **Update `agent_core.yaml`** — add a commented-out `backup_dir` key in the `bus:` block with an explanatory comment pointing operators to use an off-volume path.

## File-level changes

| File | Change |
|------|--------|
| `packages/core/src/agent_core/bus/backup.py` | **New** — `snapshot_store`, `list_snapshots`, `apply_retention`, `run_backup` |
| `packages/core/src/agent_core/bus/core.py` | **Modify** — add `backup_dir: Path \| None = None` and `backup_interval_seconds: int = 3600` to `BusConfig` |
| `packages/core/src/agent_core/bus/runner.py` | **Modify** — read `bus.backup_dir` and `bus.backup_interval_seconds` from YAML; pass to `BusConfig` |
| `packages/core/src/agent_core/bus/cli.py` | **Modify** — add `_backup_loop` task in `_run_bus`; add `restore` CLI command with aiosqlite integrity check + `shutil.copy2` |
| `packages/core/tests/bus/test_backup.py` | **New** — unit + CLI tests for snapshot, retention, error isolation, restore |
| `agent_core.yaml` | **Modify** — add commented `backup_dir:` example in the `bus:` block |

## Alternatives considered

1. **Use SQLite's Online Backup API via `sqlite3.Connection.backup()` instead of `VACUUM INTO`.** The stdlib `sqlite3` module exposes `conn.backup(target_conn)` which pages through the DB without blocking. Ruled out: `aiosqlite` is already the dependency; `VACUUM INTO` is simpler (one SQL string, no target connection management), produces a compacted file (no fragmentation), and handles WAL mode correctly. `backup()` would require managing two simultaneous aiosqlite connections and yields an unvacuumed copy. `VACUUM INTO` is SQLite's documented recommended path for offline-consistent snapshots.

2. **Use Litestream for continuous WAL replication.** Noted in the design doc as the upgrade path for multi-machine. Ruled out as YAGNI: this is a single-box deployment; Litestream adds a sidecar process, Go runtime, and S3/cloud storage config that far exceeds the scope of a scheduled hourly backup.

3. **Keep 24 most-recent snapshots (flat count) instead of hourly+daily.** Simpler logic, no date-parsing. Ruled out: a flat count of 24 provides only 24 hours of recovery coverage with no long tail. Generational retention (hourly + daily) provides the same recent granularity plus 14-day coverage for slow-burn corruption scenarios. The design doc approves this scheme explicitly.

4. **Store backup config outside `BusConfig` in a new `BackupConfig` dataclass.** Cleaner separation. Ruled out: only two fields; a separate dataclass would require a new nested key in the YAML (`bus.backup.backup_dir` etc.) breaking the existing flat `bus:` convention. Two additional fields on `BusConfig` is proportionate to the scope.

## Open questions

*None.* The design authority (`docs/superpowers/specs/2026-07-15-data-durability-design.md`) is unambiguous on scope, cadence, retention policy, and backup primitive. All file paths and function names are verified against the actual codebase.

## Out of scope

- Eβ-2 (terminal envelope retention + WAL checkpoint + periodic VACUUM) — separate ticket, no dependency on Eβ-1.
- Eβ-3 (corruption guard at boot → quarantine + restore from snapshot) — depends on Eβ-1 (snapshot path must exist first) but is P2 and held.
- Eβ-4 (migration versioning with `user_version`) — separate ticket.
- Eβ-5 (scheduler WAL mode) — separate ticket; setting `journal_mode=WAL` on `scheduler.db` is not part of this backup ticket.
- Eβ-6 (JSONL trail age rotation) — separate ticket.
- Backup of any store other than `bus.sqlite` and the co-located `scheduler.db` — if a future deployment puts `scheduler.db` on a non-adjacent path, that will need a config key (YAGNI now).
- Verifying that the backup volume is actually a different physical volume than the store — this is an operator deployment concern documented in comments, not enforced in code.
- `integrity_check` at daemon boot (that's Eβ-3's scope) — `restore` runs `integrity_check` only on the snapshot being restored.
