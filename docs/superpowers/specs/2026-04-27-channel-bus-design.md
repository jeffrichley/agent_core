# Channel Bus — Design Spec

**Date:** 2026-04-27
**Status:** Approved

## Overview

A unified, in-process message bus for agent_core that handles all ingress
(channels), egress (channels), and inter-agent communication through a
single abstraction. Every participant — Discord, scheduler, HTTP webhook,
sensor stream, Claude Code agent, embedded SDK loop — is an `Endpoint`
addressable by a stable name. Endpoints exchange `Envelope`s through a
`Bus` that durably persists them, dispatches by recipient name, and
provides at-least-once delivery with endpoint-side idempotency.

The design is deliberately small. The bus does the smallest thing that
works: named-recipient unicast routing, durable mailboxes, push delivery
when endpoints are live, two-stage hook pipeline. Everything richer —
priority, fanout, cancellation primitives, multi-host federation — lives
above the bus, inside specific adapters or hooks.

This spec defines v1: the bus core, the `Endpoint` Protocol, the
`Envelope` schema, the persistence layer, configuration, the security
floor, and the explicit set of features deferred to later phases.

## Architectural Shape

### The unified-endpoint model

There is no architectural distinction between "channel" and "agent."
Both are `Endpoint`s on the bus. A Discord channel adapter is an endpoint
named `discord`. A Claude Code instance is an endpoint named
`agent-pepper`. The bus routes between them by `to:` field, knowing
nothing about whether the recipient is a chat channel or an LLM.

```
  ┌──────────────────── Bus (in-process router) ─────────────────────┐
  │                                                                  │
  │   Durable mailboxes (SQLite-backed)                              │
  │   ┌──────────────┐ ┌──────────────┐ ┌────────────┐ ┌───────────┐ │
  │   │ agent-pepper │ │ agent-deb    │ │ discord    │ │ scheduler │ │
  │   └──────────────┘ └──────────────┘ └────────────┘ └───────────┘ │
  │                                                                  │
  └──┬─────────────┬──────────────┬───────────────┬──────────┬───────┘
     ▼             ▼              ▼               ▼          ▼
  ┌─────┐      ┌─────┐         ┌─────┐        ┌──────┐   ┌────────┐
  │Disc │      │Sched│         │HTTP │        │MCP CC│   │OwnTracks│
  │adptr│      │adptr│         │adptr│        │adptr │   │adptr   │
  └─────┘      └─────┘         └─────┘        └──────┘   └────────┘
```

This collapse buys two things. First, inter-agent communication is free:
agent-pepper sends an envelope `to: agent-deb` through the same machinery
Discord uses to reach agent-pepper. No separate inter-agent subsystem.
Second, buffering falls out of the design naturally: every endpoint has
a durable mailbox, so "Claude Code is not running yet" is the same case
as "Discord WebSocket dropped" — mail queues, drains on reconnect.

### Three core types

- **`Envelope`** — the wire format. Stable JSON shape with `from`, `to`,
  `correlation_id`, `kind`, `payload`, `metadata`, optional `expires_at`.
- **`Endpoint`** — a Protocol any adapter satisfies: a name, an async
  `start` / `deliver` / `stop`. Discord, scheduler, MCP-Claude-Code,
  HTTP, OwnTracks — all the same Protocol.
- **`Bus`** — registers endpoints, holds per-endpoint durable mailboxes,
  dispatches by `to:`. Operations: `register`, `publish`, `ack`, `nack`,
  plus operator-facing CLI commands.

### What the bus does NOT do

This omission list is load-bearing. It is the discipline that lets the
bus stay small while ingress and egress sources keep being added.

| Not in the bus | Lives here instead |
|---|---|
| Routing by type, role, or topic | Adapter-side logic; future router endpoints |
| Priority queues | Consumer adapter (e.g., MCP `list_pending` lets the agent pick order) |
| Pub/sub fanout | Future `events` endpoint adapter |
| Cancellation as a primitive | `Cancellation` envelope kind; advisory only |
| Bus-level rate limiting | `pre_deliver` hook |
| Bus-level authn/authz | Endpoints declared in YAML are trusted; `pre_publish` ACL hook (Phase 2) refines this |
| Cross-host federation | Single-process bus; revisit when the second host appears |
| Exactly-once delivery | At-least-once + endpoint idempotency |
| Timeout enforcement on agent thinking | Long work uses `Progress` envelopes |
| Push notification of new mail to endpoints | Bus calls `deliver()` directly when envelopes arrive |

### Process model

agent_core is one long-lived Python process. The bus, all endpoint
adapters, all endpoint state, and the SQLite mailbox file all live
inside it. There is no separate broker process, no Redis, no NATS, no
Kafka. The runtime starts via `agent-core bus run`, reads
`agent_core.yaml`, instantiates and starts each endpoint, and drives
one asyncio event loop.

External agents and external services live in their own processes and
talk to agent_core through an in-process **adapter** that owns the IPC.
From the bus's perspective, the adapter *is* the endpoint; whatever IPC
the adapter uses (MCP over HTTP, a Discord WebSocket, an APScheduler
event, a webhook POST) is the adapter's private business.

```
┌────────────────── agent_core process (one) ─────────────────────────┐
│  ┌── Bus ──┐                                                        │
│  │ SQLite  │                                                        │
│  │mailboxes│                                                        │
│  └────┬────┘                                                        │
│       │                                                             │
│  Endpoint adapters (Python objects in this process):                │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────────────────┐     │
│  │ Discord  │ │ Scheduler│ │ ClaudeCodeMCPEndpoint            │     │
│  │ adapter  │ │ adapter  │ │   name=agent-pepper              │     │
│  │          │ │          │ │   mount=/mcp/agent-pepper        │     │
│  │          │ │          │ │ (FastMCP server on shared host)  │     │
│  └─────┬────┘ └──────────┘ └────────────────┬─────────────────┘     │
│        │ websocket                          │ MCP over HTTP/SSE      │
└────────┼────────────────────────────────────┼─────────────────────-─┘
         ▼                                    ▼
   ┌──────────┐                       ┌──────────────────────┐
   │ Discord  │                       │ Claude Code process  │
   │ servers  │                       │  (the Pepper agent)  │
   └──────────┘                       └──────────────────────┘
```

For multiple agents on the same bus, there is one
`ClaudeCodeMCPEndpoint` adapter per agent (each with its own `name` and
`mount` path), all hosted on a single shared HTTP server inside the
agent_core process. Each Claude Code instance connects to its own URL
path and that path *is* the agent's identity:

```
agent_core process (same one)
   ClaudeCodeMCPEndpoint(name=agent-pepper, mount=/mcp/agent-pepper) ←─MCP─→ [CC: Pepper]
   ClaudeCodeMCPEndpoint(name=agent-deb,    mount=/mcp/agent-deb)    ←─MCP─→ [CC: Deb]
```

Process separation is what makes the durable mailbox semantics
meaningful: when a Claude Code instance dies or restarts, mail
addressed to its name keeps queuing in the bus's SQLite-backed mailbox.
When agent_core itself restarts, all mailboxes survive (state is on
disk) and in-flight envelopes redeliver. Co-locating the agent runtime
inside agent_core would invalidate this guarantee.

## The Envelope

Envelopes are fully-typed Pydantic models. Nothing on the envelope is a
freeform `dict` except the nested domain payload of `Event` kinds.

```python
from datetime import datetime
from typing import Annotated, Any, Literal
from pydantic import BaseModel, Field


class Envelope(BaseModel):
    id: str                          # uuid; unique per envelope
    correlation_id: str              # threads related envelopes
    in_reply_to: str | None = None   # optional pointer to specific envelope id
    from_: str = Field(default="", alias="from")  # bus-stamped at publish time (see Security)
    to: str                          # endpoint name (single recipient only)
    kind: Literal[
        "TextMessage", "Event", "ToolInvocation",
        "Cancellation", "Progress", "Acknowledgment",
    ]
    payload: "EnvelopePayload"       # discriminated union on `kind`
    metadata: dict[str, Any] = {}    # adapter-private; never load-bearing for routing
    expires_at: datetime | None = None
    created_at: datetime
```

### The `kind` discriminator and payload models

Each `kind` has its own structured payload model:

```python
class TextMessagePayload(BaseModel):
    kind: Literal["TextMessage"] = "TextMessage"
    text: str
    attachments: list["Attachment"] = []


class EventPayload(BaseModel):
    kind: Literal["Event"] = "Event"
    type: str                        # domain discriminator: "location", "sleep", "github_push", ...
    schema_version: str = "1"
    data: dict[str, Any]             # domain-specific schema; bus does not validate


class ToolInvocationPayload(BaseModel):
    kind: Literal["ToolInvocation"] = "ToolInvocation"
    tool: str
    args: dict[str, Any]


class CancellationPayload(BaseModel):
    kind: Literal["Cancellation"] = "Cancellation"
    reason: str | None = None


class ProgressPayload(BaseModel):
    kind: Literal["Progress"] = "Progress"
    status: Literal["working", "blocked", "complete"]
    note: str | None = None
    percent: float | None = None


class AcknowledgmentPayload(BaseModel):
    kind: Literal["Acknowledgment"] = "Acknowledgment"
    of: str                          # envelope id this acknowledges
    note: str | None = None


EnvelopePayload = Annotated[
    TextMessagePayload | EventPayload | ToolInvocationPayload
    | CancellationPayload | ProgressPayload | AcknowledgmentPayload,
    Field(discriminator="kind"),
]
```

This is a two-tier discriminator. The top-level `kind` is a closed
structural set the bus and all adapters know. For `kind: Event`, the
payload's `type` field is open-ended — domain events grow without
touching the bus or its vocabulary.

### Single recipient

`to:` is always a single endpoint name, never a list. Multi-recipient
ergonomics live at the publishing API:

```python
async def publish(self, envelope: Envelope, to: str | list[str] | None = None) -> None
```

When `to` is a list, the bus mints N envelopes (each with its own `id`,
sharing `correlation_id`) and stores N rows. On the wire and in the
mailbox it is always single-recipient. This keeps per-envelope ack state
simple, makes failure semantics unambiguous, and keeps trace output
clean.

### TTL

`expires_at: datetime | None` is an optional per-envelope TTL. Default
`None` (no expiry). When set, the periodic TTL sweep transitions
expired-and-undelivered envelopes to `state='expired'` and logs. There
is no notification or callback on expiry — that is policy and would
bloat the bus.

Publishers set TTL when payload value is time-bound (a `Progress`
envelope from a long-running task; a 3pm calendar reminder; a
`LocationEvent`). Conversational messages typically have no TTL.

### `metadata` discipline

`metadata` is for adapter-private breadcrumbs (`discord_message_id`,
trace context) and is never inspected for routing. The rule: if removing
a `metadata` field breaks delivery, that field is in the wrong place.
Routing keys live on `to:`, identity lives on `from:`, threading lives
on `correlation_id` / `in_reply_to`.

## The Endpoint Protocol

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class Endpoint(Protocol):
    """An addressable participant on the bus."""

    name: str  # stable identity used for `to:` / `from:` routing

    async def start(self, bus: "BusHandle") -> None:
        """Bus is ready. Open connections, register listeners, start your loop."""

    async def deliver(self, envelope: "Envelope") -> None:
        """Bus is delivering an envelope addressed to you.

        You MUST eventually call bus.ack(envelope.id) when handling completes.
        Raising EndpointUnavailable signals temporary failure; the bus will
        pause delivery to this endpoint and retry on backoff. Other exceptions
        are terminal — the envelope is moved to the dead-letter mailbox.
        """

    async def stop(self) -> None:
        """Graceful shutdown. Close connections, flush state."""
```

The Protocol is `@runtime_checkable` — same DX as the existing
`HookTool` Protocol. No required base class, no inheritance, no
framework registration calls.

### `BusHandle`

The handle is what an endpoint uses to talk back to the bus. It is bound
to the endpoint at construction; the endpoint can never spoof another.

```python
class BusHandle(Protocol):
    async def publish(self, envelope: "Envelope",
                      to: str | list[str] | None = None) -> None:
        """Send an envelope. Bus stamps `from:` to this endpoint's name,
        persists, then delivers to envelope.to."""

    async def ack(self, envelope_id: str) -> None:
        """Confirm successful handling of a delivered envelope. Idempotent."""

    async def nack(self, envelope_id: str, requeue: bool = True) -> None:
        """Reject a delivered envelope. requeue=True schedules redelivery."""

    def endpoints(self) -> list["EndpointInfo"]:
        """Snapshot of currently-registered endpoints (name + description).
        Used by consumer adapters to surface discovery to their agents."""


class EndpointInfo(BaseModel):
    name: str
    description: str = ""
```

That is the entire surface available to endpoints. Endpoints never see
other endpoints' mailboxes, other endpoints' adapters, or bus
internals. They see a name + description directory through
`endpoints()`, and that is all the topology information they need.

### Endpoint discovery

Agents need to know who they can address. The bus already knows the
registered endpoint set — it just instantiated them all from YAML.
Discovery is a metadata view of that set, surfaced to whoever needs it.

**Static metadata in YAML.** Every endpoint declaration may carry an
optional `description: str` field. Operator-authored prose that says
what the endpoint is for. There is no schema language, no capability
matrix, no JSON-RPC service descriptor — just a sentence or two
describing the endpoint.

```yaml
endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-deb
    description: "Research-focused agent. Deep web research, source comparison, citations."
    params: { ... }
```

**Bus-level access.** `BusHandle.endpoints()` returns the snapshot
described above. The set is fixed at boot (registration is YAML-only;
see § Endpoint Protocol > Registration).

**Surface in the agent's idiom.** Each consumer adapter exposes the
directory in whatever shape its agent runtime expects. For
`ClaudeCodeMCPEndpoint`, that means MCP tools that Claude can call:

```
list_endpoints()
  → [{"name": "agent-deb", "description": "Research-focused agent..."},
     {"name": "discord",   "description": "Bridges Discord channels..."},
     ... ]

describe_endpoint(name="agent-deb")
  → {"name": "agent-deb", "description": "Research-focused agent..."}

send(to="agent-deb", correlation_id="c2", kind="TextMessage",
     payload={"text": "Look up Jeff's preferred slot..."})
  → publishes the envelope; bus stamps `from:` automatically
```

Agents never hardcode endpoint names. They call `list_endpoints()`,
read descriptions, decide who to address, and send. Adding a new agent
is "edit YAML with name + description, restart" — every other agent
sees it on its next discovery call.

**What this design deliberately omits:**

- **Capability schemas / RPC IDLs.** No "agent-deb accepts these payload
  shapes." Description is prose. Agents reason about descriptions; they
  do not statically validate capability matrices.
- **Liveness flags in the directory.** `endpoints()` returns the
  *registered* set, not the *currently connected* set. The bus already
  buffers when an endpoint is offline; the sender does not need to
  know. (Easy to add a `live: bool` flag later if a use case appears.)
- **Per-endpoint metadata beyond description.** No tags, no roles, no
  versions. If you need that information, put it in the description.

The set of addressable endpoints is operator-controlled. Agents can
only reach what is in YAML. They can discover only what the operator
declared. The directory is the agent's world; the operator owns the
world.

### Why `deliver()` does not auto-ack

For some endpoints, "delivered" and "handled" coincide — Discord adapter
posts to the channel inside `deliver()` and acks before returning. For
others they are separated in time — the Claude-Code-MCP adapter queues
the envelope inside `deliver()`, returns immediately, and acks only when
Claude actually drains the envelope via MCP tools.

Explicit ack handles both cleanly. The bus tracks "in-flight" envelopes
(delivered but unacked) with a configurable redelivery timeout, so an
endpoint crashing mid-handle gets the envelope back on next start.

This produces **at-least-once delivery + endpoint-side idempotency**.
Endpoints dedupe by tracking the last seen envelope id, an LRU cache of
seen ids, or by handlers that are intrinsically idempotent. The bus does
not promise exactly-once.

### Lifecycle

1. **Boot.** Bus reads `agent_core.yaml`, instantiates each endpoint
   class with declared `params`.
2. **Start.** Bus calls `start(bus_handle)` on each endpoint. After it
   returns, the endpoint is live.
3. **Drain.** Bus replays persisted-but-undelivered envelopes for this
   endpoint via `deliver()`, in arrival order.
4. **Steady state.** Bus calls `deliver()` for each new addressed
   envelope. Endpoint calls `bus_handle.publish()` to send.
5. **Stop.** On shutdown, bus calls `stop()` in reverse-registration
   order. In-flight envelopes (delivered but unacked) are redelivered on
   next boot.

### Connection-state policy

When an endpoint's upstream drops (Discord WS disconnect, MCP client
disconnect), there are two valid patterns and the Protocol does not
pick:

- **Raise `EndpointUnavailable` from `deliver()`.** Bus pauses delivery,
  queues mail in the mailbox, retries on backoff. Once `deliver()`
  succeeds, drains in arrival order.
- **Internally queue inside `deliver()`.** Endpoint always accepts; ack
  is deferred until the downstream consumer actually handles. Bus
  considers it delivered.

Discord-style adapters typically use the first pattern; MCP-style
adapters typically use the second.

### Hook stages

Two pluggable pipeline stages, declared in YAML the same way the
existing `HookTool` registrations work:

- **`pre_publish`** — fires when an endpoint calls `bus.publish()`.
  Hooks can inspect, log, redact, drop, or modify. Transcript writers
  live here.
- **`pre_deliver`** — fires before the bus calls `endpoint.deliver()`.
  Last-mile rate limiting, dedupe, transformation.

```python
@runtime_checkable
class BusHook(Protocol):
    async def execute(self,
                      stage: Literal["pre_publish", "pre_deliver"],
                      envelope: "Envelope",
                      params: dict) -> "Envelope | None":
        """Return the (possibly modified) envelope to continue.
        Return None to drop the envelope.
        Raising aborts the operation and surfaces an error to the caller."""
```

Hooks are never where routing logic lives. Routing is by `to:`.

### Registration is YAML-only

No runtime `register_endpoint()` API. Adding an endpoint = edit YAML,
restart runtime. Hot-plug is YAGNI; revisit when there is an actual
reason.

### Dead-letter mailbox

When `deliver()` raises a non-`EndpointUnavailable` exception, or when
an envelope exceeds `max_delivery_attempts`, it transitions to
`state='dead_letter'`. Operators inspect and replay via CLI; nothing is
ever silently dropped from a real failure.

## The Bus

### What the bus exposes

**To endpoints (the `BusHandle`):** `publish`, `ack`, `nack`. Nothing
more.

**To operators (CLI):**

```
agent-core bus run                       # start bus + endpoints
agent-core bus stop                      # graceful shutdown
agent-core bus status                    # endpoints, in-flight count, dlq depth
agent-core bus mailbox <endpoint>        # list pending envelopes for endpoint
agent-core bus trace <correlation_id>    # full thread of envelopes
agent-core bus dlq                       # list dead-letter envelopes
agent-core bus replay <envelope_id>      # re-queue a dead-letter envelope
agent-core bus dlq purge --older-than 7d
```

**To other Python code:** nothing public. The `Bus` class is private.
The only legitimate way to publish is from a registered endpoint via its
`BusHandle`. This is the discipline that keeps `from:` provenance
trustworthy — every envelope's origin is a registered endpoint, never
an ad-hoc caller.

### Persistence: one SQLite table

Single table; each envelope has 1:1 delivery state because `to:` is
single-recipient.

```sql
CREATE TABLE envelopes (
    id              TEXT PRIMARY KEY,         -- uuid
    correlation_id  TEXT NOT NULL,
    in_reply_to     TEXT,
    from_endpoint   TEXT NOT NULL,
    to_endpoint     TEXT NOT NULL,
    kind            TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    expires_at      TIMESTAMP,
    created_at      TIMESTAMP NOT NULL,

    state           TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending','in_flight','acked','dead_letter','expired')),
    delivery_count  INTEGER NOT NULL DEFAULT 0,
    last_attempted  TIMESTAMP,
    in_flight_until TIMESTAMP,
    nack_reason     TEXT
);

CREATE INDEX idx_envelopes_to_state    ON envelopes(to_endpoint, state, created_at);
CREATE INDEX idx_envelopes_correlation ON envelopes(correlation_id);
CREATE INDEX idx_envelopes_expires     ON envelopes(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX idx_envelopes_in_flight   ON envelopes(in_flight_until) WHERE state='in_flight';
```

Storage path is `~/.agent-core/bus.sqlite` (configurable via
`bus.storage_path`). **Nothing is ever deleted in the hot path** — only
`state` updates. Acked envelopes stay queryable for `acked_retention_days`
(default 14), then a periodic archive job moves them to
`bus-archive.sqlite`. This is what makes
`agent-core bus trace <correlation_id>` work even on completed
conversations.

### State machine

```
                          deliver()
   ┌─────────┐  ───────────────────▶  ┌────────────┐    ack()    ┌────────┐
   │ pending │                        │ in_flight  │ ──────────▶ │ acked  │
   └─────────┘  ◀── nack(requeue) ─── └────────────┘             └────────┘
        │                                   │
        │   expires_at < now                │  deliver() raises non-EU
        │                                   │  OR delivery_count > max
        ▼                                   ▼
   ┌─────────┐                        ┌──────────────┐
   │ expired │                        │ dead_letter  │ ◀── nack(no requeue)
   └─────────┘                        └──────────────┘
```

Transitions only happen via the bus. Endpoints drive `ack` / `nack`;
periodic sweeps move expired and timed-out rows.

### Dispatch flow

`bus.publish(envelope)`:

1. **Stamp `from:` to the calling endpoint's bound name** (security
   primitive — see Security). This happens before any hook fires so
   hooks see authenticated provenance.
2. Run `pre_publish` hooks (mutate, log, or drop).
3. Persist row with `state='pending'`.
4. If `envelope.to` is registered, live, and not paused: `_dispatch`.
5. Otherwise leave in mailbox; drain when the endpoint comes live.

`_dispatch(envelope)`:

1. Run `pre_deliver` hooks.
2. Update row: `state='in_flight'`, `delivery_count += 1`,
   `last_attempted = now`, `in_flight_until = now + redelivery_timeout`.
3. Call `endpoint.deliver(envelope)`.
4. On `EndpointUnavailable` → revert `state='pending'`, mark endpoint
   paused, schedule retry on backoff.
5. On any other exception → `state='dead_letter'`, log with traceback.
6. On clean return → leave `state='in_flight'`, wait for endpoint to
   ack/nack.

Both `ack` and `nack` are idempotent. Double-ack from a flaky MCP
connection is a no-op.

### Sweeps

Two periodic background tasks running on the bus's loop:

- **TTL sweep** (default cadence 60s): rows with `expires_at < now AND
  state IN ('pending','in_flight')` → `state='expired'`. Logged, no
  notification.
- **Redelivery sweep** (default cadence 10s): rows with `state='in_flight'
  AND in_flight_until < now`:
  - `delivery_count < max_delivery_attempts` (default 5) →
    `state='pending'`, will be re-dispatched.
  - Else → `state='dead_letter'`.

The default `redelivery_timeout` is **5 minutes** — generous on purpose.
This is crash-recovery, not "the agent took too long to think." Long
work uses `Progress` envelopes; the bus does not police agent latency.

All four cadence/timeout values are configurable in `agent_core.yaml`
under the `bus:` block.

### Concurrency model

Single asyncio event loop. SQLite via `aiosqlite` in WAL mode — single
writer connection, shared reads. Endpoints run as asyncio tasks on the
same loop.

**No threads, no multiprocess, no user-visible locks.** If an endpoint
needs heavy CPU work (LLM inference, embeddings, file hashing), the
adapter spawns its own thread or subprocess — that is the adapter's
problem, not the bus's. The bus stays single-loop.

### Dead-letter mailbox

Dead-letter rows stay in the same table (`state='dead_letter'`).
Operators interact via the CLI commands above. There is no automatic DLQ
expiry; terminal failures should be looked at, and the operator decides
when. The DLQ is small by construction.

## Configuration & Wiring

### YAML extension

The existing `agent_core.yaml` gets three new top-level blocks. The
existing `pipelines` block (HookTool registrations for Claude Code
lifecycle events) is unchanged.

```yaml
# Bus runtime settings — all optional, sensible defaults
bus:
  storage_path: ~/.agent-core/bus.sqlite
  redelivery_timeout_seconds: 300
  max_delivery_attempts: 5
  ttl_sweep_seconds: 60
  redelivery_sweep_seconds: 10
  acked_retention_days: 14
  max_pending_per_endpoint: 10000

# HTTP host shared by all MCP endpoint adapters
http:
  bind_host: 127.0.0.1
  bind_port: 8788

# Endpoints — addressable, named, instantiated at boot.
# `description` is operator-authored prose surfaced to other agents
# via the discovery API.
endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-pepper
    description: "Chief-of-staff agent. Owns calendar, projects, people; routes work."
    params:
      mount: /mcp/agent-pepper

  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-deb
    description: "Research-focused agent. Deep web research, source comparison, citations."
    params:
      mount: /mcp/agent-deb

  - class: agent_core.endpoints.discord.DiscordEndpoint
    name: discord
    description: "Bridges Discord channels. Use for user-facing replies."
    params:
      token_env: DISCORD_BOT_TOKEN
      routing:
        "1234567890":            # ops channel
          recipients: [agent-pepper, agent-deb]
        "9876543210":            # general
          recipients: [agent-pepper]

  - class: agent_core.endpoints.scheduler.SchedulerEndpoint
    name: scheduler
    description: "Fires scheduled prompts on cron/interval. Send envelopes here to add jobs."
    params:
      jobs_path: ./jobs.yaml

# Bus pipeline hooks — fire on pre_publish / pre_deliver
bus_hooks:
  pre_publish:
    - class: agent_core.bus_hooks.transcript.TranscriptWriter
      params:
        output_dir: ~/.agent-core/transcripts/
  pre_deliver: []

# Existing — Claude Code lifecycle hooks, untouched
pipelines:
  SessionStart:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector
      params: { format: "%A, %B %d, %Y %I:%M %p %Z" }
```

The shape mirrors the existing `pipelines` block: fully-qualified class
path + `params` dict. Same import-by-string pattern. Same
`@runtime_checkable` Protocol verification at load time.

The `bind_host` defaults to `127.0.0.1` deliberately: v1 ships
loopback-only. Binding to other interfaces (`0.0.0.0`, a LAN IP)
requires the auth/TLS items in BACKLOG to be implemented first; the
runner refuses to start if `bind_host` is non-loopback and no auth hook
is configured.

### Module layout

```
src/agent_core/
├── models.py                 (existing — extended with Envelope + payload models)
├── cli.py                    (existing — extended with `bus` subcommands)
├── hooks/                    (existing — untouched)
│   └── ...
├── bus/                      (new)
│   ├── __init__.py
│   ├── core.py               (Bus class, dispatch loop, sweeps)
│   ├── handle.py             (BusHandle — what endpoints get)
│   ├── persistence.py        (SQLite layer; aiosqlite)
│   ├── protocol.py           (Endpoint, BusHook protocols + EndpointUnavailable)
│   └── runner.py             (boot sequence: load YAML → instantiate → start)
├── endpoints/                (new — built-in adapters)
│   ├── __init__.py
│   ├── claude_code_mcp.py
│   ├── discord.py
│   ├── scheduler.py
│   ├── http.py
│   └── stub.py               (test/echo endpoint)
└── bus_hooks/                (new — built-in pre_publish / pre_deliver hooks)
    ├── __init__.py
    └── transcript.py
```

### CLI surface

The existing `agent-core hooks run` subcommand stays. New top-level
group `agent-core bus` (commands listed above under "What the bus
exposes").

`agent-core bus run` is the primary entry point — the long-running
process that hosts the bus and all endpoints. Operators run it under
systemd / launchd / a shell loop, however they prefer.

### MCP transport implementation

`ClaudeCodeMCPEndpoint` uses **HTTP/SSE only** (no stdio support in
v1). Identity is path-based: each adapter mounts at `/mcp/<name>` on
the shared HTTP host, and each Claude Code instance configures its
`.mcp.json` to connect to its specific URL.

```jsonc
// .mcp.json for Pepper's Claude Code instance
{
  "mcpServers": {
    "agent-core": {
      "type": "http",
      "url": "http://localhost:8788/mcp/agent-pepper"
    }
  }
}
```

Implementation libraries (declared in `pyproject.toml`):

- **FastMCP** (`/prefecthq/fastmcp`, pinned to `^3.2`) — handles MCP
  protocol details: initialize handshake, tool registration with
  schema generation, server-pushed notifications, session management.
  Each `ClaudeCodeMCPEndpoint` instance owns one `FastMCP` server.
- **Starlette** — ASGI host. The bus's HTTP server is one Starlette
  app; each endpoint adapter contributes its FastMCP ASGI app via
  `Mount("/mcp/<name>", app=mcp.http_app(path="/"))`.
- **Uvicorn** — ASGI runner driving the Starlette app on the bus's
  asyncio event loop.

Tools each `ClaudeCodeMCPEndpoint` exposes to its connected Claude
Code instance:

- `send(to, kind, payload, correlation_id?, in_reply_to?, metadata?, expires_at?)`
- `list_endpoints() → [{name, description}]`
- `describe_endpoint(name) → {name, description}`
- `list_pending() → [envelope]` — drains the agent's mailbox snapshot
- `handle(envelope_id) → ack` — convenience wrapper around `ack`
- `ack(envelope_id)`, `nack(envelope_id, requeue?)`

Inbound envelopes flow to Claude Code via MCP **notifications** on
the SSE stream. The adapter holds the active session and pushes
notifications as they arrive at the agent's mailbox. If no session is
connected, mail queues at the bus per the durable-mailbox semantics.

**Why no stdio:** the bus is a long-lived shared service hosting
multiple agents simultaneously. stdio MCP is a parent-child subprocess
relationship — exactly one parent per server. Supporting it would
require either spawning a separate agent_core per Claude Code (defeats
the bus) or running an stdio-bridge subprocess per agent (adds a
process and a transport with no benefit). HTTP/SSE on loopback is the
correct shape for this design.

## Security

The bus is a localhost trust domain. Endpoints declared in
`agent_core.yaml` are trusted by virtue of being declared. The bus
provides one core security primitive in v1; everything else is opt-in
hooks (Phase 2, captured in BACKLOG).

### Bus-stamped `from:`

This is the load-bearing primitive. The bus constructs a fresh
`BusHandle` for each endpoint at registration time, with the endpoint's
declared name immutably bound inside it. The bus then passes that
handle to the endpoint via `start(bus_handle)`. When the endpoint
calls `publish()` on its handle, the bus overwrites `envelope.from_`
to the bound name, ignoring whatever the caller put there.

```python
class BusHandle:
    def __init__(self, bus: "Bus", endpoint_name: str):
        self._bus = bus
        self._endpoint_name = endpoint_name  # immutable, set at endpoint registration

    async def publish(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        envelope = envelope.model_copy(update={"from_": self._endpoint_name})
        await self._bus._enqueue(envelope, to)
```

Consequences:

- An endpoint cannot impersonate another endpoint, by mistake or on
  purpose.
- `from:` is trustworthy provenance — hooks, future ACL rules, audit
  trails, and agent reasoning can all rely on it.
- Endpoints do not need to know their own name; `BusHandle` knows. The
  envelope's `from_` field defaults to `""`; the bus overwrites it on
  every publish, so endpoints can leave it unset.

### Structural defaults

- **`bus.sqlite` and its directory created with restrictive permissions**
  (0600 on POSIX; equivalent ACL on Windows — owner only). Bus refuses to
  start if existing files have broader permissions, with a clear error
  message.
- **Per-endpoint mailbox cap** (`bus.max_pending_per_endpoint`, default
  10000). Once full, new publishes addressed to the endpoint fail with
  `MailboxFull`.
- **`from:` and `to:` are validated** against the registered endpoint
  set. `bus.publish()` rejects envelopes addressed to unregistered
  endpoints with a clear error.

### What is the agent's responsibility, not the bus's

**Prompt injection from envelope content is not a bus problem.** The
bus carries bytes; the agent runtime decides what to obey. The
discipline that protects you:

- Treat all envelope content as untrusted user input regardless of
  `from:`. A trustworthy `from:` tells you who handled the envelope
  last; it does not tell you the content is trustworthy.
- System prompts and operator instructions live in the agent's prompt
  template, never in incoming envelope payloads. Payloads are data.
- Provenance + audit are how you recover. `agent-core bus trace
  <correlation_id>` shows the exact envelope chain when something goes
  wrong.

The bus's contribution to prompt-injection defense is clean immutable
provenance and a complete audit trail. It does not — and cannot — solve
prompt injection.

### What is operator responsibility

- `agent_core.yaml` is power. Treat like an SSH config: file
  permissions, version control, review.
- Adapter secrets live in env vars referenced from YAML
  (`token_env: DISCORD_BOT_TOKEN`), never in YAML directly.
- Privileged CLI commands (`replay`, `trace`, `dlq`) require shell
  access, which is the standard Unix trust boundary.
- Acked retention is privacy-relevant. Default 14 days is generous;
  shorten via `bus.acked_retention_days` if conversations contain
  regulated data.

### Phase 2 hooks (deferred to BACKLOG)

The following are deliberately not in v1, captured in
[`docs/BACKLOG.md`](../../BACKLOG.md). Each is opt-in via the existing
hook stages and requires no bus-core changes:

- **ACL hook** — per-`from`/`to` allow/deny rules.
- **Redaction hook** — regex-based payload redaction before persistence.
- **Rate-limit hook** — per-endpoint or per-(from,to) throttling.

Backlog also captures further-future items (encryption at rest, mTLS for
federation, signed envelopes, sandboxing of adapters, per-endpoint E2E
keys, anomaly detection). Each lists its trigger condition.

## Out of Scope for v1

These are intentionally not built. Each has a trigger condition; the
discipline is to not implement until that trigger fires.

| Item | Why deferred / when to revisit |
|---|---|
| `events` endpoint adapter (fanout/subscriptions) | First time we have a sensor stream that needs ≥2 subscribers. |
| OwnTracks / Pepper App adapter | Once Topic 12 has a concrete spec. |
| Inbound webhook adapter (HTTP server) | First webhook source — likely soon. |
| Multiple agent consumers (agent-deb, agent-research) | Supported by design today; ship the second one when first real use case lands. |
| Automatic DLQ expiry | When a real flow generates DLQ growth. Manual purge sufficient for now. |
| Archival job (acked → `bus-archive.sqlite`) | Implement after acked retention becomes a problem in real use. |
| Tracing/observability integration (OpenTelemetry, etc.) | The CLI `trace` command is sufficient for v1. |
| Hot-pluggable endpoints (runtime register/deregister) | YAML + restart is fine; revisit only when there's actual demand. |
| Public Python `Bus` API | Endpoints are the only legitimate publishers; this is the security discipline. |
| stdio MCP transport for `ClaudeCodeMCPEndpoint` | HTTP/SSE on loopback covers single-agent and multi-agent uniformly. stdio is fundamentally 1:1 and would require a parallel implementation for no benefit. |
| Non-loopback `bind_host` (LAN, public) | Requires the auth/TLS BACKLOG items first; runner refuses to start in this configuration without them. |
| Capability schemas / RPC IDLs in the directory | Description prose is sufficient for agent-side reasoning; revisit only if a tool-discovery use case demands structure. |
| Liveness flags in `endpoints()` results | Bus already buffers when offline; sender does not need to know live state. Easy to add later. |

## Where Pepper-style concerns live

For continuity with the Pepper architecture conversation: the bus
deliberately does not solve any of the following, and each has a clear
home elsewhere.

| Concern | Where it lives |
|---|---|
| Mailbox draining cadence (FIFO vs batch vs prioritized) | The `ClaudeCodeMCPEndpoint` adapter, exposing `list_pending` so the agent picks handling order. |
| Cancellation of in-flight delegations | The originating agent's correlation table + a `Cancellation` envelope to the delegate. |
| Long-running delegations / progress | `Progress` envelopes from delegate to delegator. |
| State-event vs message-event distinction | `kind: Event` payloads with open-ended `Event.payload.type`; subscribers attach at the future `events` endpoint adapter. |
| Per-type routing rules | Inside subscriber endpoints (events fanout). The bus stays unicast-by-`to:`. |

The bus stays minimal; the agent-facing side gets richer over time.

## Open Questions and Known Risks

None blocking implementation. Items worth re-checking once real traffic
lands:

- **`max_pending_per_endpoint` default of 10000.** Reasonable for
  conversational and event volumes; may need adjustment for high-rate
  sensor streams. Configurable, so a quick fix.
- **Redelivery timeout of 5 minutes.** Generous; an MCP client that
  legitimately takes longer than 5 minutes to handle one envelope will
  cause spurious redelivery. Consider per-endpoint override if this
  shows up.
- **No automatic DLQ expiry.** Could grow unbounded if a recurring
  failure goes unattended. Operator practice is the mitigation; revisit
  if it bites.

## References

- Existing `HookTool` design: `docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md`
- Pepper data-ingress audit: source code at `E:\workspaces\ai\pepper\src\pepper\`
- BACKLOG: `docs/BACKLOG.md`
