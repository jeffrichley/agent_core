# Data durability — Design (Theme E, Cluster β)

**Theme:** agent_core#268 (Theme E — Observability & data durability) · epic #262
**Date:** 2026-07-15
**Status:** approved design, pre-implementation
**Priority:** P0–P2 cluster (eval 2026-07-13, Theme E, dimension 9). Backup/restore is `[P0]` and auto-planned; the rest are held.
**Cluster:** Eβ of Theme E. Sibling: **Eα — Observability** (health/`/healthz`/metrics, structured logging, log rotation) — a later brainstorm. Theme E was split into Eα (seeing the system) and Eβ (surviving data loss) because they are independent subsystems.

## Problem

The two SQLite stores hold everything and have **no durability layer**:

- **`bus.sqlite`** (`bus/persistence.py`): single-writer, `journal_mode=WAL`, `foreign_keys=ON`; holds all in-flight mail + supervisor state. The **hot path never deletes** — only DLQ rows are manually purgeable — so acked/expired envelopes accumulate **forever**, and WAL is enabled but **never checkpointed** (unbounded WAL + main-DB growth). Schema migration is a hand-rolled `PRAGMA table_info(envelopes)` + conditional `ALTER TABLE ADD COLUMN` with **no `user_version`**.
- **`scheduler.db`** (`endpoints/scheduler.py`): SQLAlchemy `create_async_engine` on the **default rollback journal** (weaker crash-safety than the bus), and it drives every heartbeat/liveness fire.

Concrete gaps (eval Theme E, durability half):

- **`[P0]` No backup/restore for either store.** No `.backup`/`VACUUM INTO` anywhere; both are un-backed-up single files. Disk failure/corruption = total loss, no recovery. This is not hypothetical — the 2026-07-15 C:-full incident showed how close a single-volume failure is.
- **`[P1]` No WAL checkpointing/VACUUM** — WAL never truncated; acked/expired rows never deleted → unbounded growth.
- **`[P1]` No retention/compaction** for terminal (acked/expired) envelopes.
- **`[P2]` No corruption detection/recovery** — no `PRAGMA integrity_check`; a malformed DB crashes daemon boot.
- **`[P2]` Ad-hoc migration, no version tracking.**
- **`[P2]` Scheduler store not on WAL.**
- **`[P2]` Audit/raw JSONL trails have no retention** — daily files accumulate forever.

## Design decisions (from the brainstorm, approved)

1. **Backup: scheduled online-backup snapshots, on a different volume.** `VACUUM INTO` (SQLite's online-backup path — a *consistent* copy of a live WAL database, unlike a naive file copy) both stores on a schedule to a backup directory on a **separate volume (E:)** — a backup on the same drive as the live DB is worthless when that drive fails. Rejected continuous WAL replication (Litestream-style) as YAGNI for a single-box, two-being deployment; noted as the upgrade path if we ever go multi-machine. Rejected naive `cp` (torn WAL state).
   - **Cadence + retention (approved):** **hourly** snapshots; keep **24 hourly + 14 daily** generations. Restore = copy a chosen snapshot back over a stopped daemon.

2. **Retention/compaction + WAL maintenance bound growth.** In the existing TTL sweep, delete terminal (acked/expired) envelopes older than **7 days** (approved) — recent history stays queryable for trace/audit; the DLQ stays separate and manually purged. After each retention pass, `PRAGMA wal_checkpoint(TRUNCATE)`; run a periodic `VACUUM` (daily) to reclaim pages.

3. **Corruption detection + recovery.** `PRAGMA integrity_check` at boot; on failure, quarantine the corrupt file (rename aside), restore from the latest good snapshot, and surface loudly — never silently start on a corrupt store or crash-loop on it.

4. **Migration versioning.** Replace the ad-hoc `table_info`+`ALTER` with `PRAGMA user_version` + an ordered list of migration steps applied transactionally; each bumps `user_version`. The existing two columns become migration steps 1–2.

5. **Scheduler store on WAL.** Set `journal_mode=WAL` on the `scheduler.db` engine (matches the bus) and include `scheduler.db` in the backup/retention scope.

6. **JSONL trail retention.** Rotate/retain the audit + raw JSONL trails by age — keep **30 days** (approved), drop older.

## Architecture

### 1. Backup engine (`bus/`, new `durability/backup.py` or similar)

- A backup routine: for each store path, run `VACUUM INTO '<backup_dir>/<store>-<timestamp>.sqlite'` (WAL-safe consistent snapshot), then enforce generational retention (keep last 24 hourly + 14 daily, prune the rest). Backup dir is **config-driven**, defaulting to a path on a different volume than the store; on this host, `E:\...\agent-core-backups`.
- Scheduled hourly — driven by the existing sweep loop (`bus/cli.py` sweeps) or a dedicated backup task with the same lifecycle. Failures log loudly (a silently-failing backup is worse than none) but do not crash the daemon.
- A `restore` CLI path: given a snapshot, copy it over the target store while the daemon is stopped; validated with `integrity_check` before swap.

### 2. Retention + WAL maintenance (`bus/persistence.py`, TTL sweep)

- A `compact()` method: `DELETE FROM envelopes WHERE <terminal-state> AND <age > retention_days>`, then `PRAGMA wal_checkpoint(TRUNCATE)`. Terminal = acked/expired states already distinguished by the sweep. Retention window config-driven (default 7 days). A periodic `VACUUM` (daily) reclaims freed pages. Hooked into the existing `run_ttl_sweep_once` cadence.

### 3. Corruption guard (`bus/persistence.py` connect path)

- At `connect()`, run `PRAGMA integrity_check` (or `quick_check`). On `ok` → proceed. On failure → move the file to `<store>.corrupt-<ts>`, restore the newest snapshot that itself passes `integrity_check`, log a loud error, and continue; if no good snapshot exists, refuse boot with a clear message rather than crash-loop.

### 4. Migration framework (`bus/persistence.py`)

- Read `PRAGMA user_version`; apply each migration step with `version > current` inside a transaction, bumping `user_version` on success. Steps 1–2 = the existing `urgency` and `next_attempt_at` columns. New columns become new steps. Removes the `table_info` reflection.

### 5. Scheduler WAL (`endpoints/scheduler.py`)

- Set `journal_mode=WAL` on engine init (an `event.listen`/`connect` PRAGMA or engine `connect_args`); include `scheduler.db` in the backup set.

### 6. JSONL retention (`inbound/audit.py`, `mcp_audit/`, raw trails)

- A retention pass (age-based, default 30 days) over the daily JSONL directories, driven by the same sweep cadence.

## Ticket decomposition (dependency-ordered)

- **Eβ-1 — Backup/restore: scheduled `VACUUM INTO` snapshots of both stores → off-volume dir + generational retention + `restore` CLI.** *(no dep)* `[P0]` — closes the total-loss risk. **Auto-planned.**
- **Eβ-2 — Retention/compaction: delete terminal envelopes >7d in the TTL sweep + `wal_checkpoint(TRUNCATE)` + periodic VACUUM.** *(no dep)* `[P1]` — bounds unbounded growth. Held.
- **Eβ-3 — Corruption detection + recovery: `integrity_check` at boot → quarantine + restore from snapshot.** *(blocked_by Eβ-1 — needs the snapshot/restore path)* `[P2]`. Held.
- **Eβ-4 — Migration versioning: `user_version` + ordered migrations (replace ad-hoc ALTER).** *(no dep)* `[P2]`. Held.
- **Eβ-5 — Scheduler store on WAL + included in backup scope.** *(no dep)* `[P2]`. Held.
- **Eβ-6 — JSONL trail retention (audit + raw, 30-day age rotation).** *(no dep)* `[P2]`. Held.

Only Eβ-1 auto-plans (P0, no dependencies). Eβ-3 depends on Eβ-1 but is itself P2, so it stays held (the auto-plan rule covers a P0 and what a P0 relies on — not a P2 that relies on a P0).

## Testing / validation

- **Backup:** a snapshot of a live WAL DB is a valid, `integrity_check`-clean SQLite file containing the committed rows; generational retention prunes to exactly 24 hourly + 14 daily; a backup failure logs loudly and does not crash the sweep; `restore` swaps a snapshot in and the daemon reads it.
- **Retention:** terminal envelopes older than the window are deleted; younger ones and non-terminal ones are kept; DLQ rows are untouched; after compaction `wal_checkpoint(TRUNCATE)` shrinks the WAL; row counts and file size drop.
- **Corruption:** a deliberately-corrupted store is quarantined and the newest good snapshot restored at boot; with no good snapshot, boot refuses with a clear message (no crash-loop).
- **Migration:** a store at `user_version=0` migrates forward through all steps idempotently; re-running is a no-op; a fresh DB lands at the current version.
- **Scheduler WAL:** `scheduler.db` reports `journal_mode=wal` after start; it appears in the backup set.
- **JSONL retention:** files older than the window are dropped; newer retained.

## Strengths to preserve

WAL + in-flight-requeue + TTL/DLQ sweeps + 0600 perms + hot-path indices, and the secret-safe async MCP audit — all untouched. Eβ adds snapshots, bounded retention, corruption safety, and versioned migrations *around* the existing store without changing the at-least-once delivery semantics.
