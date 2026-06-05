---
name: scheduler
description: Use when the being is curious about, debugging, or inspecting any scheduled job that fires via the agent-core daemon. Triggers on questions about heartbeats, recurring jobs, cron, why a wake-event arrived, when something next fires, whether something fired, scheduled briefings, liveness probes, missed fires, or requests to add/change/pause/resume a schedule. Also triggers proactively when reading a bus envelope whose metadata contains `scheduler_job:` — explain what's firing rather than ignoring it. Do NOT skip this skill assuming the being already knows the scheduler architecture; documentation about the scheduler in older skills (e.g. `~/.pepper/.claude/skills/scheduler/SKILL.md`) is stale, names tools under namespaces that do not exist, and recommends direct SQLite mutations that can silently corrupt the scheduler store. This skill is the canonical one.
---

# Scheduler

The agent-core daemon runs a single canonical scheduler. It fires recurring or cron-scheduled jobs as bus envelopes targeted at specific being-endpoints (wren, pepper, testbot, briefs.orchestrator, etc.). Each being receives the events in their inbox via the agent-core-channel MCP server. Mutations to jobs are performed by sending `ToolInvocation` envelopes to the scheduler endpoint over the same bus.

This skill is the canonical reference for understanding and working with that scheduler. It replaces older docs (notably `~/.pepper/.claude/skills/scheduler/SKILL.md`) that referenced an MCP server (`mcp__pepper-scheduler__*`) which is not registered in any running configuration AND recommended direct SQLite mutations which are unsafe. Trust the running processes and the agent-core endpoint source over stale docs.

## The four questions this skill answers

Most scheduler questions reduce to one of these. The body below covers each in the order most beings encounter them.

1. **What's scheduled to fire to me?** (inventory)
2. **Did X fire? When does X next fire?** (verification)
3. **How do I add, change, pause, or remove a schedule?** (mutation)
4. **Why didn't X fire when it should have?** (debugging)

## One-line architecture

- Canonical DB: `~/.agent-core/scheduler.db` (SQLite with SQLAlchemyDataStore-backed APScheduler v4 — **read-only from outside the daemon**)
- Live daemon: `agent_core.cli bus run --config ~/.agent-core/agent_core.yaml`
- Seed jobs loaded from BOTH `~/.agent-core/jobs.yaml` AND `~/.agent-core/jobs.d/*.yaml` (conf.d-style merge; collisions are loud errors)
- Mutations go through the scheduler endpoint via `ToolInvocation` envelopes addressed to `to="scheduler"`
- Each fire publishes a bus envelope to the job's target endpoint; envelope's `metadata.scheduler_job` field carries the job name

Full architecture deep-dive: `references/architecture.md`.

## Question 1: What's scheduled to fire to me?

Two paths. The bus path is canonical (works without DB access, returns rich data). The direct-read path is a fallback for when bus access isn't available.

### Canonical: call `list_jobs` via the scheduler endpoint

```python
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={"kind": "ToolInvocation", "tool": "list_jobs", "args": {}}
)
# Then consume the Acknowledgment reply (kind="Acknowledgment") and parse
# payload.note as JSON — it's a list of job dicts with name, trigger,
# target, prompt, envelope_kind, payload, next_run, paused.
mcp__agent-core__consume()
```

The reply lists ALL jobs (not just yours). Filter by `target == "<my-being-name>"` to see only your own.

### Fallback: direct SQLite read (READ-ONLY, never mutate this way)

Replace `<my-being-name>` with the being's actual endpoint name (wren, pepper, testbot, etc.):

```bash
python -c "
import sqlite3, pickle
me = '<my-being-name>'
conn = sqlite3.connect('C:/Users/jeffr/.agent-core/scheduler.db')
for sid, args_b, next_t in conn.execute('SELECT id, args, next_fire_time FROM schedules').fetchall():
    try: args = pickle.loads(args_b) if args_b else None
    except: continue
    if args and len(args) > 2 and args[2] == me:
        print(f'{sid:40s} next: {next_t}')
"
```

For deeper inspection (full prompt, trigger config, last fire time), see `references/inspect.md`.

## Question 2: Did X fire? When does X next fire?

```python
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={"kind": "ToolInvocation", "tool": "list_jobs", "args": {}}
)
# Reply contains `next_run` for every job. For `last_fire_time`, the
# bus tool doesn't return it directly — fall back to the SQLite read
# recipe in references/inspect.md if you need last-fire history.
```

**If the target is another being (not you):** you can read the DB / call `list_jobs` to see *whether* the daemon dispatched, but you can't see whether they *processed* it. To verify another being received and acted, the right move is to ask them via the bus — see `references/coordinate.md` for envelope shapes.

## Question 3: How do I add, change, pause, or remove a schedule?

**Always via the scheduler endpoint over the bus. Never by mutating `scheduler.db` directly.**

### Why direct DB mutation is dangerous

The scheduler uses APScheduler v4 with a SQLAlchemyDataStore. Each schedule row's `args` column is a pickled tuple `(scheduler_name, job_name, target, prompt, metadata)`, and the `kwargs` column carries separate fields (`envelope_kind`, `payload`) for Event jobs. The trigger is a pickled APScheduler trigger object. Pause/resume state has both a DB column AND in-memory scheduler state that must move together — a raw UPDATE of the `paused` column doesn't pause the live scheduler. Hand-mutating any of these risks unpickle errors, schedule-state desynchronization, or silent loss of fires. **Always go through the endpoint, which validates inputs with Pydantic, rebuilds triggers correctly, and uses APScheduler's own `add_schedule`/`remove_schedule`/`pause_schedule`/`unpause_schedule` operations.**

**If the bus is down:** there is no safe fallback. Mutation must wait until the bus is back. The temptation to "just UPDATE the DB while the daemon is down" is the same trap — when the daemon comes back up it reads pickled args from the DB and either crashes on bad pickles or runs with stale in-memory cache that fights your edit. Read-only inspection of `scheduler.db` is always safe (it's just SQLite); mutation is not.

### Three trigger types

Before the tools: every scheduled job uses one of three triggers. Pick by intent, not by familiarity with cron:

- **`interval`** — fires every N seconds/minutes/hours/days. Schedule: `{"seconds": N}` / `{"minutes": N}` / `{"hours": N}` / `{"days": N}`. Use for heartbeats, periodic probes, "every 30 min" patterns. Timezone-agnostic.
- **`cron`** — fires at specific wall-clock times. Schedule: `{"second": ?, "minute": ?, "hour": ?, "day": ?, "month": ?, "day_of_week": ?, "year": ?}` (omit fields = wildcard; `day_of_week` accepts `mon-fri`, `mon`, etc.). Use for "every weekday at 9 AM", "Thursdays at 4 PM", "1st of the month". **Always set `timezone`** (e.g. `"America/New_York"`) — APScheduler v4 defaults to UTC.
- **`date`** — fires ONCE at a specific timestamp then auto-deletes. Schedule: `{"run_time": "<ISO-8601-datetime>"}`. Use for one-shot reminders ("at 8:30 AM tomorrow, do X").

### The six tools

All return either `{"status": "...", "name": "..."}` on success or `"error: <message>"` on failure. The reply arrives as a separate `Acknowledgment` envelope — consume it to read the result.

> **Reply round-trip pattern (same for all six tools):** every send produces an `Acknowledgment` reply from the scheduler. Call `mcp__agent-core__consume()` after each send and look for an envelope `from="scheduler"`, `kind="Acknowledgment"`, with `payload.note` as JSON-stringified result (or `"error: <message>"` prefix on failure). Mutations are transactional — on `error:`, nothing changed; safe to fix args and retry.

#### `create_job` — register a new schedule

```python
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={
        "kind": "ToolInvocation",
        "tool": "create_job",
        "args": {
            "name": "wren-apex-verify",
            "trigger": "cron",                       # "interval" | "cron" | "date"
            "schedule": {"day_of_week": "thu", "hour": 16, "minute": 5},
            "target": "wren",
            "prompt": "Verify pepper's apex_weekly_slots fired at 16:00 ET today.",
            "timezone": "America/New_York",          # required for cron unless you want UTC
            "metadata": {},                          # optional dict
            # envelope_kind defaults to "TextMessage". For Event jobs:
            # "envelope_kind": "Event",
            # "payload": {"type": "BriefRequest", "data": {...}}
        }
    }
)
```

#### `update_job` — change one or more fields on an existing schedule (partial merge)

```python
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={
        "kind": "ToolInvocation",
        "tool": "update_job",
        "args": {
            "name": "apex_weekly_slots",             # required
            "prompt": "<new prompt>"                 # any field is optional; omitted = preserved
        }
    }
)
```

Only `name` is required. Every other field is optional — anything you omit is preserved from the current schedule. The endpoint re-validates the merged shape (e.g. switching to `envelope_kind: "Event"` without a `payload` is rejected). Under the hood the operation is `remove_schedule + add_schedule` with the new args, reusing the existing trigger when `schedule` is omitted. **No daemon restart needed.** The job's id and `last_fire_time` are preserved across updates.

#### `delete_job` / `pause_job` / `resume_job` — name-only operations

```python
# Each follows the same shape:
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={"kind": "ToolInvocation", "tool": "delete_job", "args": {"name": "<job-name>"}}
)
# Replace "delete_job" with "pause_job" or "resume_job" as needed.
```

**Pause vs. delete — pick the right one.** `pause_job` is reversible (the job definition survives; `resume_job` brings it back with `last_fire_time` intact). `delete_job` is destructive of schedule history (the row is gone; recreating loses `last_fire_time` continuity). Pause for temporary disable (vacation, debugging, batch suspension). Delete only when you genuinely want the job gone for good.

#### `list_jobs` — empty args

```python
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={"kind": "ToolInvocation", "tool": "list_jobs", "args": {}}
)
```

### Cross-being mutation requests

If the job you want to change targets another being, you CAN call `update_job` from your session — the scheduler doesn't gate by caller identity. But there's a social-trust dimension: another being's scheduled jobs are part of their operational domain. The right shape is usually to ask first (a one-line envelope), get a quick yes, then apply. See `references/coordinate.md` Pattern 2 for the request envelope shape.

### Seed files only matter on FIRST daemon start

Editing `~/.agent-core/jobs.yaml` or adding a `jobs.d/*.yaml` fragment does NOT change a live schedule — the seed loader skips any job name that already exists in `scheduler.db`. To genuinely re-seed from YAML, delete the relevant DB row first (via `delete_job`), THEN restart the daemon. For most changes, just use `update_job` and skip the YAML entirely.

## Question 4: Why didn't X fire when it should have?

Three common causes, in order of likelihood:

1. **The daemon was down at the scheduled fire time.** Cron triggers without `coalesce: true` skip missed fires; interval triggers without `misfire_grace_time` fire late or skip depending on config. Check `ps`/`wmic` for `agent_core.cli bus run` before concluding the schedule is broken.
2. **The schedule is paused.** Call `list_jobs` and check the `paused` field.
3. **The trigger's timezone doesn't match the wall-clock you expected.** APScheduler v4 defaults to UTC if no timezone is set; jobs intended for ET that drop the timezone fire 4-5 hours off. Concrete example: a job created with `{"trigger": "cron", "schedule": {"hour": 9, "minute": 0}}` (no `timezone` field) fires at 09:00 **UTC** = 05:00 EDT — your `last_fire_time` will show 05:00 in your local clock, not 09:00. Fix by setting `timezone: "America/New_York"` and re-creating the job (or `update_job` with the fixed schedule).

Detection recipes in `references/inspect.md`.

## Question 5: What if the scheduler returns `error: <message>`?

Every mutation tool returns either `{"status": "...", "name": "..."}` on success or a string `"error: <message>"` on failure. Read the reply via `mcp__agent-core__consume()` and check the `payload.note` prefix:

- **Mutations are transactional.** On `error:`, nothing changed in the live store. Safe to fix the args and retry — you won't end up with a partially-updated job.
- **Common errors and how to recover:**
  - `error: job 'X' already exists` (on `create_job`) → either use `update_job` instead, or `delete_job` first if you intend a fresh start (and accept the `last_fire_time` loss).
  - `error: job 'X' not found` (on `update_job`/`delete_job`/`pause_job`/`resume_job`) → check spelling via `list_jobs`; the job may have been renamed or never existed.
  - `error: invalid job definition: <pydantic msg>` → the args failed Pydantic validation. Usually a missing required field (e.g. `prompt` on a TextMessage job) or an envelope-kind/payload mismatch on Event jobs. Read the message; the missing/wrong field is named explicitly.
  - `error: invalid trigger: <message>` → the trigger config is malformed. APScheduler is strict about cron field types; `hour: "9"` (string) vs `hour: 9` (int) can matter, and `timezone: None` is rejected explicitly (omit the field instead).
- **No retry loop required** — these are deterministic validation failures, not transient. Fix the cause once, retry once.

## When this skill triggers from a wake-event

If a bus envelope arrives with `metadata.scheduler_job` set, the envelope is a scheduled fire — not a one-off message. The job name tells you what to do:

- Recognize the job by name (call `list_jobs` if you need to confirm)
- The response behavior for *your* being's jobs is documented in your vault (typically `Memory/HEARTBEAT.md`, `Memory/OPERATIONS.md`, or playbook files referenced by the job's prompt)
- If the envelope is from a job you don't recognize, that's a signal: either a new job you weren't notified about, or a job you should not be receiving (routing bug — call `list_jobs` to confirm the target)

## References

- `references/architecture.md` — DB schema, daemon internals, conf.d seed merge, job lifecycle, the ToolInvocation envelope contract
- `references/inspect.md` — read-only SQLite recipes for inspection + how to read the Acknowledgment reply from bus-route mutations
- `references/coordinate.md` — cross-being patterns for verifying another being's job fired or requesting a schedule change in another being's domain

## Provenance + drift warning

This skill is the canonical multi-being scheduler reference, written 2026-05-28 from Wren's vault and intended to be installed in any being's `.claude/skills/scheduler/` directory.

**Drift warnings — any of these in another `SKILL.md` means that file is stale:**

- References to `mcp__pepper-scheduler__*` tools (no such namespace exists)
- Recommendations to UPDATE `scheduler.db` directly (dangerous — corrupts pickled args)
- Claims that daemon restart is required for `update_job` (it isn't — `update_job` mutates the live store)
- Reference to `~/.pepper/scheduler.db` as the canonical store (it's a dormant legacy file, archived to `.legacy` as of 2026-05-28; canonical is `~/.agent-core/scheduler.db`)
- Treatment of `jobs.yaml` as the only seed source (the daemon merges `jobs.d/*.yaml` fragments too)

If you find a stale file, replace it with this one or ignore it. The canonical source of truth for behavior is the running daemon process + the scheduler endpoint source at `agent_core/endpoints/scheduler.py`.
