# Scheduler architecture (deep)

Read this when the SKILL.md summary isn't enough — when you need to understand WHY the scheduler is shaped the way it is, or when you're working at the daemon level rather than the consumer level.

## The single canonical scheduler

There is exactly one live scheduler in the agent-core ecosystem: the `builtin.scheduler` endpoint registered in the agent-core daemon, persisted to `~/.agent-core/scheduler.db`, read by the running daemon process.

A legacy file `~/.pepper/scheduler.db` exists (archived to `.legacy` as of 2026-05-28) from an earlier architecture where Pepper had her own separate scheduler. No process reads the legacy file now — verified by process-list scan on 2026-05-28. Any documentation that treats it as live is stale.

## How the daemon is wired

The relevant config block in `~/.agent-core/agent_core.yaml`:

```yaml
endpoints:
  - type: builtin.scheduler
    name: scheduler
    description: "Schedules prompts as bus envelopes. Six tools: create_job, update_job, delete_job, list_jobs, pause_job, resume_job."
    params:
      jobs_path: ~/.agent-core/jobs.yaml
      db_path: ~/.agent-core/scheduler.db
```

The daemon process is `agent_core.cli bus run --config ~/.agent-core/agent_core.yaml`. Verify it's running:

```bash
# Windows
wmic process where "name='python.exe'" get processid,commandline | grep "bus run"

# Linux/Mac
ps aux | grep "agent_core.cli bus run"
```

If no process is running, no jobs will fire. That's the first thing to check when something doesn't fire.

## Seed loading: jobs.yaml + jobs.d/

The `jobs_path` config points at a single file (`~/.agent-core/jobs.yaml`), but the seed loader at `agent_core/endpoints/scheduler.py:load_seed_jobs` does a **conf.d-style merge**:

1. Load top-level jobs from `~/.agent-core/jobs.yaml`
2. Then scan `~/.agent-core/jobs.d/` for `*.yaml` fragments (sorted)
3. Each fragment contributes its top-level job-name keys
4. **Naming collisions between fragments (or between `jobs.yaml` and any fragment) are loud errors** — the daemon refuses to start

In practice, today's seeds live under `~/.agent-core/jobs.d/` as per-being fragments:

```
~/.agent-core/
├── jobs.yaml              # shared/cross-being seeds (testbot-morning-brief, etc.)
└── jobs.d/
    ├── wren.yaml          # all wren-* seed jobs
    ├── pepper.yaml        # all pepper-* seed jobs (if present)
    └── ...                # other beings
```

The hatcher (the tooling that bootstraps a new being) creates the appropriate `jobs.d/<being>.yaml` fragment when a being is hatched. New jobs added to a fragment will be picked up on the next daemon restart **only if the job name isn't already in `scheduler.db`** — the seed loader skips existing names.

## DB schema (the parts you'll touch)

`schedules` table — the active job definitions:

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT | Job name (also the unique identifier). Examples: `wren-heartbeat`, `apex_weekly_slots` |
| `task_id` | TEXT | Internal task identifier. Always `agent_core.endpoints.scheduler:_fire` for jobs that publish envelopes |
| `args` | BLOB | Pickled tuple: `(scheduler_endpoint_name, job_name, target_endpoint, prompt_or_None, metadata_dict)` |
| `kwargs` | BLOB | Pickled dict. For TextMessage jobs (the default), `{}`. For Event jobs, `{"envelope_kind": "Event", "payload": {"type": ..., "data": ...}}` |
| `trigger` | BLOB | Pickled APScheduler trigger object (CronTrigger / IntervalTrigger / DateTrigger) |
| `paused` | INTEGER | 0 or 1. **In-memory scheduler state must match — don't UPDATE this column directly** |
| `last_fire_time` | TEXT | Microseconds since epoch + utcoffset (APScheduler v4 internal format) |
| `next_fire_time` | TEXT | Same format as last_fire_time |
| `coalesce` | INTEGER | If 1, missed fires are coalesced into a single fire on resume |
| `misfire_grace_time` | INTEGER | Seconds of grace before a missed fire is dropped |
| `metadata` | BLOB | Pickled dict for job-author-supplied metadata |

`jobs` table — the queue of pending fires (transient; populated by APScheduler as fire times approach).

`job_results` table — recent fire outcomes (success/failure + return value if any).

**Read these tables for inspection.** Never write to them directly — the scheduler endpoint's `update_job`/`pause_job`/etc. operations call APScheduler's own `remove_schedule`/`add_schedule`/`pause_schedule` methods, which handle pickling, in-memory state, and trigger rebuilds correctly. A raw SQL UPDATE bypasses all of that.

## The ToolInvocation contract

The scheduler endpoint exposes its tools via the bus, not as MCP tools mounted to individual sessions. The contract from `agent_core/endpoints/scheduler.py:SchedulerEndpoint.deliver`:

**Inbound envelope:**
- `kind: "ToolInvocation"`
- `to: "scheduler"`
- `payload`: an object with `tool: str` and `args: dict`

**Outbound reply envelope:**
- `kind: "Acknowledgment"`
- `to: <original sender>`
- `correlation_id: <original correlation_id>`
- `in_reply_to: <original envelope id>`
- `payload.of: <original envelope id>`
- `payload.note: <JSON-stringified result on success, or "error: <message>" on failure>`

Any non-ToolInvocation envelope sent to the scheduler gets a "warning: unsupported envelope kind" Acknowledgment and is acked without further action.

The six tools and their argument shapes are defined as Pydantic models in the endpoint source:

- `_CreateJobArgs(name, trigger, schedule, target, prompt, timezone, metadata, envelope_kind, payload)` — all but `name`, `trigger`, `schedule`, `target` are optional; `prompt` is required for TextMessage jobs, `payload` for Event jobs
- `_UpdateJobArgs(name, schedule, target, prompt, timezone, metadata, envelope_kind, payload)` — only `name` is required; every other field is partial-merge
- `_NameOnlyArgs(name)` — for delete_job, pause_job, resume_job
- `_NoArgs()` — for list_jobs

Invalid args produce a `_ToolError` with a descriptive message returned via the Acknowledgment.

## The fire path

1. Scheduler hits `next_fire_time` for a job
2. APScheduler calls `_fire(endpoint_name, job_name, target_endpoint, prompt, metadata)` with the job's pickled args (and the kwargs dict for Event jobs)
3. `_fire` looks up the live endpoint by name in `_active_endpoints` (a module-level dict — live BusHandle isn't picklable)
4. For TextMessage jobs, `_fire` publishes an envelope to `target_endpoint` with `kind="TextMessage"` and `payload=TextMessagePayload(text=prompt)`
5. For Event jobs, it publishes with `kind="Event"` and `payload=EventPayload(type=..., data=...)`
6. Either way, the envelope's `metadata.scheduler_job` field carries the job name
7. The bus delivers the envelope to the target endpoint's inbox
8. The agent-core-channel MCP server notifies the connected Claude Code session
9. The being wakes, consumes the envelope, processes the prompt or Event

## Timezones

Default for `CronTrigger` is UTC unless `timezone` is set explicitly. A bug class observed historically: a job intended to fire at 9 AM ET that doesn't set timezone fires at 9 AM UTC = 4-5 AM ET. Always set `timezone: "US/Eastern"` (or equivalent) in cron trigger config.

`IntervalTrigger` is timezone-agnostic — it fires every N seconds/minutes/hours regardless of clock.

## What can go wrong

- **Daemon down at fire time** + cron trigger without `coalesce: true` + no `misfire_grace_time` → the fire is silently dropped
- **Raw DB UPDATE bypasses APScheduler's in-memory cache** → either silently corrupts pickled args or leaves scheduler in inconsistent state
- **Raw DB UPDATE of `paused` column** → DB says paused, scheduler in-memory state says not paused (or vice versa); job fires or doesn't based on the in-memory state, not the DB
- **Timezone drift** (see above) → fires at wrong wall-clock time
- **Endpoint typo in `target`** → fire publishes but no inbox receives it; envelope sits in dead-letter or vanishes depending on bus config
- **Seed YAML edit after first daemon start** → silently no-op for any job name already in `scheduler.db` (use `update_job` instead, or delete the DB row first then restart)
- **`jobs.d/` fragment collision** → daemon refuses to start with a clear error; rename to fix
