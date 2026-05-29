# Scheduler inspection and mutation recipes

Two paths:

- **Read** the live scheduler state by talking to the scheduler endpoint (canonical) OR by direct SQLite read (fallback)
- **Mutate** the scheduler ONLY via the scheduler endpoint over the bus. Never UPDATE/INSERT/DELETE against `~/.agent-core/scheduler.db` directly — see "Why no direct SQL mutation" below.

## Read: list_jobs via the bus (canonical)

Returns rich data (target, prompt, envelope_kind, payload, next_run, paused) for every job. No SQLite required, no pickle unpacking.

```python
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={"kind": "ToolInvocation", "tool": "list_jobs", "args": {}}
)

# Then read the reply
result = mcp__agent-core__consume()
# Look in items for an envelope with from="scheduler", kind="Acknowledgment".
# payload.note is a JSON-encoded list of job dicts. Parse it.
```

Each entry looks like:

```python
{
  "name": "wren-heartbeat",
  "trigger": "<APScheduler trigger repr>",
  "target": "wren",
  "prompt": None,                         # set for TextMessage jobs
  "envelope_kind": "Event",               # "TextMessage" or "Event"
  "payload": {"type": "Heartbeat", "data": {}},  # set for Event jobs
  "next_run": "2026-05-29T02:00:00+00:00",
  "paused": False,
}
```

Filter by `target` to see jobs for one being, by `name.startswith("<prefix>-")` for namespaced jobs, etc.

## Read: direct SQLite (fallback for when bus access isn't available)

When the bus is down or you're operating from a script outside any being's session, the SQLite store is still queryable. The catch: `args` and `kwargs` are pickled APScheduler-internal blobs that have to be unpickled to be useful.

**Path note:** the recipes below use `'C:/Users/jeffr/.agent-core/scheduler.db'` for the canonical Windows install. On Linux/Mac the equivalent path is `~/.agent-core/scheduler.db` (use `os.path.expanduser('~/.agent-core/scheduler.db')` in scripts). Adjust per OS before running.

### All schedules with target and next fire time

```python
import sqlite3, pickle
conn = sqlite3.connect('C:/Users/jeffr/.agent-core/scheduler.db')
for sid, args_b, paused, next_t in conn.execute(
    'SELECT id, args, paused, next_fire_time FROM schedules ORDER BY id'
).fetchall():
    try:
        args = pickle.loads(args_b) if args_b else None
        target = args[2] if args and len(args) > 2 else '<unknown>'
    except Exception as e:
        target = f'<unpickle err: {e}>'
    pause_mark = ' [PAUSED]' if paused else ''
    print(f'{sid:40s} -> {target:30s} next: {next_t}{pause_mark}')
```

### Schedules targeting a specific endpoint

```python
import sqlite3, pickle
me = 'wren'  # or 'pepper', 'testbot', 'briefs.orchestrator', etc.
conn = sqlite3.connect('C:/Users/jeffr/.agent-core/scheduler.db')
for sid, args_b, next_t in conn.execute('SELECT id, args, next_fire_time FROM schedules').fetchall():
    try: args = pickle.loads(args_b) if args_b else None
    except: continue
    if args and len(args) > 2 and args[2] == me:
        print(f'{sid:40s} next: {next_t}')
```

### Just paused schedules

```python
import sqlite3
conn = sqlite3.connect('C:/Users/jeffr/.agent-core/scheduler.db')
for sid in conn.execute('SELECT id FROM schedules WHERE paused = 1').fetchall():
    print(sid[0])
```

### Full details for one job (trigger, prompt, last/next fire)

```python
import sqlite3, pickle
job_id = '<job-name>'
conn = sqlite3.connect('C:/Users/jeffr/.agent-core/scheduler.db')
row = conn.execute(
    'SELECT task_id, args, kwargs, trigger, paused, last_fire_time, next_fire_time, '
    'coalesce, misfire_grace_time, metadata FROM schedules WHERE id = ?',
    (job_id,)
).fetchone()
if not row:
    print(f'{job_id}: not found')
else:
    task, args_b, kwargs_b, trigger_b, paused, last_t, next_t, coalesce, grace, meta_b = row
    print(f'job:           {job_id}')
    print(f'task:          {task}')
    print(f'paused:        {bool(paused)}')
    print(f'last_fire:     {last_t}')
    print(f'next_fire:     {next_t}')
    print(f'coalesce:      {bool(coalesce)}')
    print(f'misfire_grace: {grace}s')
    try:
        args = pickle.loads(args_b) if args_b else None
        # args shape: (scheduler_endpoint_name, job_name, target, prompt_or_None, metadata_dict)
        print(f'args:          {args!r}')
    except Exception as e:
        print(f'args (raw):    <unpickle err: {e}>')
    try:
        kwargs = pickle.loads(kwargs_b) if kwargs_b else {}
        # kwargs is {} for TextMessage jobs;
        # {"envelope_kind": "Event", "payload": {"type": ..., "data": ...}} for Event jobs
        print(f'kwargs:        {kwargs!r}')
    except Exception as e:
        print(f'kwargs (raw):  <unpickle err: {e}>')
    try:
        trigger = pickle.loads(trigger_b) if trigger_b else None
        print(f'trigger:       {trigger!r}')
    except Exception as e:
        print(f'trigger (raw): <unpickle err: {e}>')
```

The args tuple's fourth element (`args[3]`) is the prompt for TextMessage jobs, `None` for Event jobs. For Event jobs, the payload type+data is in `kwargs["payload"]`.

### Detecting missed fires

A job is "overdue" if `next_fire_time` is in the past AND the daemon was up for at least one fire-window since then.

The timestamps in `schedules.last_fire_time` and `schedules.next_fire_time` are **microseconds since epoch** (APScheduler v4 internal format), with a separate `*_utcoffset` column in minutes. Convert to a `datetime` before comparing against `now()`:

```python
import sqlite3, datetime
job_id = '<job-name>'
conn = sqlite3.connect('C:/Users/jeffr/.agent-core/scheduler.db')
row = conn.execute(
    'SELECT last_fire_time, last_fire_time_utcoffset, '
    'next_fire_time, next_fire_time_utcoffset '
    'FROM schedules WHERE id = ?',
    (job_id,)
).fetchone()

def to_dt(us, offset_min):
    """Convert APScheduler v4 microseconds + offset_min to a tz-aware datetime."""
    if us is None:
        return None
    tz = datetime.timezone(datetime.timedelta(minutes=offset_min)) if offset_min is not None else datetime.timezone.utc
    return datetime.datetime.fromtimestamp(us / 1_000_000, tz=tz)

if row:
    last_us, last_off, next_us, next_off = row
    last_dt = to_dt(last_us, last_off)
    next_dt = to_dt(next_us, next_off)
    now = datetime.datetime.now(datetime.timezone.utc)
    print(f'last:    {last_dt}')
    print(f'next:    {next_dt}')
    print(f'now:     {now}')
    if next_dt and next_dt < now:
        print(f'OVERDUE — next_fire_time has passed')
```

### When the daemon isn't running

`next_fire_time` won't advance and `last_fire_time` won't update if the daemon process isn't running. Verify before concluding "X is overdue":

```bash
# Windows
wmic process where "name='python.exe'" get processid,commandline 2>&1 | grep "bus run"

# Linux/Mac
ps aux | grep -E "agent_core\.cli bus run" | grep -v grep
```

No output = daemon is down, all schedules are paused-by-process-death.

## Mutate: ToolInvocation envelopes over the bus

All mutations go through the scheduler endpoint. The endpoint validates args with Pydantic, calls APScheduler's own `add_schedule`/`remove_schedule`/`pause_schedule`/`unpause_schedule` operations, and returns a result via an `Acknowledgment` reply.

**No daemon restart is required** for any of these. The endpoint mutates the live store and the in-memory scheduler state together.

### create_job

```python
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={
        "kind": "ToolInvocation",
        "tool": "create_job",
        "args": {
            "name": "wren-apex-verify",
            "trigger": "cron",
            "schedule": {"day_of_week": "thu", "hour": 16, "minute": 5},
            "target": "wren",
            "prompt": "Verify apex_weekly_slots fired to pepper at 16:00 ET today...",
            "timezone": "America/New_York",
        },
    },
)
# Reply: kind=Acknowledgment, payload.note=JSON
# Success: '{"status": "created", "name": "wren-apex-verify"}'
# Failure: 'error: <message>'
```

Triggers: `"interval"`, `"cron"`, or `"date"`. Schedule fields per trigger:

- `interval`: `{"seconds": N}` / `{"minutes": N}` / `{"hours": N}` / `{"days": N}` (combine as needed)
- `cron`: `{"second": ?, "minute": ?, "hour": ?, "day": ?, "month": ?, "day_of_week": ?, "year": ?}` (omit fields = wildcard). Always set `timezone` unless you want UTC.
- `date`: `{"run_time": "<ISO-8601-datetime>"}` — fires once.

For Event jobs, omit `prompt` and pass `envelope_kind: "Event"` + `payload: {"type": ..., "data": ...}` instead. Walked example (modeled on the actual `testbot-morning-brief` job that fires BriefRequest events to the briefs orchestrator):

```python
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={
        "kind": "ToolInvocation",
        "tool": "create_job",
        "args": {
            "name": "testbot-morning-brief",
            "trigger": "cron",
            "schedule": {"hour": 7, "minute": 0},
            "target": "briefs.orchestrator",      # NOT a being; an orchestrator endpoint
            "envelope_kind": "Event",             # NOT TextMessage
            "payload": {
                "type": "BriefRequest",
                "data": {"brief_type": "morning_brief"},
            },
            # NOTE: no `prompt` field — Event jobs don't carry one
            "timezone": "America/New_York",
        },
    },
)
```

At fire time, the scheduler publishes an envelope with `kind="Event"`, `payload=EventPayload(type="BriefRequest", data={"brief_type": "morning_brief"})` to `briefs.orchestrator`, which then runs the gather pipeline and dispatches the composed brief to the target being. This is the pattern for any cron-triggered orchestrator job — Event-kind, payload carries the orchestrator-recognized type+data, target is the orchestrator endpoint not a being directly.

### update_job (partial merge)

Only `name` is required. Every other field is optional — omitted fields are preserved from the current schedule.

```python
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={
        "kind": "ToolInvocation",
        "tool": "update_job",
        "args": {
            "name": "apex_weekly_slots",
            "prompt": "<new prompt text>",
        },
    },
)
# Reply: '{"status": "updated", "name": "apex_weekly_slots"}'
```

Common patterns:

- Change schedule only: pass `schedule` (and `timezone` if changing tz)
- Change target only: pass `target`
- Switch envelope kind from TextMessage to Event: pass `envelope_kind: "Event"` AND `payload: {"type": ..., "data": ...}` AND clear `prompt` (the endpoint will re-validate the merged shape and reject if invalid)

The endpoint re-validates the merged shape, so an invalid update can't strand the job in a bad state.

### delete_job / pause_job / resume_job

```python
# Same shape for all three:
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={"kind": "ToolInvocation", "tool": "delete_job", "args": {"name": "<job-name>"}},
)
# Replace "delete_job" with "pause_job" or "resume_job".
# Reply: '{"status": "deleted"|"paused"|"resumed", "name": "<job-name>"}'
```

### list_jobs (no args)

```python
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={"kind": "ToolInvocation", "tool": "list_jobs", "args": {}},
)
# Reply payload.note is a JSON list of job dicts; see the canonical Read section above.
```

### Reading the reply

After sending any ToolInvocation, the scheduler publishes an `Acknowledgment` envelope back. Pick it up via `mcp__agent-core__consume()` and look for:

```python
{
  "kind": "Acknowledgment",
  "from": "scheduler",
  "in_reply_to": "<your envelope id>",
  "payload": {
    "of": "<your envelope id>",
    "note": "<JSON-stringified result OR 'error: <message>'>",
  },
}
```

`note` starting with `error:` means the call failed (invalid args, job not found, etc.) — the original schedule state is unchanged.

## Why no direct SQL mutation

The scheduler uses APScheduler v4 with a SQLAlchemyDataStore. Several reasons hand-mutating the SQLite store is unsafe:

1. **`args` is a pickled tuple** with a strict 5-element shape `(scheduler_endpoint_name, job_name, target, prompt, metadata)`. A hand-rebuilt pickle is one shape-mismatch away from an unpickle error at fire time, which silently drops the fire.
2. **`kwargs` carries envelope-kind state separately** for Event jobs (`{"envelope_kind": "Event", "payload": {...}}`). A prompt-only UPDATE on `args` for an Event job would leave the kwargs stale.
3. **`trigger` is a pickled APScheduler object.** Rebuilding it correctly requires running `build_trigger(JobDef(...))` from the endpoint code, including the timezone-omission-when-None handling for cron triggers (APScheduler v4 rejects `timezone=None` explicitly).
4. **`paused` has both a DB column AND in-memory scheduler state.** A raw UPDATE of the column doesn't pause the running scheduler — the job will keep firing based on the live state, then on next daemon restart the DB value wins (so pausing via UPDATE works after a restart but not immediately). `pause_schedule()` does both.
5. **`update_job` is `remove_schedule + add_schedule`**, not an UPDATE statement. The endpoint preserves the job id (so `last_fire_time` survives) but rebuilds everything else cleanly.

The endpoint code lives at `agent_core/endpoints/scheduler.py` if you want to read it. Trust the running code over any documentation that drifted.

## Gotchas

- **Acknowledgment replies have schema `note: str`** — not structured JSON. Parse `note` with `json.loads()` and check for the `error:` prefix to distinguish failure from success.
- **Timestamps in the SQLite store are microseconds since epoch** for last/next fire time (APScheduler v4 internal format), with a separate `utcoffset` column in minutes. Convert with care.
- **`apscheduler` package** must be importable to unpickle the `trigger` column — not always present in casual one-liners. If unpickling fails, `pip install apscheduler` first or use the bus path instead (which doesn't require unpickling).
- **`metadata` column** (the column, not the args[4] dict) carries arbitrary per-job extras (e.g. APScheduler internals) — separate from the user-supplied metadata that lives inside the args tuple.
- **`jobs` and `job_results` tables** are scheduler-internal — never write to them; APScheduler manages them.
