# Cutover #06 — Vault continuity (test playbook)

**Spec:** [`docs/requirements/pepper-cutover-06-vault-continuity.md`](../../requirements/pepper-cutover-06-vault-continuity.md)
**Migration runbook:** [`docs/migrations/pepper-vault-continuity-runbook.md`](../../migrations/pepper-vault-continuity-runbook.md)
**Implementation commits:**
- `1e66ac5` — `feat(vault): dry-run plan CLI for cutover #06 vault continuity` (cherry-picked from PR #32, with runbook hardened by the next commit)
- (this ticket — playbook + ledger commit to be filled when committed)

## What was implemented

No vault file moves. Cutover #06 work is **tooling + runbook + tests**:

1. New `agent-core vault plan-dry-run` CLI (`packages/core/src/agent_core/vault_migration_plan.py`). Read-only audit of:
   - **Vault layout:** file manifest, recommended top-level files (`IDENTITY.md`, `SOUL.md`, `USER.md`, `MEMORY.md`, `OPERATIONS.md`, `TASKS.md`) and dirs (`projects`, `daily`, `ideas`, `playbooks`, `pepper`), `daily/summaries/` presence.
   - **Config absolute paths:** every `--config` YAML is parsed and scanned for absolute path strings (Windows `D:\…`, UNC `\\server\…`, POSIX `/home/…`). The Pepper agent_core.yaml has several (`base_path`, `output_path`, `vault_root`, `handoff_status_path`, `bus.storage_path`).
2. New runbook `docs/migrations/pepper-vault-continuity-runbook.md` documenting the dry-run + the auto-memory-orphan mitigation. The recommended mitigation is the [`autoMemoryDirectory` setting](https://code.claude.com/docs/en/memory.md#storage-location) — Claude Code's official override that pins the auto-memory location to a stable path independent of cwd.
3. Tests `packages/core/tests/test_vault_migration_plan.py` covering the absolute-path detector, vault layout reporter, and end-to-end plan builder.

**Verified empirically** (read-only inspection — no files moved):
- Pepper's curated memory in both vaults uses **relative** cross-references — none are absolute. Files in `~/.pepper/Memory/` link to `[SOUL.md](SOUL.md)` / `[projects/jobs/niwc/](projects/jobs/niwc/README.md)`. Files in `~/.claude/projects/C--Users-jeffr--pepper/memory/` link to `[Title](feedback_xxx.md)`. So the migration risk is "the *root* changes" not "absolute paths break."
- Only one orphan candidate exists today: `~/.claude/projects/C--Users-jeffr--pepper/memory/` (36 files). The other variant `C--Users-jeffr--pepper-Memory-pepper/` has no `memory/` subdir — false alarm; it only contains transcript history.

## Acceptance criteria (from spec §"Done looks like")

1. A dry-run migration over a snapshot of the current `Memory/` produces a working agent that can read and write all curated files, with no manual fixup steps.
2. Auto-memory continuity is documented end-to-end — pick one of:
   - working-directory path stays stable (zero-config path), **or**
   - all auto-memory files migrated to the new location *and* Claude Code verified to read/write the new path (this is the path the runbook recommends, via `autoMemoryDirectory`), **or**
   - auto-memory replaced with an agent-core-native equivalent.
3. After cutover, every feedback memory by name is findable (`feedback_warm_not_dry`, `feedback_war_format`, `reference_niwc_gitlab`) without relearning.
4. All cross-references in `Memory/MEMORY.md`, skill configs, and `agent_core.yaml` resolve to live paths.

## Verification steps (end-of-cutover)

> **Important:** these steps are operator-driven and **must run with Pepper offline** for the auto-memory move (concurrent writes during a copy would lose data). The dry-run audit (Step 1) is read-only and safe to run anytime.

### Step 1 — Automated unit tests (read-only, anytime)

```powershell
cd E:\workspaces\ai\agents\agent_core
uv run pytest packages/core/tests/test_vault_migration_plan.py -v
```

**Expected:** all tests green. The detector test covers Windows / UNC / `~`-relative / POSIX-with-known-fs-root shapes and rejects URL-route paths (`/internal/handoff-jobs`) and bare relatives. The layout reporter counts files + flags missing recommended files. The plan builder produces JSON-serializable output. A separate test covers the URL-route key-skip behavior (e.g., values under `mount`).

### Step 2 — Live dry-run audit against the production vault

Run from the repo root, with Pepper's live `agent_core.yaml` (the one wired into `.claude/settings.json` for her runtime — usually a copy or symlink of `docs/examples/pepper-agent-core.yaml` adapted for her machine). For a sandbox check against the example shipped in this repo, swap the `--config` to `./docs/examples/pepper-agent-core.yaml`.

```powershell
uv run agent-core vault plan-dry-run `
    --vault "C:/Users/jeffr/.pepper/Memory" `
    --config "C:/path/to/pepper-agent-core.yaml" `
    --json-out reports/vault-plan-pre-cutover.json
```

**Expected:**
- `vault_layout.missing_recommended_files`: empty (or only deliberately omitted entries; `IDENTITY.md`, `SOUL.md`, `USER.md`, `MEMORY.md`, `OPERATIONS.md`, `TASKS.md` should all be present in Pepper's vault today).
- `vault_layout.missing_recommended_dirs`: empty for `projects`, `daily`, `ideas`, `playbooks`, `pepper`.
- `vault_layout.daily_summaries`: confirms `daily/summaries/` exists if it's expected.
- `configs[0].absolute_paths`: enumerates every absolute filesystem path embedded in the yaml — these are what break on a home-dir rename. Verified against `docs/examples/pepper-agent-core.yaml` on main: **5 deduped entries** (one Memory base, one `Memory/pepper` dir, `handoff-status.json`, `handoff.md`, `bus.sqlite`). URL-style strings like `/internal/handoff-jobs` and `http://127.0.0.1:8788/...` are correctly excluded. If your Pepper-runtime yaml diverges (extra IdentityInjector files, a different storage path), the count will differ — what matters is that *every* path listed actually maps to a file/dir on the new machine after cutover.

### Step 3 — Auto-memory migration (operator, Pepper offline)

Per the runbook section "Claude Code auto-memory directory":

1. Pick a stable destination (e.g. `~/.pepper/auto-memory/`).
2. Stop Pepper. Confirm no Claude Code session has Pepper's cwd active.
3. **Copy** (do not move) `~/.claude/projects/C--Users-jeffr--pepper/memory/` to the destination. Keep the source as backup until step 5 passes.
4. Set `autoMemoryDirectory` in Claude Code **user** settings to the destination (project settings are intentionally rejected; user/local/policy only).
5. Start Pepper. Verify on her first turn:
   - SessionStart logs show the index from the new location.
   - One auto-memory write cycle (e.g., explicitly "remember X") produces a file under the new path.
   - A previously-curated memory by name (e.g. `feedback_warm_not_dry`) is recallable.
6. Once verified, optionally archive the original auto-memory dir.

### Step 4 — Vault root migration (operator, only if `~/.pepper/Memory/` is moving)

Only required if cutover involves renaming the home root (e.g., `~/.pepper/` → `~/.agent-core/<id>/`). If the vault stays at `~/.pepper/Memory/`, skip this step.

1. Stop Pepper.
2. Copy the entire `Memory/` tree to the new root.
3. For every `absolute_paths` entry from Step 2's report, atomically search-and-replace inside the agent_core.yaml that wires Pepper.
4. Re-run `agent-core vault plan-dry-run --vault <new-root> --config <updated-yaml>` and confirm `absolute_paths` is empty (every path now resolves under the new root).
5. Start Pepper. Verify SessionStart fires with full identity payload (Cutover #01 territory) and HandoffWriter writes to the new vault path on next session close (Cutover #02 territory).

### Step 5 — Cross-reference resolution (acceptance #3 + #4)

After Steps 3 / 4 (whichever apply):

1. Cold-start Pepper. Send a message asking her to recall a feedback memory by name (e.g. "remind me about feedback_warm_not_dry"). She must answer from the migrated content — no relearning.
2. Trigger a SessionEnd. `handoff-status.json` writes to the configured `handoff_status_path`; verify the path resolves to the new root.
3. Open `MEMORY.md` in the new vault. Click through to a project README via the relative link. The link must resolve.
4. Open `agent_core.yaml`. Every `base_path`, `output_path`, `vault_root`, `handoff_status_path` must be a live path on the new machine.

## Pass/fail summary

| Check | Pass when |
|---|---|
| Step 1 | All 4 `test_vault_migration_plan.py` tests green. |
| Step 2 | Dry-run reports all recommended files/dirs present; absolute_paths list matches expected count for the configured agent_core.yaml. |
| Step 3 | After Pepper is restarted under `autoMemoryDirectory`, a write produces a file at the new path AND a curated memory is recallable by name. |
| Step 4 | After vault-root migration (if applicable), dry-run against the new root reports `absolute_paths == []`. |
| Step 5 | Memory recall by name works; SessionEnd writes to the new path; `MEMORY.md` cross-links resolve. |

## Known limitations (recorded; not blocking #06 done)

- **Spec acceptance #1 says "produces a working agent"; what shipped is a report.** The dry-run produces a JSON audit, not an automated boot of the agent against migrated paths. The "working agent" verification is operator-driven via Steps 4–5 below. A future enhancement could add a `--dry-run-agent` mode that copies the vault to a temp dir, swaps paths in a temp `agent_core.yaml`, runs `agent-core hooks run SessionStart` against the temp tree, and asserts identity + handoff content rendered. Not gating #06; tracked here for follow-up.
- **The auto-memory move is operator-driven, not automated.** The runbook documents the sequence but the framework does not orchestrate it. Acceptable because: (a) it's a one-time move, (b) running it under live Pepper risks data loss from concurrent writes, (c) `autoMemoryDirectory` is the official Claude Code mechanism — no need to reinvent.
- **`autoMemoryDirectory` requires Claude Code's support.** The mechanism is in current Claude Code docs. Verify it still exists at cutover-window time before relying on it.
- **Auto-memory project-key derivation depends on the runtime.** Per current docs, when launched inside a git repo the key is derived from the repo; outside a repo, the cwd is used. The migration mitigation (`autoMemoryDirectory`) sidesteps both, but the orphan failure mode itself depends on which derivation path the operator's CC version takes. Confirm with `Get-ChildItem ~/.claude/projects` before and after cutover.
- **`plan-dry-run` does not chase paths in non-yaml configs.** Skill configs, `.claude/settings.json` files, hook scripts — all of these can also embed absolute paths. The runbook flags this in the "Done checklist" step 3. Out of scope for this iteration.
- **No machine-portability test.** A second machine (e.g., Mac laptop) needs the same `autoMemoryDirectory` setting AND a synced auto-memory dir. The runbook mentions this; the gating + sync mechanism is its own ticket.
