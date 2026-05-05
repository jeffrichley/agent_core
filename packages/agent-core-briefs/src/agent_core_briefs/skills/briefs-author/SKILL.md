---
name: briefs-author
description: Use when authoring or editing a brief-framework playbook (YAML-in-MD) or its gather config (YAML) for the agent_core-briefs package — covers playbook block shapes, conditional sections, simpleeval expression sandbox, ${var} vs {{var}} substitution timing, gather config fetcher entries, and the invariants the parser does NOT validate at parse time
---

# Briefs Author

## Overview

A **brief** is a structured-composition pattern: cron fires a `BriefRequest` event → the orchestrator runs a gather pipeline → an agent (Pepper) composes the output via 7 MCP tools → atomic submit fans out to destinations + audit log. To add a new brief type, you author **two files** and drop them in the orchestrator's configured paths:

1. **Playbook** at `<playbooks_path>/<brief_type>.md` — Markdown narrative with fenced ```yaml blocks the parser classifies by top-level keys.
2. **Gather config** at the path the playbook's `gather_config:` field names — plain YAML listing the fetchers to run.

The framework reloads on each gather; save and you're live. No deploy step.

## When to use

- Adding a new brief type (`evening_check`, `weekly_recap`, `meeting_prep`, etc.).
- Editing an existing playbook's sections, gating, or destinations.
- Adding a fetcher to a gather config or changing per-fetcher timeouts.
- Diagnosing "my brief returned `_errors.X`" failures from the audit log.

**Do NOT use this skill for:** authoring fetcher Python modules (separate concern — fetcher classes implement `Fetcher` protocol with `type_id`, `namespace`, async `fetch(config, when)`); cross-endpoint MCP wiring (already done in agent_core wiring); destination authoring (separate plugin shape).

## Critical invariants (the parser will NOT save you here)

These are the gotchas that bite during authoring. The parser validates *structure*; nothing validates that your expressions or namespaces actually resolve at runtime.

### 1. The `now` namespace is opt-in — register the built-in fetcher

`when.expr: "now.is_friday"` looks reasonable. **The framework does not auto-inject `now`.** Conditional expressions evaluate against the **gathered context only** — the dict produced by `gather_context`, where each fetcher's payload lands at its declared `namespace`. If you write `now.is_friday`, you must register the built-in `now` fetcher in your gather config to produce that namespace.

The framework ships a `now` built-in (alongside `cli` and `filesystem_read`). Register it like any other fetcher:

```yaml
fetchers:
  - type: now
    namespace: now
    timeout_seconds: 5
    config:
      timezone: America/New_York   # optional; defaults to UTC
      weekly_digest_day: Monday    # optional; defaults to Monday
```

It produces these fields: `date`, `iso_datetime`, `day_of_week`, `is_monday`...`is_sunday`, `is_friday`, `is_weekend`, `is_weekly_digest_day`, `iso_week`, `hour`, `minute`. The shipped `docs/examples/playbooks/morning-gather.yaml` includes a `now` entry; copy it verbatim if you don't need the optional config knobs.

### 2. `${var}` ≠ `{{var}}` — different mechanisms, different timing

| Syntax | Fires when | From where | Where allowed |
|---|---|---|---|
| `${agent_root}` | parse time | orchestrator's `vars: {agent_root: ...}` config | playbook + gather YAML |
| `{{when.date}}` | delivery time | the `when` arg of the in-flight session | destination paths/text only |

Wrong syntax, wrong place, wrong value. The parser raises `ConfigSubstitutionError` for missing `${var}` keys; `{{...}}` outside destinations is ignored.

### 3. simpleeval is the expression sandbox — narrow whitelist

Conditional `when.expr` and dynamic-color `expr` evaluate via `simpleeval.EvalWithCompoundTypes`. Available:
- Attribute and bracket access: `projects.active`, `email['urgent']`, `email.urgent[0]`
- Comparisons: `==`, `!=`, `<`, `>`, `<=`, `>=`, `in`, `not in`
- Boolean: `and`, `or`, `not`
- Functions whitelisted: **`len`, `any`, `all`** — that's it.
- Comprehensions allowed.

**Not available:** `bool()`, `min()`, `max()`, `sum()`, `int()`, list slicing assignments, attribute assignment, imports, lambdas, `__dunder__` access.

If your gating logic needs more, do the computation in the fetcher and surface a pre-computed boolean field (e.g., gather adds `now.is_friday`; expression is just `now.is_friday`).

### 4. Fetcher `type:` is `type_id` — not the module/file name

In gather config, `type: filesystem_read` matches the **`type_id` class attribute** of the registered fetcher class, not its filename or class name. Fetchers are filesystem-discovered from the orchestrator's `fetcher_paths`; the loader registers each class under its declared `type_id`. Built-ins ship with these:

| `type_id` | Class | Purpose |
|---|---|---|
| `filesystem_read` | `FilesystemReadFetcher` | Read a file → dict (formats: text, json, yaml, lines) |
| `cli` | `CliFetcher` | Run a subprocess → dict (parse: text, json, yaml, lines) |
| `now` | `NowFetcher` | Calendar/clock facts (date, day_of_week, is_friday, is_weekly_digest_day, etc.) for the gather window's `when` time |

The built-in fetchers' source lives at `packages/agent-core-briefs/src/agent_core_briefs/fetchers/`. They're auto-loaded — when you pass `fetcher_paths=` to the orchestrator, the package's own fetchers directory is prepended automatically. You don't have to copy source into your operator-supplied paths.

All three built-ins have `namespace = ""` at the class level and require `namespace: <name>` in the gather config entry to land their payload somewhere meaningful.

#### `cli` fetcher: env semantics matter (silent gotcha)

The `cli` fetcher accepts these config keys: `command` (list of argv strings — NOT a single shell-style string), `cwd` (with `~/` expansion), `parse` (text/json/yaml/lines), and `env_passthrough` (list of env var names to forward from the parent process).

**There is NO `env:` config key.** Writing `env: { MY_VAR: "..." }` is **silently ignored** — the subprocess will fail with `KeyError: 'MY_VAR'` at runtime. To pass data into the subprocess, use one of:
- **Command-line args:** `command: ["python", "-c", "import sys; print(sys.argv[1])", "value-here"]`
- **A file path the parent writes first:** ship the file via `${agent_root}/...` substitution and have the script read it.
- **Inline literal in the script:** for short data, just embed it in the `-c` script body.

Stdin is closed; the subprocess cannot read piped input. Stderr is captured but only surfaced on non-zero exit. JSON and YAML parse modes both require a dict root — list/scalar roots raise `ValueError`.

### 5. Default fetcher timeout is 300s, not 30s

Omitting `timeout_seconds:` in a gather entry means **5 minutes**, not "fail fast." Set it explicitly for any fetcher whose runaway should fail visibly (e.g., 10s for filesystem reads, 30s for CLI calls).

### 6. Conditional sections need `required_when_active: true` to be enforced

`required: true` does NOTHING on a conditional section spec — the field is keyed on `when.expr`. If you want submitted fields validated when the section fires, set `required_when_active: true`. Otherwise the section is optional even when active.

## Quick reference — playbook block classification

The parser classifies each ```yaml block by its top-level keys:

| Top-level keys present | Block type |
|---|---|
| `brief_type`, `voice`, optional `schedule`, `gather_config` | **metadata** (exactly one per playbook) |
| `destinations: [...]` | destinations |
| `colors: {...}` | color palette |
| `section_id`, `title`, `color`, optional `required`, `fields` | **section** (one block per section) |
| `section_id`, `title`, `color`, **`when:`**, optional `required_when_active`, `fields` | **conditional section** |

Anything else (typo in a key, forgotten classification): `PlaybookParseError: unrecognized YAML block`.

## Playbook skeleton (annotated)

````markdown
# Lunch Check Playbook

## Metadata
```yaml
brief_type: lunch_check          # required; matches the BriefRequest data.brief_type
voice: pepper                    # required; renderer + agent prompt use this
schedule:
  cron: "0 12 * * *"             # informational; the scheduler endpoint reads cron from yaml separately
gather_config: ${agent_root}/Memory/gather/lunch.yaml   # ${var} resolved at parse time
```

## Destinations
```yaml
destinations:
  - type: discord_embed                              # built-in
    config:
      channel_id: "123456789"                        # required for discord_embed
  - type: markdown_file                              # built-in
    config:
      path: ${agent_root}/Memory/daily/briefs/{{when.date}}-lunch.md
      # ${agent_root}: parse-time. {{when.date}}: delivery-time.
```

## Colors
```yaml
colors:                          # palette names → int decimals
  CALM_BLUE: 3447003
  ATTENTION_AMBER: 15844367
  WEEKEND_GREEN: 5763719
```

## Sections

### morning_summary
```yaml
section_id: morning_summary
title: "☀️ Morning summary"
color: CALM_BLUE                 # palette name OR int decimal
required: true
fields:
  - name: "Highlights"
    required: true
    max_chars: 800
    guidance: "Top 3-5 things that happened in DMs this morning."
```

## Conditional sections

### weekend_check
```yaml
section_id: weekend_check
title: "🗓️ Weekend prep"
color: WEEKEND_GREEN
when:
  expr: "now.is_friday"          # SEE INVARIANT #1 — needs a `now` fetcher
required_when_active: true       # SEE INVARIANT #6
fields:
  - name: "Weekend prep"
    required: true
    max_chars: 400
    guidance: "Anything Jeff needs to handle before the weekend."
```
````

## Gather config skeleton (annotated)

```yaml
# Top-level shape: a single `fetchers:` list. No other top-level keys read.
fetchers:
  - type: filesystem_read        # type_id of a registered fetcher class (NOT module name)
    namespace: tasks             # where this fetcher's payload lands in the context dict
    timeout_seconds: 10          # SEE INVARIANT #5 — defaults to 300 if omitted
    config:                      # passed verbatim to fetcher.fetch(config, when)
      path: "${agent_root}/Memory/tasks/open.yaml"
      format: yaml

  - type: now                    # built-in calendar/clock fetcher (see Invariant #1)
    namespace: now
    timeout_seconds: 5
    config:
      timezone: America/New_York # optional; defaults to UTC
      weekly_digest_day: Monday  # optional; sets is_weekly_digest_day. Defaults to Monday.
```

## Common mistakes

| Mistake | Why it fails | Fix |
|---|---|---|
| `when.expr: "now.is_friday"` with no `now` fetcher | `_AttrDict` raises `AttributeError` → `PlaybookParseError: expression failed` at runtime | Register the built-in `now` fetcher in your gather config (see Invariant #1 for the snippet) — it produces `is_friday` and the rest of the day-of-week / week-anchor fields out of the box |
| Using `bool(x)` or `int(x)` in an expression | simpleeval whitelist excludes them | Pre-compute the boolean in the fetcher; expression becomes `<namespace>.<field>` |
| `path: "${agent_root}/.../{{when.date}}-x.md"` typed as `${when.date}` | `${...}` is parse-time and `when` is unknown then; fails with `ConfigSubstitutionError` | Use `{{when.date}}` for delivery-time substitutions in destinations |
| Conditional section with `required: true` (no `required_when_active`) | The framework keys conditional sections off `when` only; `required` is ignored | Use `required_when_active: true` |
| `type: filesystem_read.py` or `type: FilesystemReadFetcher` | `type:` matches `type_id` class attribute, not filename or class name | Use the declared `type_id` (e.g., `filesystem_read`, `cli`) |
| Adding `env: {MY_VAR: "..."}` to a `cli` fetcher entry | `cli` only supports `env_passthrough` (forward from parent env). The `env:` map is silently ignored; subprocess fails with `KeyError` | Pass data via command-line args, a file path, or inline in the `-c` script body. See Invariant #4 for details |
| Omitting `namespace:` for a built-in fetcher | Built-ins ship with `namespace = ""`; payload would land at `context[""]` | Always set `namespace:` in gather entries |
| Setting `timeout_seconds: 30` because "30s sounds fast enough" | Default is 300; explicit 30 is fine BUT some fetchers (e.g., calendar APIs) legitimately need longer | Match timeout to fetcher behavior; document in the entry comment |
| Forgetting `env_passthrough: [PATH, PATHEXT, SYSTEMROOT, ...]` for `cli` fetchers on Windows | Subprocess gets a clean env and can't find executables | Always passthrough `PATH` + `PATHEXT` on Windows |
| Omitting `gather_config:` from metadata | Empty context is legal — but every conditional/dynamic-color expression that references a fetcher namespace will fail | If you have ANY conditionals or dynamic colors, you need a gather config |

## Validating before deploying

Two cheap checks before saving to the live `<agent_root>/Memory/playbooks/`:

```powershell
# 1. Parse-only check (does the structure validate?)
uv run python -c "from pathlib import Path; from agent_core_briefs.playbook import parse_playbook; pb = parse_playbook(Path('docs/examples/playbooks/morning-brief.md'), vars_map={'agent_root': 'C:/test'}); print('voice=', pb.voice, 'sections=', len(pb.sections), 'conditional=', len(pb.conditional_sections), 'destinations=', len(pb.destinations))"

# 2. Run a single fetcher in isolation (does it return what you expect?)
uv run agent-core briefs fetchers test --type cli --config <gather-entry-as-yaml> --fetcher-path <fetcher-dir> --namespace now
```

Step 1 catches structural errors (bad block keys, missing `brief_type`, etc.). Step 2 catches fetcher-config errors (wrong CLI command, parse format mismatch, etc.) before the full brief tries to run.

## Real-world impact

Before this skill: authoring a brief required spelunking through 5 files (`tests/fixtures/playbooks/morning-test.md`, `playbook.py`, `orchestrator.py`, `engine.py`, `config.py`) to reverse-engineer the format. Two of the most common time-sinks were (a) discovering at runtime that `now.is_friday` doesn't auto-inject (mitigated post-`494de68` by shipping the built-in `now` fetcher; you still register it in your gather config) and (b) using the wrong substitution syntax in the wrong place.

After this skill: open this doc, copy the skeleton, edit the parts you care about, run the parse-only check, save. ~10 minutes for a fresh brief type instead of ~45.
