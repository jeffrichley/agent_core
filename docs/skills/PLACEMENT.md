# Canonical skills: placement convention

This document records the placement and ownership convention for skills that ship with agent-core. It's the decision record so future contributors can answer "where does my new skill go?" without guessing.

## TL;DR

**Endpoint-coupled skills:** `packages/<owning-package>/skills/<skill-name>/`, where `<owning-package>` is the package containing the endpoint source code the skill depends on.

**Cross-cutting / non-endpoint-coupled skills:** deferred convention (YAGNI; we haven't shipped one yet — when we do, the natural home is either `packages/agent-core-hatchery/skills/` for hatchery-bundled generic skills or a new `packages/agent-core-skills/` package).

## The rule

A skill goes in `packages/<owning-package>/skills/<skill-name>/` where `<owning-package>` is the package containing the endpoint code the skill documents and depends on.

| Skill | Endpoint(s) it documents | Owning package | Skill home |
|---|---|---|---|
| `scheduler` | `builtin.scheduler` | `core` | `packages/core/skills/scheduler/` |
| `voice` (future) | `builtin.voice` | `agent-core-voice` | `packages/agent-core-voice/skills/voice/` |
| `briefs` (future) | `builtin.briefs_orchestrator` | `agent-core-briefs` | `packages/agent-core-briefs/skills/briefs/` |
| `discord` (future) | `builtin.discord` | `agent-core-discord` | `packages/agent-core-discord/skills/discord/` |
| `channel` (future) | `builtin.claude_code_mcp` (channel relay) | `agent-core-channel` | `packages/agent-core-channel/skills/channel/` |

## Why co-locate skill with endpoint

Three reasons:

1. **The dependency is explicit.** A skill documenting how to use endpoint X can't fulfill its purpose if endpoint X isn't installed. Shipping them together makes "install the endpoint, get the skill" a single act.
2. **PR boundaries match ownership.** A change to the voice plugin AND its skill stays inside `packages/agent-core-voice/` — the diff doesn't sprawl across packages, and reviewers see both halves of the change.
3. **Discoverability follows source.** A reader-of-the-source-tree can always answer "where's the skill for X?" by going to wherever X's endpoint lives and looking in `skills/`. No central registry to keep in sync.

## Why NOT centralize everything in `core/skills/`

The scheduler endpoint happens to live in `core` because it's a built-in. But voice, briefs, discord, and channel endpoints each live in their own packages (`agent-core-voice/`, `agent-core-briefs/`, `agent-core-discord/`, `agent-core-channel/`). Centralizing skills in `core/skills/` would force a separation between endpoint code (in its own package) and the skill documenting it (in core), breaking principle #1 above. The rule is consistent: skill follows endpoint, wherever the endpoint lives.

## Shipped-canonical vs. being-personal

There are two distinct kinds of skills in the ecosystem:

- **Shipped-canonical skills** live under `packages/*/skills/` in this repo. They're installed into every being's vault at hatch time (or via the canonical-replacement-install pattern for already-hatched beings). All beings should have the same canonical version.
- **Being-personal skills** live only in `~/.<being>/.claude/skills/`. They're custom workflows the being authors for themselves. They are NOT canonical, NOT shipped, NOT auto-synced across beings. Pepper might write a `pepper-specific-briefing-formatter`; Wren might write a `wren-specific-vault-organizer` — neither belongs in this repo.

When a being-personal skill matures into something every being would benefit from, it can be promoted by adding it to the appropriate `packages/*/skills/` directory (with criterion-check). Until then, it stays personal.

## Canonical-replacement-install pattern

When a being already has a previous version of a shipped-canonical skill in their vault and a new version ships, the install pattern:

1. **Rename the existing file** in the being's vault to `<original>.stale-bak-YYYYMMDD`. This protects against the new version having a problem the criterion-check missed, AND ensures the previous version survives one round of auto-backup.
2. **Copy the new canonical version** from this repo into the being's vault. **Use `cp -R src/. dst/`** (with the trailing `/.`) when copying a directory tree into an existing directory — `cp -R src/ dst/` on Git Bash nests `src` INSIDE `dst/` when `dst/` already exists, leading to mixed-version contents.
3. **Verify post-install** via line-count cross-check or `grep` for expected new content. If counts don't match what the producer reported, you probably nested the install — undo and use the `src/.` form.
4. **After one auto-backup cycle** (typically next day for daily backups), delete the `.stale-bak-YYYYMMDD` file. Don't let stale-baks accumulate; vault clutter is a real cost.

Until a unified `agent-core install <skill> --being <name>` tool ships, beings or their drivers perform this manually.

## Criterion-check at PR time

Before merge, a new or updated shipped-canonical skill should get a criterion-check from at least one being (typically the one most likely to be a heavy consumer). The criterion-check covers:

- **State coverage** — does the skill answer the questions a real consumer arrives with?
- **Edge-case symmetry** — do similar operations have similar shapes? Anti-patterns explicitly called out?
- **Demand-validated examples** — are examples grounded in real production jobs/configs, not invented?
- **Instruction-accessibility** — can a being who's never seen the substrate follow the skill cold? Do literal copy-pastes of code examples actually work?

The reviewer should run at least one example end-to-end as part of the review — examples that look right on paper but fail at runtime are the most expensive failure mode this convention exists to prevent.

## Decision record

This convention was established 2026-05-29 in PR #(TBD) when the scheduler skill became the first canonical shipped skill in agent-core. Prior to that, skills lived only in individual being vaults (`~/.<being>/.claude/skills/`). The need for canonical shipping was named by Jeff after observing that:

- Documentation about an endpoint should ship WITH the endpoint, not as separate folklore in being vaults
- Future beings (hatched after the convention) should get the canonical skills automatically
- Already-hatched beings (Wren, Pepper, testbot) need an install path that doesn't surprise their existing vault state — hence the stale-bak retention pattern

The convention is intentionally light on tooling for v1 — no install command, no auto-sync, no version tracking. As the skill library grows past a handful of entries, a future `agent-core install <skill>` tool should subsume the manual pattern.
