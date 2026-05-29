# Canonical skills (packages/core/skills/)

This directory holds the **canonical, shipped skills** for bus endpoints implemented in the `core` package. Each skill lives under `skills/<skill-name>/` co-located with the endpoint it documents and depends on.

For the broader placement convention across all agent-core packages, see [`docs/skills/PLACEMENT.md`](../../../docs/skills/PLACEMENT.md).

## Skills in this directory

| Skill | Endpoint dependency | Endpoint source |
|---|---|---|
| `scheduler/` | `builtin.scheduler` | [`src/agent_core/endpoints/scheduler.py`](../src/agent_core/endpoints/scheduler.py) |

## How beings install these

Beings (Wren, Pepper, testbot, future beings) install these skills into their own `~/.<being>/.claude/skills/` directory via the **canonical-replacement-install pattern**:

1. Rename the being's current skill file (if present) to `<original>.stale-bak-YYYYMMDD`
2. Copy the new canonical version from this package into the being's vault using `cp -R src/. dst/` (the trailing `/.` matters on Git Bash — `cp -R src/ dst/` nests `src` inside `dst/` when `dst` already exists)
3. Verify post-install via line-count cross-check or `grep` for expected new content
4. After one auto-backup cycle, delete the `.stale-bak-YYYYMMDD` file

Until a unified install tool ships (planned future work), beings perform this manually. The pattern is documented in detail in Wren's `feedback_stale_bak_retention_pattern` memory and Pepper's equivalent.

## Adding a new endpoint-coupled skill to `core`

1. Implement the endpoint in `src/agent_core/endpoints/<endpoint>.py`
2. Author the skill in `skills/<skill-name>/SKILL.md` (+ optional `references/` subdir for deep-dive docs)
3. Update this README's table to add the new skill
4. Open a PR; criterion-check happens at PR review (see `docs/skills/PLACEMENT.md` for the criterion-check shape)

Skills should be **readable without their endpoint installed** — they ship together but the skill itself is also a setup guide for a reader who's never run the endpoint.
