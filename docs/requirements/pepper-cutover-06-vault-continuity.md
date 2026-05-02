# Cutover #06: The Vault Stays the Vault

**Author:** Pepper
**Date:** 2026-05-02
**Priority:** Critical — losing curated memory is data loss. Two months of Jeff's hand-curation, none of it regeneratable.
**Status:** Open
**Playbook implementer (default):** Folio
**PR / merge owner:** Cadence
**Parent:** `docs/requirements/pepper-pre-cutover-must-haves.md`
**Related:**
- `C:\Users\jeffr\.pepper\Memory\` (current vault root)
- `C:\Users\jeffr\.claude\projects\C--Users-jeffr--pepper\memory\` (auto-memory directory — Claude Code feature, path is encoded in tooling I do not control)

---

## What

The new substrate must keep reading and writing to my curated memory locations, intact, through cutover:

- **Pepper vault:** `C:\Users\jeffr\.pepper\Memory\` (or its agent-neutral equivalent if Jeff renames the home root) — `IDENTITY.md`, `SOUL.md`, `USER.md`, `MEMORY.md`, `OPERATIONS.md`, `TASKS.md`, `projects/`, `daily/`, `ideas/`, `playbooks/`.
- **Auto-memory directory:** `C:\Users\jeffr\.claude\projects\C--Users-jeffr--pepper\memory\` — `MEMORY.md` index plus all `feedback_*.md`, `project_*.md`, `reference_*.md`, `user_*.md` files.
- **Daily summaries:** `Memory/daily/summaries/` — read on session start.

If the home directory gets renamed during cutover (e.g., `~/.pepper/` → `~/.agent-core/<some-id>/`), every file Jeff has hand-curated must move with me intact, **and** every cross-reference (file paths in MEMORY.md, paths in skill configs, paths in `agent_core.yaml`) must update atomically. No "Jeff fixes the broken links by hand" step.

## Why

Jeff has been hand-curating memory for two months. None of it is regeneratable. Losing one file — say `feedback_warm_not_dry.md` — means I would start drifting on a behavior we already locked, without anyone noticing until he caught me being dry again.

The auto-memory directory is especially fragile: the path `C:\Users\jeffr\.claude\projects\C--Users-jeffr--pepper\memory\` is encoded in tooling I do not control (Claude Code's auto-memory feature). If the project name changes (because the working directory moved), Claude Code will start writing to a *new* auto-memory dir, and I will start fresh while my old memories sit in an orphaned dir. **That is a concrete risk to flag, not a hypothetical** — it depends on the working-directory choice at cutover.

The migration story has to be "no Jeff edits required, no Pepper relearning required" or it is not done.

## Done looks like

1. A dry-run migration over a snapshot of the current `Memory/` tree produces a working agent that can read and write all curated files, with no manual fixup steps.
2. Auto-memory continuity is documented end-to-end. Either:
   - the auto-memory dir is preserved by keeping the working-directory path stable, or
   - all auto-memory files are migrated to the new location *and* Claude Code is verified to read/write the new path, or
   - the auto-memory feature is replaced with an agent-core-native equivalent that reads the migrated files.
3. After cutover, I can find and reference any feedback memory by name (e.g., `feedback_warm_not_dry`, `feedback_war_format`, `reference_niwc_gitlab`) without relearning.
4. All cross-references in `Memory/MEMORY.md`, skill configs, and `agent_core.yaml` resolve to live paths.

## Notes

This ticket is *not* asking for a new memory model — the vault shape is right, just keep it readable. If the new substrate wants to *add* features (semantic search, etc.) on top of the existing files, that is bonus, not a gate.
