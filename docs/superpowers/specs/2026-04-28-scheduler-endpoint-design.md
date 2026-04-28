# SchedulerEndpoint — Design Spec

**Date:** 2026-04-28
**Status:** Approved
**Builds on:** [`2026-04-27-channel-bus-design.md`](2026-04-27-channel-bus-design.md), [`2026-04-28-bus-daemon-design.md`](2026-04-28-bus-daemon-design.md)

## Overview

Sub-project A Step 4 of the agent-core roadmap: port Pepper's APScheduler-based
scheduler into a bus-native `SchedulerEndpoint`. The scheduler runs inside the
already-shipped agent-core daemon, fires scheduled prompts as bus envelopes
(no HTTP POST to a channel server), and supports both static yaml seeds and
dynamic management via `ToolInvocation` envelopes. Pepper's existing scheduler
runtime stays untouched until a future Pepper-migration sub-project.

This spec covers v1 scope: static yaml seeds, dynamic create/update/delete/
list/pause/resume via bus envelopes, prompt-typed jobs only. Function jobs
(importlib-based) and cancellation primitives are out of scope.

## Architectural Shape

The channel-bus spec (line 676-680) already declares `SchedulerEndpoint` as an
expected adapter; this spec fills in the implementation contract. The
endpoint is a standard `Endpoint` Protocol implementer that lives inside the
daemon's process, alongside `ClaudeCodeMCPEndpoint` and any other adapters.
APScheduler's event loop runs on the same asyncio loop as the bus.

### Settled (reference)

- One `SchedulerEndpoint` instance per daemon, registered in
  `agent_core.yaml` under the `endpoints:` list.
- The endpoint is NOT `MCPHostable` — agents do not connect to it directly
  over HTTP. They reach it via the bus (sending envelopes addressed to
  `to=scheduler` from their own ClaudeCodeMCPEndpoint).
- Job firing publishes envelopes via the endpoint's `BusHandle`, which
  auto-stamps `from_=scheduler`.

### Confirmed in this spec

- **Job storage:** APScheduler's `SQLAlchemyDataStore` backed by aiosqlite
  at `~/.agent-core/scheduler.db` (separate from `bus.sqlite`).
  Configurable via the endpoint's `db_path` param.
- **Static seeds:** optional `jobs_path` param. If provided, the file is
  parsed at `start()` and seed jobs are added (skipping duplicates by ID).
  If omitted, the endpoint runs with no seed jobs.
- **Dynamic management:** agents send `ToolInvocation` envelopes to
  `to=scheduler`. Scheduler dispatches by `payload.tool` and replies via
  an `Acknowledgment` envelope back to `envelope.from_`.
- **Job kinds:** prompt-typed only. A scheduled job, when it fires,
  publishes a `TextMessage` envelope to a named target endpoint.

### Out of scope for v1

- **Function jobs** (`type: function` in Pepper's yaml). Pepper's only
  function job pointed at `pepper.backup:backup_vault`, which doesn't exist
  in agent-core. Revisit when sub-project D (native backup) lands.
- **Cancellation envelopes** for in-flight job runs. `delete_job` is the
  only stop primitive in v1.
- **Job-run history / audit trail.** Bus's `correlation_id` tracking and
  `agent-core bus trace` cover most needs.
- **Cron expression strings** — use the dict form (consistent with
  Pepper's port-by-recreation reference).
- **Operator CLI** for job management (e.g., `agent-core scheduler list`).
  Operators use the bus's existing inspection commands; agent-side
  ToolInvocation is the management surface for v1.

## Components

### `SchedulerEndpoint`

**Location:** `packages/core/src/agent_core/endpoints/scheduler.py`

Implements the `Endpoint` Protocol. Constructor signature follows the
runner's convention (every endpoint class accepts `name` as a kwarg):

```python
class SchedulerEndpoint:
    name: str    # set by runner via kwarg per existing convention
    jobs_path: Path | None = None  # optional yaml seed file
    db_path: Path = Path("~/.agent-core/scheduler.db").expanduser()
```

**Lifecycle:**
- `start(bus)`: stores the `BusHandle`, builds an `AsyncScheduler` with
  `SQLAlchemyDataStore(...)` at `db_path`, configures task concurrency
  (`max_running_jobs=None` for the firing coroutines), seeds jobs from
  `jobs_path` if set, calls `scheduler.start_in_background()`.
- `deliver(envelope)`: dispatches `ToolInvocation` envelopes by
  `payload.tool`. Replies with `Acknowledgment`. Auto-acks the incoming
  envelope after handling.
- `stop()`: shuts down APScheduler gracefully.

### Job runner

When APScheduler fires a job, it calls a coroutine on the same loop:

```python
async def _fire(name: str, target: str, prompt: str,
                metadata: dict, correlation_id: str | None = None) -> None:
    """APScheduler job callable. Publishes a TextMessage envelope to target."""
    envelope = Envelope(
        id=uuid.uuid4().hex,
        correlation_id=correlation_id or uuid.uuid4().hex,
        to=target,
        kind="TextMessage",
        payload=TextMessagePayload(text=prompt),
        metadata={"scheduler_job": name, **metadata},
        created_at=datetime.now(timezone.utc),
    )
    await bus_handle.publish(envelope)
```

Bus stamps `from_=scheduler` automatically. The recipient endpoint's
`deliver()` runs per the standard bus contract.

### Tool dispatcher

`deliver()` checks `envelope.kind`. For `ToolInvocation`:

```python
TOOL_HANDLERS: dict[str, callable] = {
    "create_job": handle_create_job,
    "update_job": handle_update_job,
    "delete_job": handle_delete_job,
    "list_jobs":  handle_list_jobs,
    "pause_job":  handle_pause_job,
    "resume_job": handle_resume_job,
}
```

Each handler returns a JSON-serialisable result dict. The endpoint then
publishes an `Acknowledgment` to `to=envelope.from_`:

```python
ack = Envelope(
    id=...,
    correlation_id=envelope.correlation_id,
    in_reply_to=envelope.id,
    to=envelope.from_,
    kind="Acknowledgment",
    payload=AcknowledgmentPayload(of=envelope.id, note=json.dumps(result)),
    created_at=...,
)
await bus_handle.publish(ack)
await bus_handle.ack(envelope.id)
```

For envelope kinds other than `ToolInvocation`, the endpoint auto-acks
the incoming envelope and publishes an Acknowledgment with
`note="warning: unsupported envelope kind '<kind>'"`.

### Static seed loader

```python
def load_seed_jobs(yaml_path: Path) -> dict[str, JobDef]: ...
```

Parses jobs.yaml into a dict keyed by job name. Each job is validated
against the `JobDef` schema (Pydantic). On `start()`, the endpoint
iterates and calls `add_schedule(...)` for each, skipping any whose
name is already present in APScheduler's persisted state.

## Job YAML Schema

```yaml
# ~/.agent-core/jobs.yaml (or wherever jobs_path points)

heartbeat:
  trigger: interval
  schedule: { minutes: 30 }
  target: agent-pepper          # required: endpoint name to deliver to
  prompt: "Heartbeat check..."
  metadata: { job_kind: heartbeat }   # optional, copied to envelope.metadata

morning_brief:
  trigger: cron
  schedule: { hour: 7, minute: 0 }
  timezone: US/Eastern          # optional; default = system local
  target: agent-pepper
  prompt: "Morning briefing..."

one_off:
  trigger: date
  schedule: { run_time: "2026-05-01T09:00:00-04:00" }
  target: agent-pepper
  prompt: "Reminder: ship sub-project D"
```

**Required fields:** `trigger` (`"interval" | "cron" | "date"`), `schedule`
(dict matching the trigger), `target` (registered endpoint name), `prompt`
(string).

**Optional fields:** `timezone` (string; cron triggers only; defaults to
system local), `metadata` (dict; copied verbatim to the envelope's
metadata, with `scheduler_job` automatically merged in).

**Schedule shape per trigger:**
- `interval`: `{seconds?, minutes?, hours?, days?}` — at least one field.
- `cron`: `{second?, minute?, hour?, day?, month?, day_of_week?, year?}`
  — APScheduler's standard cron field semantics. Empty fields are
  treated as wildcards.
- `date`: `{run_time: <ISO 8601 datetime string>}`.

`target` must match a registered bus endpoint at fire time. If the target
isn't registered, `bus.publish()` rejects per the bus's existing endpoint
validation (see channel-bus spec § Security § Structural defaults). The
job stays scheduled and will retry on its next fire window.

## ToolInvocation Contract

Agents manage jobs by sending `ToolInvocation` envelopes addressed to
`to=scheduler`:

```jsonc
// Agent sends (via send tool on its ClaudeCodeMCPEndpoint):
{
  "to": "scheduler",
  "kind": "ToolInvocation",
  "payload": {
    "kind": "ToolInvocation",
    "tool": "create_job",
    "args": {
      "name": "weekly_review",
      "trigger": "cron",
      "schedule": {"day_of_week": "fri", "hour": 17},
      "target": "agent-pepper",
      "prompt": "Weekly review prompt",
      "timezone": "US/Eastern",
      "metadata": {}
    }
  }
}
```

Scheduler replies with an Acknowledgment whose `note` is the JSON-encoded
result dict.

### Tools

| Tool | Args | Result on success |
|---|---|---|
| `create_job` | `name, trigger, schedule, target, prompt, timezone?, metadata?` | `{"status": "created", "name": "..."}` |
| `update_job` | `name, schedule?, target?, prompt?, timezone?, metadata?` | `{"status": "updated", "name": "..."}` |
| `delete_job` | `name` | `{"status": "deleted", "name": "..."}` |
| `list_jobs`  | `()` | `[{"name", "trigger", "schedule", "target", "prompt", "next_run", "paused"}, ...]` |
| `pause_job`  | `name` | `{"status": "paused", "name": "..."}` |
| `resume_job` | `name` | `{"status": "resumed", "name": "..."}` |

Update semantics: missing fields are kept from the existing job. To clear
`metadata`, pass `metadata: {}` explicitly.

### Discovery

Agents discover the scheduler via `list_endpoints()`. The endpoint's
description (operator-authored in `agent_core.yaml`) should mention the
ToolInvocation pattern, e.g.:

```yaml
- class: agent_core.endpoints.scheduler.SchedulerEndpoint
  name: scheduler
  description: |
    Fires scheduled prompts on cron/interval/date triggers.
    Send ToolInvocation envelopes (tool=create_job/update_job/
    delete_job/list_jobs/pause_job/resume_job) to manage jobs.
  params:
    jobs_path: ~/.agent-core/jobs.yaml
```

There is no separate tool-listing API on the bus in v1; agents read the
description prose. (The bus spec's open-question about a richer
capability-discovery surface remains open across all endpoints; not
something this sub-project addresses.)

## Error Handling

- **Unknown tool name** (`payload.tool` not in `TOOL_HANDLERS`):
  Acknowledgment with `note="error: unknown tool '<name>'"`. Auto-ack
  incoming.
- **Bad args** (Pydantic validation fails on the tool's args model):
  Acknowledgment with `note="error: <pydantic message>"`. Auto-ack
  incoming.
- **Job-not-found** (`update_job`, `delete_job`, `pause_job`,
  `resume_job` for a name that isn't registered):
  Acknowledgment with `note="error: job '<name>' not found"`.
- **Duplicate job name** (`create_job` for a name that already exists):
  Acknowledgment with `note="error: job '<name>' already exists"`.
- **Trigger build failure** (e.g., invalid cron field):
  Acknowledgment with `note="error: invalid trigger: <message>"`.
- **APScheduler exception during fire** (publish fails because target
  endpoint is unregistered, or any other reason):
  Logged. The job stays scheduled; APScheduler's misfire policy
  determines whether/when it retries. The bus's dead-letter handles any
  envelopes that did get persisted but then failed to deliver.
- **Unsupported envelope kind** to scheduler:
  Acknowledgment with `note="warning: unsupported envelope kind
  '<kind>'"`. Auto-ack incoming.

## Configuration

```yaml
# ~/.agent-core/agent_core.yaml

bus:
  storage_path: ~/.agent-core/bus.sqlite

http:
  bind_host: 127.0.0.1
  bind_port: 8788

endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-testbot
    description: "Test agent."
    params:
      mount: /mcp/agent-testbot

  - class: agent_core.endpoints.scheduler.SchedulerEndpoint
    name: scheduler
    description: |
      Fires scheduled prompts on cron/interval/date triggers.
      Send ToolInvocation envelopes (create_job, update_job, delete_job,
      list_jobs, pause_job, resume_job) to manage jobs.
    params:
      jobs_path: ~/.agent-core/jobs.yaml
      db_path: ~/.agent-core/scheduler.db
```

Both `jobs_path` and `db_path` are optional. `db_path` defaults to
`~/.agent-core/scheduler.db`. `jobs_path` has no default — omit to run
with no seed jobs.

## Testing

### Unit
- `Endpoint` Protocol conformance.
- `JobDef` Pydantic validation: required fields, trigger/schedule pair
  validation, `target` is a non-empty string.
- `build_trigger`: each of interval/cron/date produces the right
  APScheduler trigger object.
- `load_seed_jobs`: parses valid yaml; skips empty file; raises on
  malformed entries.
- Tool dispatch (each of 6 tools) with mocked APScheduler — verify
  Acknowledgment contents.
- Unknown tool / bad args / job-not-found / duplicate name → correct
  error Acknowledgment.
- Unsupported envelope kind → warning Acknowledgment.

### Integration
- Boot Bus + `SchedulerEndpoint` + `StubEndpoint`. Use `interval:
  {seconds: 1}` job targeting stub. Assert stub receives the envelope
  within a 3s window.
- Dynamic flow: stub sends `create_job` ToolInvocation envelope from its
  test harness, awaits Acknowledgment via correlation_id, asserts
  `list_jobs` reflects the new job, then waits for the fire.
- `delete_job` flow: create then delete; verify next fire window passes
  with no stub delivery.

### Manual end-to-end (deferred)
The integration tests cover the same vertical slice. A real Claude Code
session driving the scheduler is desirable but optional; defer to user
exploration once the PR lands.

## Module Layout

```
packages/core/src/agent_core/endpoints/
├── claude_code_mcp.py    (existing, sub-project B)
├── scheduler.py          (new — this spec)
└── stub.py               (existing)

packages/core/tests/
├── test_scheduler_endpoint.py        (new — unit tests)
└── test_scheduler_integration.py     (new — bus + scheduler + stub)
```

The endpoint is a single module file (~200-300 lines). Tool handlers,
seed loader, and `_fire` are functions inside that module — split only if
the file grows beyond ~400 lines.

## References

- Channel bus spec: [`2026-04-27-channel-bus-design.md`](2026-04-27-channel-bus-design.md)
- Bus daemon spec: [`2026-04-28-bus-daemon-design.md`](2026-04-28-bus-daemon-design.md)
- Pepper's reference scheduler implementation:
  `E:\workspaces\ai\pepper\src\pepper\scheduler\` (read-only reference;
  not modified by this sub-project per the "Pepper hands-off" rule).
- Roadmap: [`docs/ROADMAP.md`](../../ROADMAP.md) — sub-project A Step 4.
