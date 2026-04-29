# Responsive Inbox — Sub-project I Design (Part 1: Daemon Side)

**Date:** 2026-04-29
**Status:** Implemented (8 commits on `feat/responsive-inbox`); paired with channel relay.
**Source material:** Pepper's `inbox-architecture.md` (2026-04-29);
adversarial review of PR #8; testbot validation showing polling latency.
**Companion:** `docs/BACKLOG.md` entries `_notify_mail_arrived` (resolved
by this spec) and `Heartbeat-checker endpoint` (deferred).

> **Addendum (2026-04-29, after live testbot validation):** This spec covers
> the **daemon-side** half of sub-project I. Live testbot validation revealed
> that plain Claude Code drops `notifications/claude/channel` notifications
> (standard `ClientSession` validates against a strict `ServerNotification`
> union and discards unknown methods). The agent-side consumer that delivers
> autonomous wake is specified in
> [`2026-04-29-channel-relay-design.md`](2026-04-29-channel-relay-design.md).
> Both halves ship together as one PR; either alone is incomplete.

---

## TL;DR

Today, agents (testbot, future Pepper-on-agent-core) only see new envelopes
when they call `list_pending` themselves. There is no server-push, so an
inbound Discord message just sits in the mailbox until the next user prompt
nudges the agent to check. Live testbot validation made this concrete:
posting in Discord and waiting for the agent to react required actively
prompting the agent to read its mailbox. Pepper's existing channel server
handles this by pushing custom MCP notifications down the SSE stream; we've
deferred porting that capability twice now.

This spec covers the minimum surface required for an agent to feel responsive:

1. **Push notifications** for envelope arrival, via FastMCP middleware that
   captures the connected `ServerSession` and pushes a custom
   `notifications/claude/channel` summary on each delivery.
2. **Urgency** as a top-level field on the bus envelope, schema-validated,
   so producers can mark red-lane events and the mailbox view sorts
   accordingly.
3. **Same-sender batching** at `list_pending` read time, so a burst of
   messages from one source collapses into one logical event for the agent.

These three together cover the consumer-side "responsive" gap. Producer-side
suppression of no-op heartbeats is a separate sub-project (see BACKLOG).

---

## Goal

Make `ClaudeCodeMCPEndpoint` push-aware and urgency-aware so any agent
connecting to the daemon (testbot, Pepper, future fleet members) feels as
responsive as Pepper does on her current channel server, while building on
the bus's clean separation rather than re-creating Pepper's hand-rolled
gymnastics.

---

## Non-goals

- Heartbeat suppression / no-op cancellation. Deferred to its own sub-project
  per the BACKLOG entry. The check-running infrastructure doesn't exist yet
  in agent-core, and standing it up is multi-week work tied to integrations.
- Multi-session-per-endpoint support. We refuse a second concurrent session
  on the same endpoint as a defense-in-depth choice. Multi-instance agents
  get separate endpoints (`agent-pepper-primary`, `agent-pepper-shadow`),
  not shared mailboxes.
- Wake-cycle `done()` semantics. Agreed during brainstorming that the agent
  simply stops calling `list_pending` when it has nothing to do; push fires
  on next arrival. No protocol-level cycle boundary.
- Cross-sender batching. Pepper's spec explicitly excluded it ("different
  senders = different cognitive events"), and we follow the same rule.
- Distributed / multi-process push (Docket). We are a single-process daemon
  and don't need Redis.

---

## Architecture

### Existing surface (recap)

Each agent gets one `ClaudeCodeMCPEndpoint` instance, registered on the bus
with a unique name (`agent-testbot`, `agent-pepper`). The endpoint:

- Mounts a FastMCP HTTP server at `/mcp/<name>` (Streamable HTTP transport).
- Holds an in-memory mailbox `_pending: list[Envelope]` populated by
  `deliver()`.
- Exposes 7 MCP tools today: `send`, `list_endpoints`, `describe_endpoint`,
  `list_pending`, `handle`, `ack`, `nack`.

The bus routes by `envelope.to`, so each endpoint's mailbox contains only
envelopes addressed to that endpoint name. Multi-agent operation is already
supported by construction: registering more endpoints simply works.

What's missing is everything described below.

### New: session registry + push

A new `SessionRegistryMiddleware` is attached to each `ClaudeCodeMCPEndpoint`'s
FastMCP server. Pattern modeled on FastMCP's official `PingMiddleware`,
verified against `fastmcp/server/middleware/ping.py` source.

On the first MCP message from a session, the middleware:

1. Acquires a per-endpoint `anyio.Lock`.
2. Checks if `id(session)` is already known. If yes, returns immediately.
3. Reads `session._subscription_task_group` (an `anyio.TaskGroup` attached
   by FastMCP's `MiddlewareServerSession` for exactly this purpose).
4. Spawns a long-lived `_claim_session` coroutine into that task group via
   `tg.start_soon(...)` and adds the id to the known set. **Crucially this
   does not await the spawned task** — `start_soon` schedules and returns.
5. Releases the lock and continues to `call_next(ctx)`.

The spawned `_claim_session` coroutine runs for the session's full lifetime:

```python
async def _claim_session(self, session, sid):
    try:
        self._endpoint._register_session(session)
        await anyio.sleep_forever()
    finally:
        self._endpoint._unregister_session(session)
        self._known.discard(sid)
```

When the SSE stream closes (client disconnect, daemon stop, network drop),
FastMCP cancels `session._subscription_task_group`. Cancellation propagates
to `sleep_forever()`, the `finally:` block runs, and the registry entry is
cleared. This is exactly how `PingMiddleware._ping_loop` cleans up — same
shape, same guarantees.

`_register_session` enforces the single-slot collision policy:

```python
def _register_session(self, session) -> None:
    if self._active_session is not None and self._active_session is not session:
        raise RuntimeError(
            f"endpoint '{self.name}' already has an active session; "
            f"refusing concurrent connection"
        )
    self._active_session = session
```

Push happens through `_notify_mail_arrived(envelope_id)`. Replaces today's
no-op log line:

```python
async def _notify_mail_arrived(self, envelope_id: str) -> None:
    session = self._active_session
    if session is None:
        return  # agent will see via list_pending on next connect
    summary = self._build_summary()  # see below
    try:
        await session.send_message(_make_channel_notification(summary))
    except Exception:
        log.warning("push to '%s' failed; clearing slot", self.name, exc_info=True)
        self._active_session = None  # treat as dead; rely on polling fallback
```

No `asyncio.Lock` around `send_message`: traced in the MCP SDK, it bottoms
out at `await self._write_stream.send(message)` against an
`anyio.MemoryObjectSendStream`, which is multi-producer-safe by design.

### Notification payload shape

Method: `notifications/claude/channel` (Pepper's existing precedent;
verified Claude Code wakes the agent on this method). Params:

```json
{
  "content": "INBOX: 3 pending — 1 from discord-pepper (TextMessage), 2 routine",
  "meta": {
    "count": 3,
    "urgency_max": "yellow",
    "urgency_counts": {"red": 0, "yellow": 1, "green": 2},
    "by_sender": [
      {"from": "discord-pepper", "count": 1, "kinds": ["TextMessage"]},
      {"from": "scheduler", "count": 2, "kinds": ["ToolInvocation"]}
    ],
    "endpoint": "agent-pepper",
    "fired_at": "2026-04-29T13:42:18Z"
  }
}
```

The agent's notification handler responsibility, as documented in the
endpoint's MCP server `instructions`: when this notification fires, call
`list_pending(...)` to read full content and decide which envelope(s) to
process. The summary is enough to triage urgency and decide whether the
wake matters.

### Coalescing burst arrivals

If three envelopes arrive within microseconds, naïve "one push per arrival"
produces three near-identical notifications. We debounce per-endpoint:

```python
async def _notify_mail_arrived(self, envelope_id: str) -> None:
    if self._debounce_task is not None and not self._debounce_task.done():
        self._debounce_task.cancel()
    self._debounce_task = asyncio.create_task(self._fire_after_debounce())

async def _fire_after_debounce(self) -> None:
    try:
        await asyncio.sleep(0.05)  # 50ms — short enough to be imperceptible
    except asyncio.CancelledError:
        return
    # Fire one summary reflecting current state.
    ...
```

50ms is below human-perceptible latency for a chat ping but long enough to
collapse a burst of arrivals into one push. Configurable via endpoint
constructor param `notify_debounce_seconds=0.05`.

### Urgency on Envelope

Add a top-level field, schema-validated:

```python
# packages/core/src/agent_core/bus/envelope.py
class Envelope(BaseModel):
    ...
    urgency: Literal["green", "yellow", "red"] = "green"
```

Producer responsibilities:

| Producer | Default | Override conditions |
|---|---|---|
| `DiscordEndpoint` (inbound TextMessage) | green | red if content matches a configured regex (e.g., `(?i)\b(urgent|now|stop)\b`) — endpoint config |
| `SchedulerEndpoint` | green | red configurable per job in jobs.yaml |
| Future `BackupEndpoint` (failure) | red | always |
| Bus internal (sweep, dead-letter) | green | n/a |

`list_pending` ordering: red entries first (FIFO within tier), then yellow
(FIFO within tier), then green (FIFO). Tier breaks always win over arrival
time, matching Pepper's spec § 3.6.

### Same-sender batching at read time

`list_pending` grows a parameter:

```python
async def list_pending(batch_window_seconds: int = 0) -> list[dict]:
    ...
```

When `batch_window_seconds > 0`, consecutive envelopes from the same
`from_` whose `created_at` are within the window are merged into a single
returned group:

```json
[
  {
    "type": "single",
    "envelope": { id, from, to, kind, payload, ... }
  },
  {
    "type": "batch",
    "from": "agent-pepper",
    "kind": "TextMessage",
    "envelopes": [
      { id, payload, created_at, ... },
      { id, payload, created_at, ... },
      { id, payload, created_at, ... }
    ],
    "first_arrival": "...",
    "total_age_seconds": 78
  }
]
```

Merging is purely a read-time view. Each underlying envelope retains its
own id, persistence row, and ack semantics — `handle(envelope_id)` still
operates per-envelope. The agent receiving a batched group can either
process the messages as a unit and call `handle` on each, or process
individually. The bus state remains consistent either way.

Default `batch_window_seconds=0` preserves today's flat-list behavior so
existing callers (testbot's current usage) don't break.

### Multi-agent operation

The architecture above is **per-endpoint**. Each `ClaudeCodeMCPEndpoint`
instance has its own `_active_session`, its own `SessionRegistryMiddleware`,
its own `_pending` mailbox, its own debounce task, its own push pipeline.

To run multiple agents on one daemon:

```yaml
# ~/.agent-core/agent_core.yaml
endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-testbot
    params: { mount: /mcp/agent-testbot }

  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-pepper
    params: { mount: /mcp/agent-pepper }

  - class: agent_core_discord.endpoint.DiscordEndpoint
    name: discord-pepper
    params: { target: agent-pepper, token_env: DISCORD_PEPPER_TOKEN, ... }

  - class: agent_core_discord.endpoint.DiscordEndpoint
    name: discord-testbot
    params: { target: agent-testbot, token_env: DISCORD_TESTBOT_TOKEN, ... }
```

Each agent's Claude Code session connects to its own mount path. Push
notifications fire only on the endpoint that received the envelope (bus
routes by `envelope.to`). Each mailbox sorts by urgency independently.
Agents can publish envelopes to each other through the bus — `agent-pepper`
publishing to `agent-testbot` lands in testbot's mailbox.

---

## Verification step (in-spec, before lock-in)

The notification payload shape (`notifications/claude/channel` with
summary content rather than full message body) is **assumed** to wake the
connected Claude Code agent. Pepper's deployed channel server uses the
same method but with full-content payloads. We need to verify the summary
shape also wakes the agent before committing the implementation.

**Test:** Add a one-shot debug tool to `ClaudeCodeMCPEndpoint` (gated
behind a feature flag) called `_debug_emit_test_notification`. Call it
from the live testbot session. Observe:
- Does Claude Code surface the notification as a new turn?
- Does the agent see and parse the meta fields?

If yes: implement Shape B as designed. If no: fall back to Shape C
(hybrid — inline single-envelope content when only one is pending,
summary when multiple). The implementation cost difference is small;
the verification gates which path we take.

This verification is the first test in the implementation plan.

---

## Failure modes and recovery

**No active session.** `_notify_mail_arrived` is called, slot is None.
Drop silently. The mailbox still holds the envelope; whenever the agent
connects, it sees the queue via `list_pending`. No data loss.

**Session connected but write fails.** `send_message` raises during the
SSE write (network drop, server-side close, transport bug). Catch, log
WARN, clear `_active_session`. The mailbox still holds the envelope;
agent sees it on reconnect. No data loss; no retries; latency tax until
agent reconnects.

**Concurrent session attempt.** Second client connects to the same mount
path. `_register_session` raises. The middleware swallows the raise (or
re-raises through MCP error semantics — implementation detail). The
existing session keeps working. Operator sees the failure in logs.

**Push debounce race during shutdown.** `_fire_after_debounce` is mid-sleep
when `stop()` is called. The task is cancelled by the daemon shutdown
sweep. No notification fires. Mailbox state on disk persists. Agent
sees on next start.

**Burst exceeds mailbox cap.** Unchanged from today: bus enforces
`max_pending_per_endpoint` (default 10,000); `MailboxFull` raised at
publish time. Producer's responsibility to handle. Push notifications
themselves don't have a queue; debounce naturally absorbs bursts.

---

## Testing strategy

### Unit tests

Mirror the scheduler / Discord pattern:

- **`test_session_registry_middleware`**: middleware captures session ref
  on first message; stashes in endpoint slot; clears on cancellation of
  the spawned `_claim_session` task.
- **`test_session_collision_refused`**: second concurrent session attempt
  raises; original session keeps slot.
- **`test_notify_mail_arrived_no_session`**: drop silently when no
  session connected; no exception, no log error.
- **`test_notify_mail_arrived_write_failure`**: simulated `send_message`
  raise; slot is cleared; envelope remains in mailbox.
- **`test_debounce_coalesces_burst`**: 5 arrivals within 30ms produce
  one notification; summary reflects all 5.
- **`test_envelope_urgency_field`**: schema validates the literal;
  default is `"green"`; producers can override.
- **`test_list_pending_orders_by_urgency`**: red first, yellow next,
  green last, FIFO within tier.
- **`test_list_pending_batch_window`**: 3 envelopes from same `from_`
  within 30s window collapse into one batched group; default 0 returns
  flat list (regression safety for existing testbot calls).
- **`test_batched_envelopes_still_individually_ackable`**: `handle()`
  on one envelope id within a batch removes only that one.

Total estimate: ~25 new unit tests.

### Integration tests

- **`test_real_mcp_session_receives_push`**: spin up a real
  `ClaudeCodeMCPEndpoint` with FastMCP, connect a real MCP client (the
  `mcp` package's `Client`), wait for a published envelope, observe
  the notification arrives on the SSE stream within ~100ms.
- **`test_session_lifecycle_cleanup`**: same setup; close the client;
  verify `_active_session` clears within ~1s.

These run alongside the existing `tests/test_bus_daemon_integration.py`
and don't need real Claude Code in-the-loop — the `mcp` Python client
is enough to exercise the push path.

### Live testbot validation

Same pattern as PR #8 (Discord) and PR #7 (Scheduler):

1. Drop new config into `~/.agent-core/agent_core.yaml` (one new
   `agent-testbot-2` endpoint mounted at `/mcp/agent-testbot-2` for
   side-by-side comparison without disturbing the live testbot).
2. Restart daemon.
3. From the testbot Claude Code session, run a 5-step validation:
   - **Step 1:** Send a test envelope to `agent-testbot-2` via the bus.
     Confirm a `notifications/claude/channel` notification arrives and
     wakes the agent.
   - **Step 2:** Send 3 envelopes from same source within 50ms.
     Confirm one summary notification fires (debounce works).
   - **Step 3:** Send envelopes with mixed urgencies. Confirm
     `list_pending` returns red-first.
   - **Step 4:** Send 3 envelopes from same source within 30s.
     Confirm `list_pending(batch_window_seconds=30)` returns one batched
     group.
   - **Step 5:** Disconnect agent's session, send envelope while
     disconnected, reconnect. Confirm `list_pending` shows the
     stale-but-undelivered envelope; no push lost (mailbox is
     authoritative).

Each step has explicit visual / structural assertions. PASS gate before
merge, same as PR #8.

---

## Open questions deferred to plan / implementation

- **Notification handler instructions:** the endpoint's FastMCP server
  carries `instructions=` text that's surfaced to the connecting client.
  The current `ClaudeCodeMCPEndpoint` instructions don't mention
  notifications; we need to add language explaining the
  `notifications/claude/channel` shape and what to do on receipt
  (call `list_pending`, etc.). Pepper's instructions text is a good
  reference template (`pepper/channel/server.py:355-366`).
- **Custom regex for Discord urgency:** the override rules table mentions
  matching content for "URGENT" / "now" / "stop" — exact regex and case
  rules are config knobs. Default to a sensible regex; expose as
  `urgency_red_regex` constructor param.
- **Backwards compatibility for the bus envelope schema change:**
  adding a `urgency` field with a default doesn't break existing
  producers, but persisted SQLite envelopes from before this change
  won't have the field. The Persistence layer's `model_validate_json`
  on row read should accept missing-and-default-to-green silently.
  Confirm the existing roundtrip.

---

## What this is not

- It's not the heartbeat-checker endpoint (deferred; in BACKLOG).
- It's not Discord adapter v2 features (polls, threads, scheduled events;
  deferred; sub-project E v2).
- It's not multi-instance fanout. One endpoint = one logical agent slot.
- It's not a replacement for `list_pending` polling. Push is best-effort;
  polling is authoritative.
- It's not a distributed-worker push system. We're single-process.

---

## Summary

Three connected changes, all consumer-side, all on `ClaudeCodeMCPEndpoint`
plus one schema field on `Envelope`:

1. **Session registry + push notification** via FastMCP middleware,
   mirroring `PingMiddleware`'s task-group pattern. Best-effort, polling
   is the failsafe.
2. **Urgency field on Envelope**, schema-validated, ordered in `list_pending`.
3. **Same-sender batching** at `list_pending` read time via optional window
   parameter.

Estimated effort: ~3 days. Final gate is live testbot validation across
five scenarios, mirroring the discipline that caught the `embeds=None` bug
in PR #8.

Producer-side suppression of routine traffic (the heartbeat work) lives
in its own sub-project per the BACKLOG.
