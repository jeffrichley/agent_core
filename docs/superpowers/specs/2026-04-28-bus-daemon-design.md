# Bus Daemon & ClaudeCodeMCPEndpoint — Design Spec

**Date:** 2026-04-28
**Status:** Proposed (pending user review)
**Builds on:** [`2026-04-27-channel-bus-design.md`](2026-04-27-channel-bus-design.md)
**Clarifies:** Transport language in the channel-bus spec — see § Architectural shape.

## Overview

This spec defines sub-project B (v1) of the agent-core roadmap: turning
the bus into a real long-running daemon process that Claude Code
instances can connect to via MCP. The bus core, persistence, runner,
and operator CLI were merged in Phase 1 (PR #2). What's still missing
is (a) the `ClaudeCodeMCPEndpoint` adapter that bridges Claude Code to
the bus, (b) the shared HTTP host that serves it, and (c) a
PID-managed daemon lifecycle CLI. This spec defines those three
pieces and the validation milestone (a fresh test agent) that proves
the architecture end-to-end.

Pepper's existing runtime is **deliberately untouched** in this
sub-project. Migration of Pepper to the new architecture is a separate
future sub-project, planned only after the architecture is validated
on a fresh test agent.

## Architectural Shape

Most architectural decisions for this scope are settled in the
2026-04-27 channel-bus spec. This spec confirms or extends a small
set of points that were either ambiguous or genuinely open.

### Settled (reference, not re-decided)

- One long-lived `agent-core` daemon process per machine. Bus,
  endpoints, and persistence all live in-process. (Channel-bus spec
  § Process model.)
- Endpoint identity is path-based: each `ClaudeCodeMCPEndpoint` mounts
  at `/mcp/<name>`. The URL path IS the agent's identity on the bus —
  no headers, no env-var declaration. (Channel-bus spec
  § MCP transport implementation.)
- v1 binds loopback only (`127.0.0.1`); the runner refuses
  non-loopback bind without an auth hook (already enforced in
  `bus/runner.py`).
- Multi-agent support is by design: multiple `ClaudeCodeMCPEndpoint`
  instances on the same shared HTTP host, each with its own name and
  mount path.

### Confirmed in this spec

- **Transport: Streamable HTTP.** The channel-bus spec says "HTTP/SSE
  only"; this is interpreted as the modern MCP Streamable HTTP
  transport (single endpoint per server, SSE under the hood for
  streaming responses), not the deprecated two-endpoint HTTP+SSE
  flavor. FastMCP's default.
- **Daemon lifecycle: PID-managed only in v1.** Cross-platform
  `start`, `stop`, `status` with a PID file. System service
  installation (systemd, launchd, Windows Service) is deferred until
  concrete demand emerges.
- **Endpoint registration: central pull config.** A single
  `~/.agent-core/agent_core.yaml` is the source of truth for declared
  endpoints. v1 expects hand-edits; an `agent install <name>` CLI
  that automates this is its own future sub-project.

### Out of scope for this sub-project

- Multi-agent CLI (`agent install`, `agent start`, `agent stop`,
  `agent list`) — its own future sub-project.
- Pepper migration to the new architecture — a future sub-project,
  planned only after end-to-end validation on a fresh test agent.
- First-time setup / install polish — sub-project C
  (smart init/update).
- System service installation (systemd / launchd / Windows Service) —
  deferred until demand emerges.
- Phase 2 bus features (auth hooks, transcript writer) — already in
  BACKLOG.
- Hot-reload of `agent_core.yaml` without restart — restart on YAML
  change is the v1 expectation.

## Components

### `ClaudeCodeMCPEndpoint`

**Location:** `packages/core/src/agent_core/endpoints/claude_code_mcp.py`

Implements the `Endpoint` Protocol. Wraps a single `FastMCP` server
that exposes the bus's tool surface to one connected Claude Code
instance. Constructor signature follows the runner's convention
(every endpoint class accepts `name` as a kwarg):

```python
class ClaudeCodeMCPEndpoint:
    name: str    # set by runner via kwarg per existing convention
    mount: str   # URL path under the shared HTTP host (e.g. "/mcp/agent-pepper")
```

**Tools registered with the FastMCP server** (matching the channel-bus
spec § MCP transport implementation):

- `send(to, kind, payload, correlation_id?, in_reply_to?, metadata?, expires_at?)` — publishes via the endpoint's `BusHandle`. Bus stamps `from:`.
- `list_endpoints() → [{name, description}]` — reads `BusHandle.endpoints()`.
- `describe_endpoint(name) → {name, description}` — same source, single entry.
- `list_pending() → [envelope]` — snapshot of this endpoint's mailbox.
- `handle(envelope_id)` — convenience: equivalent to `ack`.
- `ack(envelope_id)`, `nack(envelope_id, requeue=True)` — direct ack/nack.

**Inbound delivery** (`deliver(envelope)`): pushes the envelope as an
MCP notification on the active SSE stream of the connected session.
If no session is currently connected, raises `EndpointUnavailable` so
the bus queues the envelope in the mailbox and retries on reconnect.

**Session lifecycle:** at most one Claude Code session per endpoint at
a time (the URL path identifies the agent). Implementation pins
second-connection behavior in the plan; the assumption is "reject the
second."

### Shared HTTP host

**Location:** `packages/core/src/agent_core/bus/http_host.py` (new
module).

Starlette ASGI app + Uvicorn server. The runner constructs the host
**after** all endpoints are registered, mounting each
`ClaudeCodeMCPEndpoint`'s FastMCP sub-app at its declared `mount`
path. Uvicorn runs on the bus's asyncio event loop (no separate
thread). Bound to `http.bind_host:http.bind_port` from
`agent_core.yaml` (default `127.0.0.1:8788`). The existing loopback
guardrail in `bus/runner.py` is unchanged.

The host is started before any `endpoint.start()` runs so that
endpoint adapters that need ASGI lifespan events can rely on it.

### Daemon lifecycle CLI

**Location:** `packages/core/src/agent_core/daemon/cli.py` (new
sub-package). The existing `agent-core bus` namespace stays for
operator surface (status, mailbox, trace, replay, etc.). The new
`agent-core daemon` namespace handles process supervision.

**Commands:**

- `agent-core daemon start` — spawns
  `agent-core bus run --config ~/.agent-core/agent_core.yaml` detached;
  writes `~/.agent-core/daemon.pid`. Refuses to start if the PID file
  exists and the process is alive.
- `agent-core daemon stop` — reads the PID file, kills the process
  tree (psutil — same approach as Pepper's `pepper stop`).
  Idempotent. Cleans stale PID files.
- `agent-core daemon status` — reports running/not-running, PID,
  approximate uptime, last 20 lines of `~/.agent-core/daemon.log`.

**Daemon log:** `~/.agent-core/daemon.log` — the runner's stdout and
stderr when started via `daemon start` are redirected here. Created
with 0600 permissions.

**File layout in `~/.agent-core/`:**

```
~/.agent-core/
├── agent_core.yaml      # bus config + endpoint declarations
├── bus.sqlite           # bus mailbox (existing)
├── daemon.pid           # PID file
├── daemon.log           # daemon stdout/stderr
└── (subdirs as bus needs them)
```

### Test agent workspace (deliverable, not code)

A hand-built `~/.testbot/` workspace used to validate the architecture
end-to-end. Minimum contents:

- `.mcp.json` — single MCP server entry pointing at the daemon:

  ```json
  {
    "mcpServers": {
      "agent-core": {
        "type": "http",
        "url": "http://localhost:8788/mcp/agent-testbot"
      }
    }
  }
  ```

- `CLAUDE.md` — short identity instructions ("you are testbot, a test
  agent for validating the agent-core bus").
- A scratch directory the agent can use as cwd.

The corresponding endpoint is hand-added to
`~/.agent-core/agent_core.yaml`:

```yaml
endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-testbot
    description: "Test agent for validating bus architecture."
    params:
      mount: /mcp/agent-testbot

  - class: agent_core.endpoints.stub.Stub
    name: stub
    description: "Echo endpoint for round-trip testing."
```

## Data Flow

Outbound — testbot calls a tool:

```
testbot's claude
   → POST /mcp/agent-testbot/...        (Streamable HTTP)
   → Starlette routes to mounted FastMCP app
   → FastMCP dispatches send() tool
   → tool calls bus_handle.publish(envelope)
   → bus stamps from: agent-testbot, runs pre_publish hooks, persists
   → recipient.deliver()
```

Inbound — something publishes addressed to testbot:

```
some endpoint
   → bus.publish(envelope, to="agent-testbot")
   → bus persists, dispatches to the agent-testbot endpoint
   → ClaudeCodeMCPEndpoint(name=agent-testbot).deliver(envelope)
   → push MCP notification on active SSE stream
   → testbot's claude eventually calls ack(envelope_id) tool
   → endpoint forwards to bus_handle.ack
```

Offline — testbot is disconnected when something publishes to it:

```
bus.publish(to="agent-testbot")
   → endpoint.deliver() raises EndpointUnavailable (no session)
   → bus pauses delivery to this endpoint, queues in mailbox
   → on session reconnect, bus drains mailbox in arrival order
```

## Configuration

The existing `agent_core.yaml` schema is unchanged. The
`ClaudeCodeMCPEndpoint` joins the existing `endpoints:` list. Example
v1 configuration after B ships:

```yaml
bus:
  storage_path: ~/.agent-core/bus.sqlite

http:
  bind_host: 127.0.0.1
  bind_port: 8788

endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-testbot
    description: "Test agent for validating bus architecture."
    params:
      mount: /mcp/agent-testbot

  - class: agent_core.endpoints.stub.Stub
    name: stub
    description: "Echo endpoint for round-trip testing."
```

## Error Handling

Most error paths are already specified by the bus protocols. New
behaviors introduced by this sub-project:

- **`deliver()` with no active session** → raise `EndpointUnavailable`.
  Bus pauses delivery, queues in mailbox, retries on reconnect.
- **MCP session disconnect mid-`deliver()`** (broken pipe) →
  `EndpointUnavailable`. Bus retries.
- **Second Claude Code connection to the same `/mcp/<name>`** →
  behavior pinned in implementation plan; assumption is "reject the
  second" (matches the channel-bus spec's one-session-per-endpoint
  expectation).
- **`daemon start` when daemon already running** → exit 1, print
  existing PID.
- **`daemon stop` when not running** → idempotent, exit 0; clean stale
  PID file.
- **`daemon start` with no `~/.agent-core/agent_core.yaml`** → exit 1
  with a message pointing at where to create the config. v1 has no
  auto-init; that's sub-project C.
- **HTTP port already in use** → daemon exits 1 with a clear message
  including the offending port.

## Testing

Three layers:

### Unit
- `ClaudeCodeMCPEndpoint` protocol conformance.
- Tool registration on the FastMCP server.
- `deliver()` with active session pushes notification.
- `deliver()` without active session raises `EndpointUnavailable`.
- FastMCP test client exercises the tool surface.

### Integration
- Boot a `Bus` with one `ClaudeCodeMCPEndpoint` and one `Stub`.
- Drive the endpoint with an in-process MCP client.
- Round-trip: agent calls `send(to="stub", ...)`, stub receives.
- Mailbox semantics: disconnect the agent's session, publish addressed
  to the agent, reconnect, verify drain in arrival order.

### Manual end-to-end (the validation milestone)
- `agent-core daemon start` with a hand-curated `agent_core.yaml`
  declaring `agent-testbot` and `stub`.
- Launch real Claude Code in `~/.testbot/`. Verify connection.
- Have testbot call `list_endpoints` (sees stub + itself).
- Have testbot call
  `send(to="stub", kind="TextMessage", payload={"text": "hello"})`.
  Verify stub logs received it.
- Round-trip variant: testbot calls
  `send(to="agent-testbot", ...)` — sends to itself. Verifies inbound
  notification path against a real MCP client.
- `agent-core daemon stop` — verify clean shutdown and stale PID
  cleanup.

## Open Questions and Known Risks

- **One-session-per-endpoint enforcement.** The path-based identity
  model assumes one Claude Code session per `/mcp/<name>` mount at a
  time. FastMCP's exact behavior on a second concurrent connection
  needs to be verified during implementation; the plan pins the
  policy.
- **End-to-end "publish into the bus from outside" testing.** The
  bus's security primitive (only registered endpoints can publish)
  means there is no easy CLI to inject envelopes for testing. The
  validation milestone uses agent-sends-to-self and
  agent-sends-to-stub round trips. If broader testing affordances are
  needed later, an explicit dev-only endpoint can be added (out of
  v1 scope).
- **Daemon log rotation.** v1 writes to a single `daemon.log` with no
  rotation. If logs grow unbounded in real use, add rotation later.

## References

- Channel bus spec (foundation):
  [`docs/superpowers/specs/2026-04-27-channel-bus-design.md`](2026-04-27-channel-bus-design.md)
- Workspace monorepo spec:
  [`docs/superpowers/specs/2026-04-28-monorepo-workspace-design.md`](2026-04-28-monorepo-workspace-design.md)
- Roadmap (sub-project B):
  [`docs/ROADMAP.md`](../../ROADMAP.md)
