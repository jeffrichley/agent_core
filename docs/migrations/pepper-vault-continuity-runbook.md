# Pepper vault continuity (cutover #06)

This runbook supports [`pepper-cutover-06-vault-continuity.md`](../../requirements/pepper-cutover-06-vault-continuity.md): keep curated Memory paths and config cross-references coherent across a home-directory rename or machine move.

## Dry-run path audit

From the `agent_core` repo (with `uv sync` applied):

```bash
uv run agent-core vault plan-dry-run --vault "C:/Users/you/.pepper/Memory" --config ./docs/examples/pepper-agent-core.yaml
```

- Repeat `--config` for every `agent_core.yaml` (or copy) that embeds absolute paths.
- Add `--json-out ./reports/vault-plan.json` to persist the report next to snapshots in CI or migration tickets.

The command is read-only except for the optional JSON output file.

## What the report contains

- **vault_layout**: full file manifest under `--vault`, counts, and whether recommended top-level files / directories from the ticket are present.
- **daily_summaries**: notes whether `daily/summaries/` exists (session-start summaries per spec).
- **configs**: each `--config` file plus deduplicated absolute paths found anywhere in the parsed YAML (Windows `D:\…`, UNC `\\server\…`, POSIX `/home/…` style strings).

Use the path list to plan atomic search-and-replace when the vault root or Claude project path changes.

## Claude Code auto-memory directory

The canonical risk is documented in the ticket: the auto-memory path under `~/.claude/projects/<project-key>/memory/` is tied to a **project key** Claude Code derives from the runtime context. Per the current [Claude Code memory docs](https://code.claude.com/docs/en/memory.md#storage-location), the project key is derived from the git repository when one is present (so worktrees and subdirectories of the same repo share one auto-memory dir); outside a git repo, the working-directory path is used. Either way, if the derivation input changes (the repo root path moves, or a non-repo cwd changes), Claude Code starts writing to a *new* auto-memory dir and the old files orphan.

**Recommended mitigation (the simple, supported one):** set `autoMemoryDirectory` in policy, local, or user Claude Code settings. Per the docs:

```json
{
  "autoMemoryDirectory": "~/.pepper/auto-memory"
}
```

Constraints (verify against the docs at cutover time):

- Read from **policy, local, or user** settings — *not* project settings (`.claude/settings.json` in a repo). The docs explain this is to prevent a shared project from redirecting auto-memory writes to sensitive locations.
- We don't depend on mid-session rebind; the migration sequence below stops Pepper before changing the setting and restarts after.
- The path is literal (no stable-id mode); we choose a fixed path and stop encoding cwd / repo root.
- Machine-local. A second machine needs the same setting *and* a synced auto-memory dir.

**Migration sequence** (operator-driven; Pepper must be offline so the source dir is quiescent):

1. Pick the stable destination, e.g. `~/.pepper/auto-memory/`.
2. Copy (do not move) `~/.claude/projects/<current-project-key>/memory/` to the destination. Keep the source as a backup until step 6.
3. Set `autoMemoryDirectory` in Claude Code user settings to the destination.
4. Start Pepper. Send one prompt that triggers auto-memory write (e.g., "remember that X").
5. After the session, list the destination dir's recent writes (`Get-ChildItem ~\.pepper\auto-memory -Recurse | Select FullName, LastWriteTime`) and confirm a fresh file or modification under the new path. Then start a second session and confirm a previously-curated memory by name (e.g. `feedback_warm_not_dry`) is recallable.
6. Once both checks pass, archive the original auto-memory dir (don't delete — keep at least until next vault snapshot). The original dir at `~/.claude/projects/<old-key>/memory/` is now safe to leave in place; Claude Code will not write to it again as long as `autoMemoryDirectory` is set.

**Fallbacks** (only if the override turns out to be unavailable in your CC version):

- Keep the working-directory path stable so the encoded cwd doesn't change. Brittle but zero-config.
- Replace auto-memory reliance with an agent-core-native injector reading the migrated files (larger change; revisit only if the override is gone in a future CC version).

The dry-run tool here does **not** automate the auto-memory move — it focuses on the **Pepper Memory vault** plus **YAML configs shipped or owned by the agent** (for example `docs/examples/pepper-agent-core.yaml`).

## Done checklist (operator)

1. Run `plan-dry-run` against a snapshot of production `Memory/`.
2. Confirm `missing_recommended_*` is empty or intentionally waived.
3. Resolve every `absolute_paths` entry after a rename (including skills and non-repo configs not passed to `--config`).
4. If the cwd is changing: set `autoMemoryDirectory` in Claude Code user settings, then copy the auto-memory dir to the new path and verify reads/writes (steps 1–5 in the section above).
5. Re-run hooks / SessionStart in a staging profile and confirm IdentityInjector + HandoffWriter paths resolve.
