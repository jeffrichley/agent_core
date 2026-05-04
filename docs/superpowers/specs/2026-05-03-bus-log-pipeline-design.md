# Bus Log Pipeline — design

**Date:** 2026-05-03
**Ticket:** Cutover #04 ([`pepper-cutover-04-daily-jsonl-pipeline.md`](../../requirements/pepper-cutover-04-daily-jsonl-pipeline.md))
**Author:** brainstorm with Jeff
**Related:**
- Cutover #08 (notification surfaces — what gets published to the bus)
- Cutover #05 (skills discovery — WAR skill consumes summaries this pipeline produces)
- [`docs/superpowers/specs/2026-04-27-channel-bus-design.md`](2026-04-27-channel-bus-design.md) (bus + responsive inbox)

---

## Goal

Persist every bus envelope as JSONL so each agent's existing reflection job (e.g., Pepper's 3 AM cron that produces `Memory/daily/summaries/<date>.md`) keeps working through cutover, **with no information loss** and **no duplication on disk** when multiple agents share one bus.

## Why this matters

The daily summaries are load-bearing in three places (per the spec):
1. **WAR skill** — Friday's executive summary uses the week's daily summaries.
2. **SessionStart context** — Pepper's `CLAUDE.md` loads the most recent two summaries every boot.
3. **Self-narration** — "what did I do yesterday" is grounded in summaries.

If the responsive inbox + channel relay (cutover #08) ship without the JSONL pipe, summaries silently go blind. Failure isn't visible until Friday's WAR run finds nothing to summarize.

## Scope

**In scope:**
- BusHook on `pre_publish` writing one JSONL file per day at a daemon-owned location.
- `agent_core.bus_log` library: read + filter + project envelopes.
- CLI `agent-core bus-log show` for cron / operator use.
- MCP tool `show_my_day` on `ClaudeCodeMCPEndpoint` for agent self-introspection.
- Default projectors for `TextMessage`, `Acknowledgment`, `HandoffReady`, `HandoffFailed`, scheduler-heartbeat.
- Pluggy entry-point registration so other packages (e.g., `agent-core-discord`) ship their own projectors.

**Not in scope:**
- The reflection job itself — that's Pepper's existing code (`gather.py`). It gets a one-line change to call the new library function.
- Cross-machine deployment (the bus log lives in the daemon's storage area; if reflection ever needs to run on a different machine than the daemon, that's a separate ticket — likely an HTTP export endpoint).
- Multi-tenant privacy (today all agents are owned by the same operator; per-agent file isolation is a future concern).
- Replacing the SessionEndWriter daily-JSONL append. That stream is hook-pipeline scoped (per-session transcripts) and orthogonal to bus traffic.

---

## Architecture

```
                                       ┌────────────────────────────┐
                                       │ Bus daemon — pre_publish    │
                                       └─────────────┬──────────────┘
                                                     │
                                                     ▼ (DailyRawJsonlHook)
                          ┌──────────────────────────────────────────────────┐
                          │ ~/.agent-core/bus/raw/<date>.jsonl                │
                          │ One line per envelope. Bus-native shape, full     │
                          │ fidelity. ALL agents, ALL traffic. Single source. │
                          └─────────────────────────┬────────────────────────┘
                                                    │
                                                    │  agent_core.bus_log.iter_for_agent(
                                                    │      path, agent="pepper", projected=True)
                                                    │
                       ┌────────────────────────────┴────────────────────────────┐
                       │                                                          │
                       ▼ filter (envelope.to == agent OR envelope.from_ == agent) │
              ┌────────────────────────────────────┐                              │
              │ Per-agent envelope stream          │                              │
              │ (still bus-native shape)           │                              │
              └────────────────────┬───────────────┘                              │
                                   ▼ project via registered projector             │
              ┌────────────────────────────────────┐                              │
              │ Tool 3 rows                        │                              │
              │ {ts, dir, src, cid, sender,        │                              │
              │  content}                          │                              │
              └────────────────────┬───────────────┘                              │
                                   │                                               │
            ┌──────────────────────┼─────────────────────────┐                     │
            ▼                      ▼                         ▼                     │
    ┌───────────────┐   ┌───────────────────┐   ┌────────────────────────┐         │
    │ CLI:          │   │ MCP tool:         │   │ Reflection job (cron):│         │
    │ agent-core    │   │ show_my_day       │   │ calls iter_for_agent  │◄────────┘
    │ bus-log show  │   │ on Claude         │   │ then summarizes →     │
    │ --agent ...   │   │ CodeMCPEndpoint   │   │ daily/summaries/      │
    └───────────────┘   └───────────────────┘   │ <date>.md             │
                                                └────────────────────────┘
```

**Layers:**

1. **Write side (bus-owned, single source of truth):** one BusHook, one file, one day. Bus-native shape — full envelope, no projection, no per-agent split.
2. **Read library (`agent_core.bus_log`):** filter to one agent's perspective, project to Tool 3 shape via registered projectors. Same machinery for all callers.
3. **Surfaces (thin wrappers over the library):** CLI for cron/operator, MCP tool for in-session agent introspection, direct library import for the reflection job.

---

## Decisions

### D1 — Bus-native shape on disk; project at read time

**Decision:** The JSONL line is the full envelope (`id`, `correlation_id`, `from_`, `to`, `kind`, `payload` with full `kind`+`type`+`schema_version`+`data`, `urgency`, `metadata`, `created_at`). Projection to the reflection job's Tool 3 shape happens when reading, not writing.

**Rationale:** Writing Tool 3 directly loses every structured field on `Event` envelopes (HandoffReady's `job_id`, `discord.reaction_add`'s emoji + user, etc.). Once dropped, it can't be recovered. Bus-native preserves everything; future skills that want richer detail (e.g., "summarize my agent-to-agent handoffs this week") read the same files.

**Cost:** The reflection job needs a one-line change: instead of reading raw JSON dicts, call `iter_for_agent(...)`. That's "the single documented adapter" the spec invites.

### D2 — Single bus-owned log, not per-agent files with duplication

**Decision:** ONE file per day at `~/.agent-core/bus/raw/<date>.jsonl`. Filter at read time when each agent's reflection job runs.

**Rationale:** Per-agent files duplicate cross-agent traffic on disk and require per-agent hook configuration (linear scaling, more registration footguns). Single-source-of-truth matches the bus's role and keeps onboarding new agents zero-config (their reflection just calls `iter_for_agent(agent="newbot")`).

**Trade-off accepted:** All agents' traffic shares one file. For now, all agents are owned by the same operator (Jeff), so cross-tenant privacy isn't a concern. If multi-tenant ever becomes real, per-agent files (or per-tenant files) becomes a separate ticket.

### D3 — Adapter (projector) protocol, plugin-registered

**Decision:** A `Projector` protocol with a single `render(envelope, *, perspective) -> dict | None` method, registered via the same pluggy entry-point mechanism agent_core uses for hook tools, endpoints, and bus_hooks. Lookup key: `payload.type` for Events; the envelope `kind` (e.g., `"TextMessage"`) for non-Events.

**Rationale:** Each event type knows how to render itself best. Without a registry, the reflection job needs hardcoded knowledge of every event type. With a registry, new event types ship with their own projector and the reflection job stays generic. Discord-specific rendering lives in `agent-core-discord`; agent_core stays decoupled.

### D4 — Fallback projector for un-registered event types

**Decision:** If no projector is registered for an envelope's `payload.type` or `kind`, a fallback projector renders generic content like `event:{type} data={shortJSON}`. Never silently dropped.

**Rationale:** Fail-loud-then-degrade-gracefully. New event types appear in summaries in a generic form until someone writes a proper projector; nothing silently disappears. The alternative — skip with a warn log — risks losing entire categories of events from summaries until someone happens to notice.

### D5 — `--agent` required at the CLI; auto-scoped at the MCP tool

**Decision:** `agent-core bus-log show` requires `--agent <name>`. The MCP tool `show_my_day` takes no `agent` parameter; it reads `self.name` from the endpoint.

**Rationale:**
- Cron and operators have no implicit identity; making `--agent` explicit prevents "I forgot the flag and dumped cross-agent traffic to my console."
- The MCP server already knows the calling agent (each `ClaudeCodeMCPEndpoint(name="pepper")` is bound to one agent at construction). Asking for `agent` would be redundant and creates a "Pepper passes `agent='vale'`" footgun. Auto-scoping prevents that by construction.

### D6 — Three call surfaces, one library

**Decision:** Library function (`iter_for_agent`) is the API. CLI and MCP tool are thin wrappers. The reflection job imports the library directly.

**Rationale:** All three callers want the same logic (filter + project). One implementation, three thin shells. The MCP tool ships in #04 because the marginal cost is ~15 lines once the library exists, and it unlocks "what just happened" agent self-introspection for free.

### D7 — Hook on `pre_publish` only

**Decision:** Register `DailyRawJsonlHook` only at `pre_publish`. Skip `pre_deliver`.

**Rationale:** Each logical publish should appear once in the log. `pre_publish` fires once per `bus.publish()`; `pre_deliver` fires per delivery attempt and would duplicate on redelivery. The log represents intent (an envelope was sent on the bus) rather than transport detail (how many times we tried to deliver it).

### D8 — Heartbeat filtering at projector level, not write time

**Decision:** The raw log retains every envelope (modulo D9 below). Heartbeat noise is filtered when projecting for summaries.

**Rationale:** The raw file should be debugging-complete. If we filter heartbeats at write time, we can never reconstruct "did the heartbeat fire on Tuesday?" The summary stays clean by having the heartbeat projector return `None`.

### D9 — Skip routine envelope kinds at write time: `Acknowledgment`, `Progress`, `Cancellation`

**Decision:** The hook does NOT log `Acknowledgment` / `Progress` / `Cancellation` envelopes. These are transport-protocol envelopes, not user-meaningful traffic. Configurable but defaults to skip.

**Rationale:** Routine green acks alone would dominate any reasonable day's traffic by 10–100×, and they carry no semantic value the reflection job could use. Different from D8 because heartbeats *might* be debugging-relevant; routine acks essentially never are.

---

## Schemas

### Bus-native JSONL line (the on-disk format)

One JSON object per line, full envelope. Example for a Discord inbound:

```json
{
  "id": "a1b2c3...",
  "correlation_id": "c1d2e3...",
  "from_": "discord",
  "to": "pepper",
  "kind": "TextMessage",
  "payload": {
    "kind": "TextMessage",
    "text": "Did you see the report?",
    "schema_version": "1"
  },
  "urgency": "yellow",
  "metadata": {
    "discord_channel_id": "...",
    "discord_guild_id": "...",
    "discord_user_display_name": "Jeff"
  },
  "created_at": "2026-05-03T17:42:13.123456+00:00"
}
```

Example for a HandoffReady event:

```json
{
  "id": "h1...",
  "correlation_id": "c2...",
  "from_": "handoff-jobs",
  "to": "pepper",
  "kind": "Event",
  "payload": {
    "kind": "Event",
    "type": "HandoffReady",
    "schema_version": "1",
    "data": {
      "job_id": "j-1",
      "session_id": "s-1",
      "handoff_path": "C:\\Users\\jeffr\\.pepper\\Memory\\pepper\\handoff.md",
      "content_sha256": "..."
    }
  },
  "urgency": "green",
  "metadata": {"handoff_job_id": "j-1", "handoff_event": "SessionEnd"},
  "created_at": "2026-05-03T17:45:00.000000+00:00"
}
```

### Tool 3 row (the projection target)

The shape Pepper's `gather.py` already expects. Yielded by `iter_for_agent` when `projected=True`:

```json
{
  "ts": "2026-05-03T13:42:13-04:00",
  "dir": "in",
  "src": "discord",
  "cid": "c1d2e3...",
  "sender": "Jeff",
  "content": "Did you see the report?"
}
```

- `ts`: ISO 8601 derived from `envelope.created_at`, rendered in the timezone passed to `iter_for_agent` (read-time concern, not bake-time). Default `US/Eastern`. Different reflection jobs can request different timezones from the same source file.
- `dir`: `"in"` if `envelope.to == perspective`, `"out"` if `envelope.from_ == perspective`, `"self"` if both (rare; agent talking to itself).
- `src`: `envelope.from_` by default. Per-event-type projectors may override (e.g., a future channel-relay projector could surface the channel id here).
- `cid`: `envelope.correlation_id` — preserves message threading.
- `sender`: human-readable display name (e.g., Discord user display name from metadata) when available; else `envelope.from_`.
- `content`: text content for `TextMessage`; the per-event-type rendering for Events; generic fallback otherwise.

### Projector protocol

```python
from typing import Protocol
from agent_core.bus.envelope import Envelope

class Projector(Protocol):
    """Render a bus envelope into a Tool 3 summary row, or skip it."""

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None:
        """Return a Tool 3-shaped dict, or None to skip this envelope from
        the summary entirely (e.g., heartbeat noise).

        `perspective` is the agent name reading the log — drives `dir`
        (in/out) and `src`/`sender` interpretation when the same envelope
        appears in both A's and B's logs.

        `timezone` is an IANA timezone string (e.g., "US/Eastern") that
        determines how `envelope.created_at` is rendered into `ts`.
        Passed by the caller; projectors do not have an opinion of their
        own — same source file, different perspectives, different ts
        timezones.
        """
        ...
```

### Registration mechanism

Two paths, mirroring how hook tools / endpoints / bus_hooks are registered today:

1. **Pluggy entry point** — packages declare in their `pyproject.toml`:
   ```toml
   [project.entry-points."agent_core.bus_log_projectors"]
   "discord.reaction_add" = "agent_core_discord.projectors:ReactionAddProjector"
   ```
   `agent_core.bus_log` discovers these at import time.

2. **Programmatic** — for tests and ad-hoc registration:
   ```python
   from agent_core.bus_log import register_projector
   register_projector("HandoffReady", HandoffReadyProjector())
   ```

Lookup priority: `payload.type` (for `Event` envelopes) → `envelope.kind` (for `TextMessage`, etc.) → fallback projector.

---

## Components

### `DailyRawJsonlHook` (write side)

Single `BusHook` registered at `pre_publish`. Config:

```yaml
bus_hooks:
  pre_publish:
    - type: builtin.daily_raw_jsonl
      params:
        log_root: "~/.agent-core/bus/raw"      # daemon-owned location
        timezone: "US/Eastern"                  # for date-rolling
        skip_kinds: ["Acknowledgment", "Progress", "Cancellation"]
        # Optional substring-based content filter (if needed for sensitive data):
        # skip_content_substrings: []
```

Behavior:
- Resolve target path: `<log_root>/<YYYY-MM-DD>.jsonl` using `timezone` for date.
- If `envelope.kind in skip_kinds`, return envelope unchanged (no write).
- Serialize envelope via Pydantic `model_dump_json()` — preserves the full shape including `EventPayload.type` and `data`.
- Append-only write with a single `f.write(line + "\n")`. Open → write → close per line for crash safety (filesystem flush on close; we accept the syscall overhead — bus traffic volume is modest).
- On `OSError`: log + return envelope unchanged. Hook MUST NOT abort publish — the bus is the source of truth, the log is observability.

### `agent_core.bus_log` library

```python
def iter_envelopes(
    path: Path,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Iterator[Envelope]:
    """Yield raw envelopes from one daily file, optionally bounded by time."""

def iter_for_agent(
    path: Path,
    *,
    agent: str,
    projected: bool = True,
    timezone: str = "US/Eastern",
    since: datetime | None = None,
    until: datetime | None = None,
) -> Iterator[Envelope | dict]:
    """Yield envelopes touching `agent` (to or from_).
    With projected=True, yield Tool 3 dicts via registered projectors,
    rendered in the given `timezone`; None projector results are skipped.
    With projected=False, yield raw Envelope instances (the timezone is
    irrelevant in that mode and is ignored)."""

def register_projector(key: str, projector: Projector) -> None:
    """Register a projector for an Event payload type or envelope kind.
    Programmatic; pluggy entry points are the package-level form."""
```

### Default projectors shipped in `agent_core`

- `TextMessageProjector` — `dir` from `to`/`from_` vs perspective; `sender` from metadata when present (e.g., `discord_user_display_name`); `content` is `payload.text`.
- `AcknowledgmentProjector` — returns `None` (skip; already filtered at write time but defensive).
- `HandoffReadyProjector` — `content="continuity ready: handoff.md → {handoff_path}"`. `src="handoff-jobs"`.
- `HandoffFailedProjector` — `content="continuity failed: {error}"`. `src="handoff-jobs"`.
- `SchedulerHeartbeatProjector` — recognizes scheduler heartbeats by `metadata.scheduler_job` matching a heartbeat name; returns `None`.
- `FallbackProjector` — for any unregistered Event type: `content="event:{type} data={shortJSON}"`. Never returns `None`.

### CLI: `agent-core bus-log show`

```
agent-core bus-log show --agent <name> [options]

Options:
  --agent NAME           Required. Whose perspective to filter to.
  --date YYYY-MM-DD      Date to read. Default: today (in --timezone).
  --raw                  Skip projection; emit raw envelope JSON. Default: projected.
  --projected            Force projected output (default).
  --log-root PATH        Where the daily files live. Default: ~/.agent-core/bus/raw.
  --timezone TZ          For --date interpretation. Default: US/Eastern.
  --limit N              Last N rows only.
```

Output: JSON lines on stdout. Cron pipes this to its summarizer:

```bash
agent-core bus-log show --agent pepper --date 2026-05-02 \
  | python /pepper/reflection/summarize.py > /pepper/Memory/daily/summaries/2026-05-02.md
```

### MCP tool: `show_my_day`

Added to `ClaudeCodeMCPEndpoint` next to `list_pending` and `handle`:

```python
@self._mcp.tool()
async def show_my_day(
    date: str | None = None,
    projected: bool = True,
    limit: int | None = None,
) -> list[dict]:
    """Return today's bus traffic for this agent.
    Use for self-introspection ('what just happened') or feeding into a
    reflection summary. Projection produces Tool 3-shaped rows."""
    target = date or datetime.now(UTC).strftime("%Y-%m-%d")
    path = self._bus_log_root / f"{target}.jsonl"
    if not path.exists():
        return []
    rows = list(iter_for_agent(path, agent=self.name, projected=projected))
    return rows[-limit:] if limit else rows
```

Constructor gains `bus_log_root: Path | None = None` (parameter alongside `mount`, `wake_on_all_acknowledgments`, etc.). When `None`, defaults to `~/.agent-core/bus/raw` — same default the `DailyRawJsonlHook` uses, so a config that sets neither just works.

The endpoint config in `agent_core.yaml` gains a matching field. **Consistency requirement:** if either the BusHook or the endpoint sets `log_root` / `bus_log_root` to a non-default value, both MUST point at the same directory — the endpoint reads what the hook writes. Either set both, or set neither. We do not introduce a daemon-level shared config field for this in #04 because it's the only field that needs sharing today; if more arrive, a `daemon.bus.log_root` config namespace becomes worth the indirection.

The agent name is `self.name` — set at endpoint construction (line 125 of `claude_code_mcp.py`). Each agent's MCP server is a separate instance; cross-agent leakage is prevented by construction, not by trust in the caller.

---

## Pepper's reflection job — adapter

Per the spec: "with no code changes (or with a single documented adapter)." The adapter is a one-line change to wherever `gather.py` currently reads the day's JSONL:

```python
# Before: read raw dict per line from Memory/daily/raw/<date>.jsonl
# After:
from agent_core.bus_log import iter_for_agent

rows = list(iter_for_agent(
    Path.home() / ".agent-core/bus/raw" / f"{date}.jsonl",
    agent="pepper",
    projected=True,
))
```

That's the entirety of the integration on Pepper's side. Everything downstream (summarization, prompt construction, output formatting) is unchanged.

---

## Testing strategy

**Unit:**
- BusHook: round-trip a TextMessage and an Event envelope, assert the JSONL line round-trips through `iter_envelopes` to identical fields.
- BusHook: skip kinds (Acknowledgment, Progress, Cancellation) by default, configurable.
- BusHook: OSError on write doesn't abort publish.
- `iter_for_agent`: filter accepts to-only, from-only, both, neither.
- Projectors: TextMessage with/without metadata sender; HandoffReady; HandoffFailed; FallbackProjector for unknown event type.
- `register_projector` overrides + falls back correctly.
- Heartbeat projector returns None for matching scheduler-heartbeat envelopes.

**Integration:**
- End-to-end: a real `Bus.publish` goes through the hook and produces a JSONL file; reading it via `iter_for_agent(agent="...")` produces the expected Tool 3 rows.
- MCP tool: `show_my_day` returns rows scoped to the connected agent name; can't be coerced to return another agent's rows.

**Acceptance scenarios** (from spec §"Done looks like"):
- Discord inbound + Pepper's reply → both rows in Pepper's perspective view.
- Scheduler trigger → row in Pepper's perspective view.
- Channel-relay event (notifications/claude/channel underlying envelope) → row.
- Heartbeat scheduler → filtered out of projected view.

---

## Open questions

(None — all decisions locked above.)

## Non-goals

- Replace the SessionEndWriter daily-JSONL stream. Different scope (per-session transcripts vs bus traffic).
- Cross-machine reflection (daemon on machine A, agent runs on machine B). Out of scope; future ticket if needed.
- Multi-tenant isolation. Out of scope; future ticket if multi-operator scenarios become real.
- Backfilling the daemon-owned log from existing per-agent `Memory/daily/raw/` files. The spec doesn't ask for it; if a historical re-summary is ever needed, it's an ad-hoc migration, not framework code.
