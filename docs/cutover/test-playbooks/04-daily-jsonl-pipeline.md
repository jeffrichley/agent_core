# Cutover #04 — Daily JSONL pipeline (test playbook)

**Spec:** [`docs/requirements/pepper-cutover-04-daily-jsonl-pipeline.md`](../../requirements/pepper-cutover-04-daily-jsonl-pipeline.md)
**Design:** [`docs/superpowers/specs/2026-05-03-bus-log-pipeline-design.md`](../../superpowers/specs/2026-05-03-bus-log-pipeline-design.md)
**Implementation commits:**
- `0482849` feat(bus_log): projector protocol + registry skeleton
- `a69e147` fix(bus_log): apply Task 1 code-quality review feedback
- `bec28f2` feat(bus_log): TextMessage projector + real fallback projector
- `a1d4586` test(bus_log): cover Task 2 fallback projector branches missed by review
- `93d36b3` feat(bus_log): iter_envelopes — raw read with time bounds and malformed-line tolerance
- `f28c9a2` fix(bus_log): apply Task 3 code-quality review feedback
- `34c3780` feat(bus_log): iter_for_agent — filter + project with timezone passthrough
- `7605a06` fix(bus_log): apply Task 4 code-quality review feedback
- `7823eb0` feat(bus_log): HandoffReady/HandoffFailed/Ack-skip/Heartbeat-skip projectors
- `fe183cb` fix(bus_log): apply Task 5 code-quality review feedback (test only)
- `f8088ad` feat(bus_hooks): DailyRawJsonlHook — append-only bus log writer at pre_publish
- `3c9aa56` fix(bus_log): apply Task 6 code-quality review feedback
- `78ffde6` feat(plugins): bus_log projectors via pluggy register_bus_log_projectors hookspec
- `138d09d` fix(plugins): apply Task 7 code-quality review feedback
- `8d38645` feat(plugins): register builtin.daily_raw_jsonl bus hook type
- `5dbcc36` feat(cli): agent-core bus-log show — inspect daily bus traffic
- `befc29b` fix(bus_log/cli): apply Task 9 code-quality review feedback
- `bf2d8b8` feat(claude_code_mcp): show_my_day MCP tool — agent-scoped bus log view
- `e78702a` fix(claude_code_mcp): apply Task 10 code-quality review feedback
- `92c28d5` feat(yaml): wire builtin.daily_raw_jsonl bus hook into pepper example + tripwire test

## What was implemented

Single bus-owned daily JSONL log written by a `pre_publish` BusHook, plus a read library + inspection CLI + per-agent MCP tool.

- Write: `builtin.daily_raw_jsonl` writes every published envelope to `~/.agent-core/bus/raw/<date>.jsonl` in bus-native shape (full envelope, no info loss). Skips `Acknowledgment`/`Progress`/`Cancellation` by default; configurable.
- Library: `agent_core.bus_log` exposes `iter_envelopes` (raw) and `iter_for_agent` (filter to one agent + project to Tool 3 rows via registered projectors).
- Projectors: default coverage for `TextMessage` (with scheduler-heartbeat skip), `Acknowledgment` (skip), `HandoffReady`, `HandoffFailed`, plus a fallback projector that renders unregistered event types generically (never silently dropped). New event types register projectors via the `register_bus_log_projectors` pluggy hookspec.
- CLI: `agent-core bus-log show --agent <name> [--date YYYY-MM-DD] [--projected | --raw] [--limit N]` — for cron and operators.
- MCP: `show_my_day(date=None, projected=True, limit=None)` on `ClaudeCodeMCPEndpoint` — agent identity is read from `self.name`; there is no `agent` parameter, so cross-agent queries are prevented by construction.
- Pepper example yaml (`docs/examples/pepper-agent-core.yaml`) registers the hook on `pre_publish`. Tripwire test in `test_pepper_example_yaml.py` catches removal.

Pepper's existing 3 AM reflection job (her code, not in this repo) gets a single one-line change to call `iter_for_agent(...)` instead of reading raw JSON files. That's "the single documented adapter" the spec invites.

## Acceptance criteria (from spec §"Done looks like")

A mixed-traffic test day produces a JSONL file the reflection job can summarize:

1. Discord inbound message + Pepper's reply → both in JSONL with consistent envelope shape.
2. Scheduler trigger (e.g., scheduled prompt) → in JSONL.
3. Channel-relay event (notification through `notifications/claude/channel`) — the underlying envelope that triggered the relay → in JSONL.
4. The existing reflection job runs at 3 AM and produces `Memory/daily/summaries/<date>.md` matching the current shape (after the one-line `iter_for_agent` adapter).

Heartbeat-noise filtering still applies (the WAR `gather.py` filters scheduler-heartbeat entries; the projector layer filters them too via `SchedulerHeartbeatSkipProjector`).

## Verification steps (end-of-cutover)

### Step 1 — Automated unit + integration tests

```powershell
cd E:\workspaces\ai\agents\agent_core
uv run pytest packages/core/tests/test_bus_log_projectors_registry.py `
              packages/core/tests/test_bus_log_default_projectors.py `
              packages/core/tests/test_bus_log_reader.py `
              packages/core/tests/test_bus_log_iter_for_agent.py `
              packages/core/tests/test_bus_log_projector_discovery.py `
              packages/core/tests/test_daily_raw_jsonl_hook.py `
              packages/core/tests/test_bus_log_cli.py `
              packages/core/tests/test_show_my_day_mcp_tool.py `
              packages/core/tests/test_pepper_example_yaml.py -v
```

Expected: all green. Confirms the registry, projectors (TextMessage/Fallback/HandoffReady/HandoffFailed/Acknowledgment-skip/Heartbeat-skip), reader (raw + filtered + projected, time-bounded), pluggy entry-point discovery, BusHook (write side, skip kinds, timezone-aware date rolling, OSError tolerance), CLI (all flag combinations), MCP tool (auto-scoping by `self.name`), and the Pepper-yaml tripwire all hold.

### Step 2 — Live mixed-traffic day

Boot the bus daemon with the example yaml. Drive a small mixed-traffic day:

1. Send a Discord message to Pepper. She replies via the channel relay.
2. Trigger a scheduler job (one-shot near-future entry).
3. Force a SessionEnd to fire a `HandoffReady` (cutover #02 mechanism).

**Test:**

```powershell
uv run agent-core bus-log show --agent pepper --date $(Get-Date -Format "yyyy-MM-dd")
```

Expected: Tool 3-shaped rows on stdout covering all three streams. Heartbeat envelopes (if scheduler heartbeats are configured) must NOT appear in projected output — confirm with `--raw` that they exist on disk:

```powershell
uv run agent-core bus-log show --agent pepper --date $(Get-Date -Format "yyyy-MM-dd") --raw `
  | Select-String '"scheduler_job":"heartbeat"'
```

Expected: matches present in raw output, absent from projected.

### Step 3 — Reflection-job adapter

In Pepper's reflection codebase (separate repo), update `gather.py` to read via `iter_for_agent`:

```python
from agent_core.bus_log import iter_for_agent
rows = list(iter_for_agent(
    Path.home() / ".agent-core/bus/raw" / f"{date}.jsonl",
    agent="pepper",
    projected=True,
))
```

Run the 3 AM reflection cron manually against yesterday's log. **Test:** `Memory/daily/summaries/<yesterday>.md` is produced, has Pepper's traffic, and matches the existing shape Friday's WAR consumes.

### Step 4 — In-session self-introspection (MCP tool)

From a live Pepper session, call `show_my_day` via MCP. Expected: returns the same Tool 3 rows the CLI produces, but scoped automatically to Pepper. Verify cross-agent isolation: Pepper cannot pass `agent="vale"` because the parameter doesn't exist.

## Pass/fail summary

| Check | Pass when |
|---|---|
| Step 1 | All 9 listed test files green. |
| Step 2 | Mixed-traffic day produces projected rows for Discord in/out, scheduler trigger, and HandoffReady; heartbeats absent from projected, present in raw. |
| Step 3 | Reflection job's one-line adapter runs unmodified; produces `daily/summaries/<date>.md` matching existing shape. |
| Step 4 | `show_my_day` returns Pepper's rows; cannot be coerced to another agent. |

## Known limitations (recorded; not blocking #04 done)

- **Unbounded log growth.** Daily files have no rotation, no size limit, and no retention policy. Each day is a separate file but they accumulate forever in `~/.agent-core/bus/raw/`. At hundreds of envelopes per day, files are KB-to-low-MB; after a year of operation the directory is hundreds of MB. Cleanup is currently a manual `rm` against old dates. If/when retention becomes operationally painful, add a sweep job — separate ticket.
- **Cross-machine deployment.** The bus log lives on the daemon's machine. If Pepper's reflection ever runs on a different machine than the daemon, an HTTP export endpoint or file sync becomes necessary — separate ticket.
- **Multi-tenant isolation.** All agents' traffic shares one file. Today all agents are owned by the same operator; if multi-tenant ever becomes real, per-agent files become a follow-up ticket.
- **Slow-consumer / disk-full robustness.** The hook catches `OSError` and continues (logged at ERROR). An adversarial environment could lose log entries. Acceptable because the bus is the source of truth; the log is observability.
- **No backfill from existing `Memory/daily/raw/`.** If a historical re-summary is ever needed, that's an ad-hoc migration, not framework code.
