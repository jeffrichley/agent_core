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

The canonical risk is documented in the ticket: the path under `.claude/projects/.../memory/` is tied to the **encoded project path**. Mitigations (pick one with Jeff before cutover):

1. **Keep the working-directory path stable** so Claude Code keeps writing the same auto-memory folder, or  
2. **Copy** the old auto-memory tree into the new location and verify Claude Code reads/writes there after the move, or  
3. **Replace** reliance on auto-memory with an agent-core-native injector that reads the migrated files (larger product change).

None of these are automated here; the dry-run tool focuses on the **Pepper Memory vault** plus **YAML configs shipped or owned by the agent** (for example `docs/examples/pepper-agent-core.yaml`).

## Done checklist (operator)

1. Run `plan-dry-run` against a snapshot of production `Memory/`.
2. Confirm `missing_recommended_*` is empty or intentionally waived.
3. Resolve every `absolute_paths` entry after a rename (including skills and non-repo configs not passed to `--config`).
4. Re-run hooks / SessionStart in a staging profile and confirm IdentityInjector + HandoffWriter paths resolve.
