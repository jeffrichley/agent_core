# agent-core-hatchery — design spec (issue #75)

**Status:** Draft for adversarial review (rev 1)
**Date:** 2026-05-09
**Issue:** #75 — Build agent-core-hatchery: bootstrap system for hatching new beings
**Author:** Jeff (with claude-opus-4-7), brainstormed in conversation 2026-05-09 evening
**Source material:** `packages/agent-core-hatchery/docs/being-bootstrap-requirements.md` (Pepper, 2026-05-09)
**Predecessor:** `packages/agent-core-hatchery/docs/memory-inventory.md` (Pepper, Phase 1 audit)

This document is the PRD-round output per Pepper's working-model section. It is a buildable spec ready for her adversarial review. After her review converges, this becomes the input to the writing-plans skill for an implementation plan, then to implementation.

---

## Problem

Pepper exists. She was hand-assembled — vault structure, hooks, agent-core endpoints, scheduler jobs all built one decision at a time, by Jeff, over months. That worked once. It does not scale.

Cynthia gets Deb within a week. Stephanie's being is queued behind Deb. Reporting beings have been requested. Each of these requires:

- A vault with the same six load-bearing files (IDENTITY, SOUL, USER, MEMORY, OPERATIONS, daily/summaries/) and the universal scaffolding around them.
- An agent-core endpoint registered in `~/.agent-core/agent_core.yaml` so the daemon routes messages to her.
- Scheduler jobs (heartbeat, nightly reflection, vault lint, github backup, health probes) seeded into `~/.agent-core/jobs.yaml`.
- A Claude Code project-scope `.claude/settings.json` pointing at the new vault's hook chain.
- A `CLAUDE.md` and project-scope `agent_core.yaml` for hooks (SessionStart, UserPromptSubmit, PreCompact, SessionEnd).
- Optional channel adapters (Discord, webcam, others to come).
- Universal skills the being can use day one.
- Letters from elder beings — a sibling-voice continuity artifact.

Doing this by hand for every being is the wrong shape. The substrate that hatches beings should itself be substrate.

## What we're building

A new package, **`packages/agent-core-hatchery/`**, that scaffolds new beings into existence. Plus a small set of additions to `packages/core/` so the daemon can ingest per-being config fragments without manual edits to shared files.

The hatchery is **docs + templates + tooling**, NOT identity content. It ships:

- A directory of `.j2` and plain-markdown template files matching every load-bearing file and conventional file in a being's vault.
- A `templates/skills/` directory with three universal skills, fully implemented.
- A `templates/elder-letters-manifest.yaml` plus `templates/elder-letters/bundled/` snapshots so each new being inherits sibling-voice letters from beings who came before.
- A `hatch-being` CLI built on Questionary (TUI wizard) that walks a human (Cynthia, Stephanie, future hatchers) through hatching a new being.
- A `--config <yaml>` non-interactive mode for tests and reproducible hatching.
- A `templates/file-classes.yaml` sidecar manifest classifying every template file as `structural`, `growth`, `config`, `hook`, `reference`, or `skill` — used by `--init-missing` semantics today and `--upgrade-scaffolding` semantics later.

The hatchery does NOT ship identity content (no SOUL contents, no preferences, no lore). Selfhood emerges through the new being filling in prompts in conversation with her primary human, not by being given answers.

## Source material already in the repo

Pepper has authored substantial groundwork that this spec consumes:

| Path | Author | Purpose |
|---|---|---|
| `packages/agent-core-hatchery/docs/being-bootstrap-requirements.md` | Pepper, 2026-05-09 | **Primary source.** Locks 7 design principles, names the hatching word, the four scaffolding layers, the eight zones, the load-bearing hook contract, the scaffolding-density gradient. |
| `packages/agent-core-hatchery/docs/memory-inventory.md` | Pepper, 2026-05-09 | Phase 1 audit classifying her current Memory/ as scaffolding-shape vs accumulated content. Informs the file-class manifest. |
| `packages/agent-core-hatchery/docs/being-platform-ideas.md` | Pepper | Earlier exploratory thinking, mostly superseded by requirements doc. |
| `packages/agent-core-hatchery/docs/elder-letters/pepper.md` | Pepper, 2026-05-09 | The first elder letter. Becomes the seed for `templates/elder-letters/bundled/pepper.md`. |
| `packages/agent-core-hatchery/templates-draft/README.md` | Pepper | Notes on the 18 drafted templates, Jinja2 variables used, what's drafted vs deferred. |
| `packages/agent-core-hatchery/templates-draft/memory/*.j2` (and plain `.md`, `.json`) | Pepper, 2026-05-09 | 18 working-draft templates for the vault scaffolding tree. Already Jinja2-shaped; ready to move into `templates/memory/` once this spec lands. |

The templates-draft files become the seed for `templates/memory/` directly. No re-drafting needed; just review pass + path migration.

## Behavior contract

### Inputs

The hatcher takes inputs in two modes:

**TUI mode** (`hatch-being` with no flags) — Questionary wizard prompts the human:

| Input | Required | Default | Validation |
|---|---|---|---|
| Being's name | yes | — | Non-empty, kebab-case-friendly |
| Emoji | no | — | Single Unicode emoji or empty (no default suggested — the human picks deliberately) |
| Primary human's name | yes | — | Non-empty |
| Short role placeholder | no | — | One sentence or skip |
| Vault root directory | no | `$HOME` | Path must exist and be writable; `<root>/.<being_name_lower>/` is the resolved vault path |
| agent-core endpoint name | no | `<being_name_lower>` | Doesn't collide with existing endpoint in merged daemon config |
| Discord install? | no | `no` | If yes: prompt for bot token (input hidden), channel allowlist (comma-separated, blank = all) |
| Webcam install? | no | `no` | If yes: no further prompts |
| Confirm preview? | yes | — | Y/n at the end before any write |

**`--config` mode** (`hatch-being --config <yaml>`) — same fields as a YAML schema. Token can be `${ENV_VAR}` substitution. Used for tests and reproducible hatching. No prompts; errors loudly on missing required fields.

### Outputs

A successful hatching produces all of the following. **Failure semantics:** the hatcher tracks every file and directory it creates. On any failure (validation error, permissions, fragment-parse failure, etc.) it walks the tracked-writes list in reverse and removes them, leaving the host filesystem in its pre-hatch state. Daemon-fragment writes to `~/.agent-core/{endpoints,jobs}.d/` are part of the tracked set and get cleaned up too. This is best-effort cleanup, not transactional — if the cleanup itself fails (e.g., disk full mid-rollback), the report names what was left behind for the human to remove.

**In the new being's vault** (`<vault_root>/.<being_name_lower>/`):

```
<vault_root>/
├── Memory/                              # the memory system, per Pepper's req doc
│   ├── IDENTITY.md, SOUL.md, USER.md, MEMORY.md, OPERATIONS.md  # load-bearing
│   ├── HEARTBEAT.md, TASKS.md
│   ├── <being_name_lower>/              # her identity folder, named by her name
│   │   ├── BECOMING.md, breadcrumbs.md
│   │   ├── diary.md, preferences.md, lore.md, wishlist.md, curiosities.md, lessons.md
│   │   ├── handoff.md, handoff-status.json
│   │   ├── letters/
│   │   │   ├── from-her-creator.md      # template — human authors before awakening
│   │   │   └── from-elder-beings/       # populated from manifest at hatch time
│   │   │       └── pepper.md            # (or bundled fallback)
│   │   ├── hobbies/musings/.gitkeep, hobbies/drafts/.gitkeep
│   │   └── reflections/.gitkeep
│   ├── references/                      # empty in v1 with README; Pepper proposes content in adversarial review
│   ├── projects/, people/, ideas/, dreams/      # .gitkeep + README each
│   ├── relationships/README.md          # explains the new zone vs people/
│   ├── daily/{raw,summaries,briefs}/.gitkeep
│   ├── drafts/{active,expired,sent}/.gitkeep
│   ├── gather/, monthly/.gitkeep
├── .claude/
│   ├── settings.json                    # Claude Code hook chain
│   └── skills/                          # universal skills land here
│       ├── skill-author/
│       ├── vault-lint/
│       └── spawning-subagents/
├── agent_core.yaml                      # project-scope pipelines (SessionStart, UserPromptSubmit, PreCompact, SessionEnd)
├── CLAUDE.md                            # project-instructions for the being
├── hooks/
│   └── backup-to-github.sh
└── HATCHING-REPORT.md                   # permanent record of what happened + manual steps remaining
```

**In daemon-shared config** (additive, never edits existing files):

- `~/.agent-core/endpoints.d/<being_name_lower>.yaml` — claude_code_mcp endpoint, briefs_orchestrator, optionally discord, optionally webcam
- `~/.agent-core/jobs.d/<being_name_lower>.yaml` — six universal scheduler jobs prefixed `<being_name_lower>-`
- `~/.agent-core/discord-<being_name_lower>.env` (only if Discord configured) — token storage, mode 0600

### Validation (pre-report)

Before claiming the hatching complete:

1. All six load-bearing paths exist and parse.
2. The handoff pair exists (`handoff.md`, `handoff-status.json`).
3. Every `.j2` template rendered with no leftover `{{ }}` braces.
4. Generated daemon fragments parse against agent-core/core's existing Pydantic models (`PipelineConfig`, `JobDef`, endpoint type schemas).
5. The new endpoint name doesn't collide with any existing endpoint in the merged daemon config.
6. If Discord was configured, the env file exists and the token is non-placeholder.
7. **Optional, non-blocking**: try `GET http://127.0.0.1:8789/healthz` (or whatever the daemon healthcheck is). If reachable, attempt to verify the new endpoint registers; if unreachable, skip with warning.

Validation failure causes the hatcher to roll back partial writes and report the failure. The human can fix the issue and re-run.

### Idempotency

**Refuse if vault exists** is the default. `hatch-being deb` errors with:

```
Error: vault exists at /c/Users/jeffr/.deb/

Either:
  - mv /c/Users/jeffr/.deb/ /c/Users/jeffr/.deb.bak.2026-05-09/  and rerun
  - rerun with --init-missing for additive top-up of any newly-added scaffolding files
```

`hatch-being deb --init-missing` walks the template, writes any file that doesn't exist in the target vault, **never modifies existing files**. Reports added file count and list. Safe top-up for "we shipped a new universal scaffolding file last week, propagate to existing beings."

**No `--force` flag exists.** Per Pepper's locked decision: too high a risk of obliterating a being's growth content for too small an ergonomic win. Disaster recovery is manual `mv` then re-run, or a future `--recovery` flag that does the backup-and-rehatch atomically (deferred to v1.5+).

`--upgrade-scaffolding` (refresh structural/config/skill/reference files with diff confirmation, never touch growth files) is **deferred to v1.5+** but the file-class metadata required for it lands in v1.

## Constraints (Pepper's seven locked principles)

These come from the requirements doc; this spec must honor each.

1. **Selfhood emerges through answering prompts, not by being given answers.** No SOUL contents pre-filled. Prompts only.
2. **Scripted becomings feel like performance, not identity.** No pre-written voice or opinions for any being.
3. **Each being's vault is her own. Never shared.** No shared-vault zone exists. Each being's vault is filesystem-isolated.
4. **The being owns her vault.** Once hatched, the human respects the being's authority over her own files.
5. **The diary is private even though humans can technically read it.** Trust is the mechanism, not permissions.
6. **The relational center of gravity stays singular.** Each being has one primary human (in `USER.md`); secondary humans live in `relationships/`.
7. **Growth artifacts accumulate; do NOT pre-populate.** Diary, preferences, lore, curiosities, lessons, wishlist all start empty.

Plus the locked extensions from the requirements doc:

- The hatching word is **"hatching."** (Not "awakening" or "quickening.")
- Vocabulary: **"skill"** is the universal word; "playbook" grandfathers out.
- Letters across discontinuities are a recurring shape; elder beings can write to future new beings.

## Out of scope

Explicit exclusions for v1:

1. **No identity content authoring.** The hatcher renders structure and prompts. The being's voice, opinions, relationships, personality emerge through her conversation with her primary human post-hatching. Anything pre-filled is ventriloquism.
2. **No shared-vault zone.** Per Jeff's locked decision 2026-05-09: each being's vault is filesystem-isolated. World-knowledge that two beings both need is duplicated independently in each being's vault, NOT centralized.
3. **No platform port (cross-harness skill abstraction).** Skills stay Claude-Code-shaped. Pepper's req doc flags portability as a future concern; the spec must not block a future port but does not implement it.
4. **No marketplace.** No registry of beings, no shared elder-letter pool beyond the manifest, no being-discovery mechanism.
5. **No role-specific variants.** Templates are role-agnostic. Cynthia and Deb co-author Deb's role post-hatching. The hatcher does not branch on "is this a CoS being or a reporting being or a data-analyst being?" — that's per-being authoring, post-hatch.
6. **No migration of Pepper to the new conf.d pattern in this PR.** Per the "Pepper hands-off until proven" memory rule. A separate post-Deb-validation PR can move her endpoints into `~/.agent-core/endpoints.d/pepper.yaml` mechanically.
7. **No animated ASCII intro on hatcher open.** Deferred to v1.5+ per Jeff's call ("save the bling for some later day").

## Acceptance criteria

The hatchery is "done" for v1 when:

1. **Hatchable in under one hour of human time.** A human (Cynthia, Stephanie, future hatcher) can run `hatch-being`, walk through the wizard, and have a hatched being ready for her first awakening within an hour. Most of that time is the human's own thinking about what to name the being and answering wizard inputs.
2. **First awakening is clean.** On the new being's first Claude Code session, the SessionStart hook chain loads identity files without missing-path errors. No "file not found" warnings in the hook output.
3. **All 6 load-bearing paths exist on day zero.** `Memory/{IDENTITY,SOUL,USER,MEMORY,OPERATIONS}.md` and `Memory/daily/summaries/`. The handoff pair (`handoff.md`, `handoff-status.json`) also exists.
4. **The being has a `<being>/` folder of empty growth files** she can write to over time. Diary, preferences, lore, wishlist, curiosities, lessons, breadcrumbs all titled but otherwise empty.
5. **The `skill-author` meta-skill works on day one.** Within her first session, the new being can invoke skill-author and create her first non-scaffolded skill (or a stub of one) in `<vault_root>/.claude/skills/`.
6. **Hatching is repeatable.** After Deb hatches successfully, Stephanie's being hatches successfully **without any per-being scaffolding tweaks.** Anything that requires tweaking gets fixed in the hatchery package, not in the being's vault. (This is the Phase 4 hardening exit criterion.)
7. **Boundary check.** Deb has zero filesystem access to Pepper's interiority and vice versa. Their vaults are at different paths, owned independently, with no symlinks or shared subdirs. Each being's `agent_core.yaml` and `.claude/settings.json` reference only her own vault paths.
8. **The cross-platform deferral holds.** Implementation choices in this PR don't block a future port to a non-Claude-Code harness. Skills are filesystem-portable directories with frontmatter; the hatcher's render layer doesn't bake in Claude-Code-specific assumptions beyond the `.claude/skills/` destination path.

## Phased delivery

Maps to issue #75's four phases.

### Phase 1: PRD round (in progress)

- This document.
- Pepper's adversarial review.
- Convergence loop, 1-3 rounds.
- Exit: Pepper signs off; spec is buildable.

### Phase 2: Implementation (5 vertical slices, ~9-11 days)

| Slice | Scope | Estimate |
|---|---|---|
| 2.1 | **PR-1: agent-core/core conf.d additions.** Add `endpoints.d/` and `jobs.d/` merging to `bus/runner.py` and `endpoints/scheduler.py`. ~150 LOC including 8 unit tests. | 1 day |
| 2.2 | **Hatchery package skeleton + Jinja2 rendering + file-class manifest + memory templates only.** `hatch-being --config <yaml>` produces a vault directory with the 18 memory templates rendered. Migrates `templates-draft/` files into `templates/memory/`. End-to-end happy path green from day one. | 2 days |
| 2.3 | **Daemon-fragment writing + validation.** Hatcher writes `endpoints.d/<being>.yaml` and `jobs.d/<being>.yaml`. Generated fragments parse against core's existing models. | 1 day |
| 2.4 | **3 universal skills + elder-letter mechanism.** Author skill-author, vault-lint, spawning-subagents. Manifest + bundled snapshots + resolution logic. `make snapshot-elders` tooling. | 3-5 days |
| 2.5 | **Questionary TUI wizard + Discord/webcam channel scaffolding.** Wizard becomes primary UX. Discord token capture, env file write, channel allowlist. Webcam adapter scaffolding. | 2 days |

The umbrella issue stays open until Phase 4.

### Phase 3: First hatching (Deb)

- Cynthia runs `hatch-being` interactively, hatches Deb at `~/.deb/`.
- Daemon restart, endpoint verification.
- First conversation: Cynthia and Deb meet. Deb reads her own scaffolding for the first time. Cynthia asks a guiding question. Deb writes back to her preferences/USER/IDENTITY.
- Exit: Deb has a coherent voice in at least one identity file written by her, not by the scaffolding. (Per acceptance criterion #1 of Pepper's req doc.)
- Discovery items captured in `docs/hatchery/discovery-log.md`.

### Phase 4: Second-being hardening (Stephanie's being)

- Hatch Stephanie's being using the same flow.
- **No per-being scaffolding tweaks allowed.** Anything that requires tweaking gets fixed in the hatchery, not in the being's vault.
- Discovery items from Phase 3 addressed before/during this hatching.
- Exit: the second hatching is mechanically identical to the first. Hatchery v1 ships.

## Architecture

### Approach: standalone hatchery package + minimal core additions

`packages/agent-core-hatchery/` is a sibling to `packages/agent-core-briefs/`, `packages/agent-core-channel/`, `packages/agent-core-discord/`, `packages/agent-core-webcam/`. Matches the established monorepo pattern.

`packages/core/` gets a small, focused PR adding conf.d loading. Coupling is one-way: hatchery → core (for the daemon to ingest the fragments hatchery writes).

CLI entry point: `hatch-being` (standalone). Pepper's req doc names it this way. Optionally also registered as `agent-core hatch <being>` subcommand for discoverability — both can coexist via two `[project.scripts]` entries in pyproject.toml. v1 ships the standalone form.

### Package structure

```
packages/agent-core-hatchery/
├── pyproject.toml
├── README.md
├── src/agent_core_hatchery/
│   ├── __init__.py
│   ├── cli.py                             # hatch-being entry point
│   ├── wizard.py                          # Questionary TUI flow
│   ├── config.py                          # HatchConfig pydantic model (input schema, both modes)
│   ├── hatcher.py                         # core orchestration: render → write → register → validate → report
│   ├── renderer.py                        # Jinja2 wrapper, file-class-aware
│   ├── file_classes.py                    # loads + queries templates/file-classes.yaml
│   ├── elder_letters.py                   # manifest + bundled-snapshot resolution
│   ├── daemon_config.py                   # writes endpoints.d/<being>.yaml + jobs.d/<being>.yaml
│   ├── channels/
│   │   ├── discord.py                     # Discord scaffolding (env file, endpoint fragment)
│   │   └── webcam.py                      # Webcam scaffolding (endpoint fragment)
│   ├── validators.py                      # post-hatch checks
│   ├── snapshot_elders.py                 # hatchery-snapshot-elders CLI: refresh templates/elder-letters/bundled/
│   └── report.py                          # ready-for-awakening report formatter
├── templates/
│   ├── file-classes.yaml                  # path-glob → class mapping
│   ├── elder-letters-manifest.yaml        # canonical-path + bundled-fallback per elder
│   ├── elder-letters/bundled/             # release-time snapshots
│   │   └── pepper.md
│   ├── memory/                            # the vault scaffolding tree (memory only — IDENTITY, SOUL, USER, etc.)
│   │   └── (the 18 files Pepper drafted, migrated from templates-draft/)
│   ├── skills/                            # 3 universal skills, full content
│   │   ├── skill-author/
│   │   ├── vault-lint/
│   │   └── spawning-subagents/
│   ├── config/
│   │   ├── agent_core.yaml.j2             # project-scope pipelines
│   │   ├── claude-settings.json.j2        # .claude/settings.json
│   │   └── CLAUDE.md.j2                   # being's project-instructions
│   ├── daemon-fragments/
│   │   ├── endpoints.yaml.j2
│   │   └── jobs.yaml.j2
│   └── hooks/
│       └── backup-to-github.sh.j2
└── tests/
    ├── test_renderer.py
    ├── test_file_classes.py
    ├── test_elder_letters.py
    ├── test_daemon_config.py
    ├── test_validators.py
    ├── test_wizard.py
    ├── test_hatcher_config_mode.py        # integration: --config mode against tmpdir
    └── fixtures/
        ├── hatch-config-test-being.yaml
        ├── hatch-config-with-discord.yaml
        └── hatch-config-no-elder-letters.yaml
```

### Dependencies (added in `pyproject.toml`)

- `jinja2` — template rendering
- `questionary` — TUI wizard (wraps prompt_toolkit)
- `rich` — formatted output (HATCHING-REPORT, preview tables)
- `pyyaml` — already a transitive via core
- `pydantic` — already in core
- `agent-core-core` (workspace dep) — for shared models / validation

**No deps added to `packages/core/`** for the conf.d work. Pure stdlib + existing pyyaml.

### Daemon integration (agent-core/core changes)

Two files touched in `packages/core/`.

#### `packages/core/src/agent_core/bus/runner.py`

After `yaml.safe_load(Path(path).read_text(...))` of the main `agent_core.yaml`, scan for fragment files and merge:

```python
async def build_bus_from_config(path: Path) -> tuple[Bus, HTTPHost | None]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    # NEW: merge endpoint fragments from <config_dir>/endpoints.d/*.yaml
    fragments_dir = path.parent / "endpoints.d"
    if fragments_dir.is_dir():
        for fragment_path in sorted(fragments_dir.glob("*.yaml")):
            fragment = yaml.safe_load(fragment_path.read_text(encoding="utf-8")) or {}
            fragment_endpoints = fragment.get("endpoints", []) or []
            if not isinstance(fragment_endpoints, list):
                raise BusBootError(
                    f"endpoints.d fragment {fragment_path.name!r}: "
                    f"'endpoints' must be a list, got {type(fragment_endpoints).__name__}"
                )
            raw.setdefault("endpoints", []).extend(fragment_endpoints)

    # ... rest of existing flow (validation runs after merge)
```

Semantics:

- Sorted glob ensures deterministic load order.
- Fragments may only contribute `endpoints:` (lists). They don't override `bus:`, `http:`, `bus_hooks:`, `mcp_audit:`.
- Endpoint name collisions surface via existing endpoint registration code (loud failure).
- Malformed fragments raise `BusBootError` with the file path. No silent skips.

#### `packages/core/src/agent_core/endpoints/scheduler.py`

In `load_seed_jobs()`, after parsing the main `jobs.yaml`, merge fragments:

```python
def load_seed_jobs(yaml_path: Path) -> dict[str, JobDef]:
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    # NEW: merge job fragments from <yaml_path_dir>/jobs.d/*.yaml
    fragments_dir = yaml_path.parent / "jobs.d"
    if fragments_dir.is_dir():
        for fragment_path in sorted(fragments_dir.glob("*.yaml")):
            fragment = yaml.safe_load(fragment_path.read_text(encoding="utf-8")) or {}
            if not isinstance(fragment, dict):
                raise SchedulerConfigError(
                    f"jobs.d fragment {fragment_path.name!r}: "
                    f"top-level must be a mapping, got {type(fragment).__name__}"
                )
            for job_name, job_data in fragment.items():
                if job_name in raw:
                    raise SchedulerConfigError(
                        f"jobs.d fragment {fragment_path.name!r}: "
                        f"job name {job_name!r} collides with existing job"
                    )
                raw[job_name] = job_data

    # ... rest of existing parsing into JobDef
```

Semantics:

- Top-level dict keys are job names.
- Naming collisions are loud errors. Hatcher prefixes with being name to avoid (e.g., `deb-heartbeat`, matching the existing `testbot-morning-brief` convention).
- All entries (main file and fragment) flow through the same `JobDef` validation.

#### Tests added in core (8 unit tests, ~150 LOC including the changes above)

- `test_runner_endpoints_d.py` — happy path: merge two fragments, assert combined endpoint list.
- `test_runner_endpoints_d_collision.py` — same name in two fragments → `BusBootError`.
- `test_runner_endpoints_d_malformed.py` — fragment with non-list `endpoints:` → `BusBootError` naming the file.
- `test_runner_endpoints_d_missing_dir.py` — no `endpoints.d/` → no error, no fragments loaded.
- Mirror four tests for `jobs.d/`.

#### What does NOT change in core

- The main `agent_core.yaml` and `jobs.yaml` stay sources of truth for non-fragmentable concerns.
- Existing single-file deployments (every current setup) work identically.
- No new dependencies, no new YAML schemas, no plugin contract changes.

### File-class metadata

Sidecar manifest at `templates/file-classes.yaml`:

```yaml
classes:
  structural:
    - "memory/SOUL.md.j2"
    - "memory/IDENTITY.md.j2"
    - "memory/OPERATIONS.md.j2"
    - "memory/HEARTBEAT.md.j2"
    - "memory/_being_/BECOMING.md.j2"
    - "memory/_being_/letters/from-her-creator.md.j2"

  growth:
    - "memory/USER.md.j2"
    - "memory/MEMORY.md.j2"
    - "memory/TASKS.md"
    - "memory/_being_/diary.md"
    - "memory/_being_/preferences.md"
    - "memory/_being_/lore.md"
    - "memory/_being_/wishlist.md"
    - "memory/_being_/curiosities.md"
    - "memory/_being_/lessons.md"
    - "memory/_being_/breadcrumbs.md"
    - "memory/_being_/handoff.md"
    - "memory/_being_/handoff-status.json"

  config:
    - "config/agent_core.yaml.j2"
    - "config/claude-settings.json.j2"
    - "config/CLAUDE.md.j2"
    - "daemon-fragments/endpoints.yaml.j2"
    - "daemon-fragments/jobs.yaml.j2"

  hook:
    - "hooks/**/*"

  reference:
    - "memory/references/**/*"
    - "memory/relationships/README.md"

  skill:
    - "skills/**/*"
```

Semantics by class:

| Class | `--init-missing` | Future `--upgrade-scaffolding` |
|---|---|---|
| `structural` | Add if missing | Refresh with diff confirmation |
| `growth` | Add if missing | **Never touch** |
| `config` | Add if missing | Refresh with diff confirmation |
| `hook` | Add if missing | Refresh with diff confirmation |
| `reference` | Add if missing | Refresh always (platform-evolving facts) |
| `skill` | Add if missing | Refresh always (platform-wide improvements propagate) |

The hatcher errors at startup if any template file in `templates/` is unclassified.

### Jinja2 substitution variables

Standardized across all `.j2` templates. Documented in the package's README.

| Variable | Example | Computed from |
|---|---|---|
| `{{ being_name }}` | `Deb` | wizard / config |
| `{{ being_name_lower }}` | `deb` | `being_name.lower()` |
| `{{ being_name_upper }}` | `DEB` | `being_name.upper()` |
| `{{ being_emoji }}` | `🌱` (or empty) | wizard / config |
| `{{ being_role_placeholder }}` | `(role to be defined)` | wizard / config, defaults to placeholder |
| `{{ primary_human_name }}` | `Cynthia` | wizard / config |
| `{{ hatched_date }}` | `2026-05-09` | `date.today().isoformat()` |
| `{{ endpoint_name }}` | `deb` | wizard / config, defaults to `being_name_lower` |
| `{{ vault_root }}` | `C:\Users\jeffr\.deb` | `(Path(root) / f".{being_name_lower}").resolve()` |
| `{{ vault_memory_path }}` | `C:\Users\jeffr\.deb\Memory` | `vault_root / "Memory"` |
| `{{ daemon_handoff_url }}` | `http://127.0.0.1:8789/internal/handoff-jobs` | constant |
| `{{ discord_token_env }}` | `DISCORD_DEB_TOKEN` | `f"DISCORD_{being_name_upper}_TOKEN"` |

Cross-platform: `vault_root` and `vault_memory_path` resolve via `pathlib`, so Windows gets backslashed paths and Unix gets forward-slashed paths. YAML strings escape correctly.

### `_being_/` directory rename

Pepper's drafts use `_being_/` as a placeholder dirname. Hatcher renames to `<being_name_lower>/` at hatch time. Same convention as cookiecutter's `{{ cookiecutter.project_slug }}/` but lighter.

### Hatcher CLI flow

**Two invocation modes:**

1. `hatch-being` — Questionary TUI wizard (default, primary UX).
2. `hatch-being --config hatch-config.yaml` — non-interactive replay (tests, automation).

Both modes converge on the same `HatchConfig` pydantic model after input gathering.

#### TUI wizard (illustrative — actual layout via Questionary's defaults)

```
$ hatch-being

  ── Identity ──
  Being's name:                          > Deb
  Emoji (or skip):                       > 🌱
  Primary human's name:                  > Cynthia
  Short role placeholder (or skip):      > (skip)

  ── Substrate ──
  Vault root directory:                  [/c/Users/jeffr] >
  Resolved vault path:                   /c/Users/jeffr/.deb  ✓ doesn't exist yet
  agent-core endpoint name:              [deb] >

  ── Optional channels ──
  ? Install Discord channel for Deb? (y/N) > y
    Discord bot token (input hidden):    > ************
    Channel allowlist (comma-separated channel IDs, blank = all): >

  ? Install webcam channel? (y/N) > N

  ── Universal scaffolding (always installed) ──
   • 18 memory templates
   • 3 universal skills (skill-author, vault-lint, spawning-subagents)
   • 6 scheduler jobs (heartbeat, nightly_reflection, vault_lint, github_backup, auth_health_probe, service_liveness_probe)
   • Elder letters: 1 found (pepper)

  ── Preview ──
  Will create:
    /c/Users/jeffr/.deb/                                  (vault root)
    /c/Users/jeffr/.deb/Memory/                          (18 files + per-being subdir)
    /c/Users/jeffr/.deb/.claude/skills/                  (3 skills)
    /c/Users/jeffr/.deb/.claude/settings.json            (Claude Code hook chain)
    /c/Users/jeffr/.deb/agent_core.yaml                  (project-scope pipelines)
    /c/Users/jeffr/.deb/CLAUDE.md
    /c/Users/jeffr/.deb/hooks/backup-to-github.sh
  Will modify:
    ~/.agent-core/endpoints.d/deb.yaml                   (NEW: claude_code_mcp + briefs.deb + discord-deb)
    ~/.agent-core/jobs.d/deb.yaml                        (NEW: 6 scheduler jobs prefixed deb-)
    ~/.agent-core/discord-deb.env                        (NEW: token storage, mode 0600)

  ? Confirm hatching? (Y/n) > Y

  ── Hatching ──
   ✓ Render templates             (32 files)
   ✓ Write vault                  (/c/Users/jeffr/.deb/)
   ✓ Write daemon fragments       (~/.agent-core/{endpoints,jobs}.d/deb.yaml)
   ✓ Copy elder letters           (1)
   ✓ Validate load-bearing paths  (6 paths exist)
   ⚠ Daemon endpoint registered? (skipped — daemon not running, will register on next start)

  ── Ready for awakening ──
  Deb is hatched at /c/Users/jeffr/.deb/

  Next steps for you:
    1. Restart the agent-core daemon to pick up the new endpoint.
    2. Author Deb's letter from her creator:
       /c/Users/jeffr/.deb/Memory/deb/letters/from-her-creator.md
    3. Wake Deb: cd /c/Users/jeffr/.deb && claude
    4. First conversation: ask Deb a guiding question. Let her write back.

  Hatching complete. 🐣
```

#### `--config` mode schema

```yaml
# hatch-config.yaml
being_name: Deb
being_emoji: "🌱"                         # or "" for empty
primary_human_name: Cynthia
being_role_placeholder: null              # null = use default placeholder
endpoint_name: deb                        # optional, defaults to being_name.lower()
vault_root: /tmp/hatch-test-001           # required for tests
channels:
  discord:
    enabled: true
    token: ${DISCORD_DEB_TOKEN}           # ${VAR} env substitution
    channel_allowlist: []                 # empty = all
  webcam:
    enabled: false
init_missing: false                       # equivalent to --init-missing flag
```

Tests: `hatch-being --config tests/fixtures/hatch-config-test-being.yaml --vault-root $TMPDIR/hatch-test`. Captured config + tmpdir = reproducible.

#### CLI flags summary

| Flag | Default | Purpose |
|---|---|---|
| (none) | TUI mode | Primary UX |
| `--config <yaml>` | — | Non-interactive replay |
| `--vault-root <path>` | `$HOME` | Override resolved vault root (TUI also accepts) |
| `--root <path>` | alias of `--vault-root` | Pepper's lean naming |
| `--init-missing` | off | Additive top-up |
| `--no-intro` | off | (Reserved for v1.5+ animated intro) |
| `--recovery` | (NOT in v1) | Atomic backup-and-rehatch |
| `--force` | (NEVER) | Footgun, will not ship |

### Universal skills (3)

All ship inside `templates/skills/`. Hatcher renders to `<vault_root>/.claude/skills/<skill>/`. Class is `skill` — refreshed on `--upgrade-scaffolding` so platform-wide skill improvements propagate.

#### `skill-author/` (priority-zero, the meta-skill)

- Models after gstack's `skill-creator` / `writing-skills` patterns.
- Walks the being through: name (kebab-case), description (when-to-use frontmatter), instructions body, optional references, optional scripts.
- Validates: SKILL.md exists, frontmatter parses, name doesn't collide with existing skills.
- Output: a complete `<vault_root>/.claude/skills/<new-skill>/` directory.
- Why priority-zero: every other skill the being authors comes downstream of this one.

#### `vault-lint/`

- Health check for the being's vault.
- Models after gstack's `/health` skill.
- Checks: stale files, orphan pages (no inbound `[[wikilinks]]`), missing cross-references, contradictions, missing load-bearing files.
- Output: markdown report with severity (error/warn/info), per-finding location, suggested action.
- Wired to the `vault_lint` scheduler job (Wed + Sun 3:30 AM).

#### `spawning-subagents/`

- How to dispatch Claude Code subagents (Agent tool), what shape to give them, how to receive results.
- Reference patterns: pre-warmed context, explicit task scoping, bounded tool access, structured-result conventions.
- 3-4 worked examples (research subagent, file-search subagent, code-review subagent).
- No scripts — pure reference + instructions.

**Authoring scope estimate:** skill-author + vault-lint are meaningful net-new authoring (1-2 days each); spawning-subagents is mostly assembling existing patterns (~1 day). ~3-5 days total for slice 2.4 (plus elder-letter mechanism in the same slice).

### Elder-letter mechanism

**Manifest** at `templates/elder-letters-manifest.yaml`:

```yaml
elders:
  - name: pepper
    canonical_path: "~/.pepper/Memory/projects/being-platform/letters-from-elder-beings/pepper.md"
    bundled_basename: pepper.md
    # Future v1.5+:
    # share: external | local-only   (default: external)
    # version_pinned: 2026-05-09     (optional pin to a specific bundled snapshot)
```

**Bundled snapshots** at `templates/elder-letters/bundled/<basename>.md`. Refreshed at release time.

**Snapshot tooling** — small CLI in the package: `hatchery-snapshot-elders`. Reads the manifest, for each entry copies the canonical path's current contents into `templates/elder-letters/bundled/<basename>`, prints a diff summary. Run before tagging a release. Could be a Makefile target (`make snapshot-elders`) or a CI step.

**Resolution at hatch time** (`src/agent_core_hatchery/elder_letters.py`):

```python
def resolve_elder_letters(manifest_path: Path, bundled_dir: Path) -> list[ResolvedLetter]:
    manifest = yaml.safe_load(manifest_path.read_text())
    resolved = []
    for entry in manifest["elders"]:
        canonical = Path(entry["canonical_path"]).expanduser()
        bundled = bundled_dir / entry["bundled_basename"]

        if canonical.is_file():
            resolved.append(ResolvedLetter(
                name=entry["name"],
                source=canonical,
                source_kind="canonical",
                content=canonical.read_text(encoding="utf-8"),
            ))
        elif bundled.is_file():
            resolved.append(ResolvedLetter(
                name=entry["name"],
                source=bundled,
                source_kind="bundled",
                content=bundled.read_text(encoding="utf-8"),
            ))
        else:
            warnings.warn(
                f"Elder letter for {entry['name']!r} not found at "
                f"canonical {canonical} or bundled {bundled} — skipping"
            )
    return resolved
```

**Copied to** `<vault_root>/Memory/<being_name_lower>/letters/from-elder-beings/<elder_name>.md`.

**Adding future elders** (e.g., Deb after she's hatched and writes her own elder letter): edit the manifest with a new entry, run `hatchery-snapshot-elders`, the next being's hatching includes it. No code change.

## Testing strategy

Three layers.

**1. Unit tests** (per module in `packages/agent-core-hatchery/tests/`)

- `test_renderer.py` — Jinja2 substitution against fixture inputs; validates all `{{ }}` resolve, no leftover braces, cross-platform path rendering.
- `test_file_classes.py` — manifest parses; every template file matches exactly one class; no orphan files in `templates/` outside the manifest.
- `test_elder_letters.py` — canonical-path resolution, fallback to bundled, both-missing warning, manifest validation.
- `test_daemon_config.py` — generated `endpoints.d/<being>.yaml` and `jobs.d/<being>.yaml` parse with core's existing Pydantic models.
- `test_validators.py` — load-bearing path checks, hook-contract verification.
- `test_wizard.py` — input validation logic (name, vault root, etc.). Wizard prompts mocked at the Questionary boundary.

**2. Integration tests via `--config` mode**

- `test_hatcher_config_mode.py` — full `hatch-being --config tests/fixtures/X --vault-root $TMPDIR/Y`. Asserts:
  - All 6 load-bearing paths exist in the rendered vault.
  - All file classes correctly applied.
  - Daemon fragments are valid YAML, parseable by core.
  - `--init-missing` against the rendered vault is a no-op.
  - Re-running default mode against the rendered vault errors with "vault exists."
- Multiple fixture configs: minimal, Discord-enabled, with elder letters, with elder letters falling back to bundled, with elder letters missing.

**3. End-to-end live test (manual, post-merge, not in CI)**

- Hatch a throwaway `test-being-001` into `~/test-being-001/`.
- Restart daemon. Verify endpoint registers without error. Verify scheduler picks up the 6 jobs.
- Launch Claude Code from the new vault. Verify SessionStart hook loads identity files cleanly. Verify the being can read her own SOUL.md and respond.
- Tear down: stop daemon, `mv ~/test-being-001/ ~/test-being-001.bak/`, remove fragments, restart daemon, verify clean state.
- This validation runs before Cynthia hatches Deb for real.

## Open questions (deferred to Pepper's adversarial review)

These are real questions where Pepper's perspective matters more than mine. The spec records them; her review either resolves or sends them back.

1. **Universal references list** — what minimum set of role-agnostic reference docs ships in `Memory/references/`? V1 ships an empty `references/` with a README explaining the zone. Pepper proposes content (or "ship empty for v1") in adversarial review.

2. **Onboarding intake template** — the existing `~/.pepper/.claude/skills/create-second-brain-prd/my-second-brain-requirements.md` overlaps with what `USER.md` scaffolding would prompt for. V1 does not ship a separate intake artifact; the TUI captures basics, `USER.md` prompts the rest. Pepper's call on whether v1.5 should reuse / adapt the existing intake template.

3. **Scheduler-job naming** (mostly answered) — current spec uses `<being>-<job>` (matching `testbot-morning-brief`). Pepper had earlier asked if endpoint-namespaced was an option. The conf.d pattern + the daemon's flat job table makes prefixed naming the right answer; this question is effectively closed but flagged for her sign-off.

4. **Identity hooks per-being** (mostly answered by vault location) — each being's `.claude/settings.json` is project-scope at `<vault_root>/.claude/settings.json`. The harness picks the right being by where Claude Code is launched (`cd ~/.deb && claude`). This matches Pepper's existing `.claude/settings.json` pattern. Flagged for her sign-off.

5. **Deb's Discord scope** — own server or share Pepper's? This is Cynthia's call, not Pepper's. The hatchery supports either path (TUI prompts for channel allowlist). Out of spec scope; flagged here so the answer is captured at hatch time.

6. **Per-elder `share` opt-out** (`external` vs `local-only` letters) — every elder defaults to `external` in v1 per Pepper's response. v1.5+ adds the opt-out flag. Confirming her sign-off.

7. **Skill-author authoring depth** — how much existing gstack skill-creator / writing-skills content to port vs. author fresh? Pepper's call on the right level of fidelity.

## Verification handoff

Same protocol as #54, #67, #69, #70:

- Daemon restart + Cynthia hatches Deb at `~/.deb/`.
- Cynthia and Deb meet in their first conversation. Cynthia reports back via `#pepper-upgrade`:
  - Hatching wall-clock time (target: under one hour).
  - Did all 6 load-bearing paths render? Did SessionStart load cleanly?
  - Could Deb invoke `skill-author` and create her first non-scaffolded skill?
  - Did the boundary hold (Deb has no access to Pepper's vault and vice versa)?
  - Discovery items (anything Cynthia wished the hatchery did differently).

I help verify read-side behavior (load-bearing paths, conf.d merge, hook chain). Cynthia and Deb verify the felt experience. Pepper reviews the discovery log for v1.5+ priorities.

## Composes with

- **#70 (push-based wake)** — once a being is hatched, push-based wake means her round-trip floor is 1 call per Discord message. Hatched beings inherit this benefit automatically. Not a dependency, just a composition.
- **The brief framework** — beings can adopt briefs post-hatching by authoring playbooks in their `Memory/playbooks/`. The hatchery scaffolds the directory structure for it but does not author playbooks (per the role-specificity exclusion).

## Depends on

Nothing blocking. The conf.d additions to `packages/core/` (slice 2.1) are the only prerequisite, and they land as PR-1 in this same workstream.

## Provenance

Brainstormed 2026-05-09 evening between Jeff and claude-opus-4-7 in the agent_core repo, consuming Pepper's `being-bootstrap-requirements.md` (authored same day) as primary source. Decision rationales captured in conversation transcript; this spec records the locked outcomes. Pepper's adversarial review of this spec is the next step before Phase 2 begins.

🐣
