# Daemon Bounce MCP Session Recovery — Design

**Issue:** [#91](https://github.com/jeffrichley/agent_core/issues/91) — Daemon
bounce orphans the live Claude Code MCP client session; no recovery without a
full session restart.

**Status:** Approved design (2026-05-16). Next: implementation plan.

---

## Problem

A daemon restart (`daemon stop`/`start`, the new `daemon refresh`/`install`
from #79, or a crash) permanently strands any already-connected, long-lived
Claude Code agent session. Every subsequent MCP tool call fails with
`Session not found`. The session cannot self-recover — not by retry, not by
`/mcp reconnect`. The only fix today is a full restart of that Claude Code
session.

With #79 making `daemon refresh` the **daily** code-pickup flow, this failure
goes from occasional to routine. It is also a hard prerequisite for the
always-on / reboot-resilience goal: any auto-relaunched agent must survive a
daemon bounce.

## Root cause

MCP over streamable-HTTP is session-based. On connect the client runs an
`initialize` handshake and the daemon issues an `mcp-session-id`; the client
caches it and presents it on every later call. A restarted daemon is a new
process with no memory of prior session IDs (sessions are in-process state in
`ClaudeCodeMCPEndpoint`, not persisted). The still-running client keeps
presenting the stale ID; the new process rejects every call with
`Session not found`. Claude Code's MCP client does not re-`initialize` on
`Session not found`, and `/mcp reconnect` does not force a fresh handshake.

The fragility is isolated to **one of the agent's two MCP surfaces**:

| `.mcp.json` entry | Transport | Role | Behaviour on daemon bounce |
|---|---|---|---|
| `agent-core` | streamable-HTTP, direct to daemon | bus tool surface (`send`/`consume`/`reply`/`list_pending`/…) | **stranded** — stale `mcp-session-id` (this *is* #91) |
| `agent-core-channel` | stdio child process | push-wake relay (`/notify/<agent>` SSE → inline render) | **already resilient** — `sse_client.py` reconnects forever with backoff |

The wake path already survives bounces (confirmed in the 2026-05-15 incident).
Only the direct-HTTP tool surface is broken.

## Decisions

Settled during brainstorming:

1. **Recovery bar: fully transparent.** After a daemon bounce/refresh the live
   session keeps working with zero human action and no session restart.
2. **Down-window behaviour: fail-fast, retryable.** When the daemon is
   unreachable mid-refresh, a tool call returns a structured transient error
   immediately; the agent owns the retry decision. No in-proxy hang/queue.
3. **Surface count: A1 — two stdio entries.** Replace the fragile direct-HTTP
   `agent-core` entry with a stdio proxy; leave `agent-core-channel` as-is.
   Both surfaces become stdio and resilient — the *asymmetry* that is #91 is
   eliminated. Folding both into a single MCP server (A2) was rejected: it
   couples wake-rendering with tool-proxying and departs from FastMCP's native
   proxy shape for only a cosmetic "one entry" gain (two servers is not
   cognitively harder for the agent — tools are tools regardless of backing
   server).
4. **Rollout: test agent first, Pepper last** — per the standing
   "Pepper hands-off until proven" rule.

## Library facts (verified via context7, FastMCP `/prefecthq/fastmcp`)

- `fastmcp.server.create_proxy(backend, name)` forwards all requests to an MCP
  backend (URL / FastMCP instance / script / MCPConfig) and can be run over
  **stdio** (`proxy.run()` defaults to stdio) — the documented
  "bridge HTTP backend to local stdio for Claude Desktop" pattern, which is
  exactly our scenario.
- Default mode mints a **fresh backend session per request** (session
  isolation). This is the load-bearing property: there is no long-lived
  `mcp-session-id` to go stale, so a daemon bounce cannot strand the proxy.
- `tools/list` / `tools/call` are forwarded natively (the "dynamic
  passthrough" is built in, not hand-rolled); standard MCP feature
  notifications (sampling/logging/progress/…) are forwarded.
- The docs do **not** promise forwarding of *arbitrary custom* server→client
  notification methods. Our wake (`notifications/claude/channel`) is custom
  and rides a persistent SSE stream — so wake stays on the existing
  `agent-core-channel` `/notify` path and is **not** routed through the proxy.
- Explicit session control is available via
  `FastMCPProxy(client_factory=…)` returning `ProxyClient(backend)` — this is
  the seam for per-request sessions + error translation.

## Architecture

```
Claude Code (agent session)
  ├── MCP server "agent-core"          ── stdio ──>  agent-core-busproxy (NEW)
  │                                                     │  per-request
  │                                                     │  fresh session
  │                                                     ▼
  │                                          daemon  http://127.0.0.1:8789/mcp/<agent>
  │                                                     ▲
  └── MCP server "agent-core-channel"  ── stdio ──>  channel relay (UNCHANGED)
                                                        │  SSE, reconnects forever
                                                        ▼
                                          daemon  /notify/<agent>
```

- **Tool surface → stdio FastMCP proxy.** `.mcp.json` `agent-core` changes
  from `{"type":"http","url":".../mcp/<agent>"}` to a stdio command running
  the busproxy against `http://127.0.0.1:8789/mcp/<agent>` with per-request
  backend sessions. Claude Code ⇄ stdio busproxy (lifetime = the Claude Code
  session; never dies on a bounce) ⇄ fresh HTTP session to whatever daemon is
  currently up.
- **Wake surface → unchanged.** `agent-core-channel` keeps its `/notify`
  subscription via `sse_client.py` (already reconnects across bounces).

#91 is eliminated **by construction**, not by recovery logic: no stale
session id ever exists.

## Components

1. **`agent-core-busproxy` — new, self-contained package**
   (`packages/agent-core-busproxy/`, mirroring `agent-core-channel`'s shape;
   per the "bus services are their own package, never reach into sibling
   venvs" rule). A thin stdio MCP server = `FastMCP` proxy over
   `http://127.0.0.1:8789/mcp/<agent>`. CLI:
   `agent-core-busproxy --agent <name> --daemon-url http://127.0.0.1:8789`
   (same arg shape as the channel relay).

2. **`client_factory` seam.** `FastMCPProxy(client_factory=…)` mints a fresh
   `ProxyClient` per request (FastMCP's recommended isolation mode). Single
   place where daemon-down is caught and translated.

3. **Transient-error contract.** On backend connect/handshake/transport
   failure the proxy returns a structured tool result:
   `{"error": "bus_unavailable", "transient": true,
   "retry_after_seconds": <n>, "detail": "<redacted reason>"}`.
   A genuine backend tool exception forwards through **verbatim** and is
   **not** relabeled transient. `detail` is redacted (no signed URLs/tokens —
   same discipline as the #76 redaction work).

4. **`.mcp.json` template + per-agent cutover.** The canonical per-agent
   template swaps the `agent-core` `{"type":"http",…}` block for the stdio
   busproxy command; `agent-core-channel` untouched. Applied per agent
   (test agent first, Pepper last).

**Explicitly out of scope (YAGNI):** no shared/persistent backend session
(per-request is the safe default; revisit only if localhost handshake latency
ever bites); no in-proxy retry/queue (fail-fast — the agent owns retry).

## Data flow

- **Steady state (no call in flight):** there is *no* persistent MCP session
  for the agent — and that is correct. The daemon's
  `ClaudeCodeMCPEndpoint.deliver()` already handles "no session connected":
  it queues the envelope and raises `EndpointUnavailable` (bus redelivers),
  while `_notify_mail_arrived` fans out to the broker → `/notify/<agent>`.
  "No live MCP session" becomes the *normal* resting state, not a failure.
- **Wake:** envelope → daemon queues + publishes to `/notify/<agent>` →
  unchanged channel relay injects the wake. (Unchanged path.)
- **Tool call:** Claude Code → stdio → busproxy → `client_factory` mints a
  fresh `ProxyClient` → new `initialize` + session against the currently
  running daemon → `tools/call` forwarded → result → backend session closes.
  Next call repeats with a brand-new session.
- **Across `daemon refresh`:** if a call lands while the daemon is down, the
  per-request connect fails → busproxy returns the transient error → agent
  retries → by retry the new daemon is up → fresh session succeeds. The stdio
  pipe never broke; the channel relay reconnected its SSE on its own.

**Key invariant:** the busproxy holds no cross-call state. Every tool call is
independent and self-contained — which is exactly why a backend restart
cannot strand it.

## Error handling & self-healing

1. **Backend errors never crash the proxy.** Every forwarded call wraps
   backend connect/handshake/transport failures and returns the structured
   transient result. A daemon-down or mid-refresh state produces a *tool
   error*, never a process exit. This invariant is what makes the structural
   self-healing hold.
2. **Genuine tool errors pass through verbatim** — not relabeled transient,
   so the agent never retry-loops on a real failure.
3. **Residual single point of failure — the stdio child's own liveness.**
   The busproxy (and the channel relay) are children Claude Code spawns. A
   daemon *bounce* is fully self-healing by construction. A *crash of the
   busproxy process itself* (a proxy bug/OOM, not a daemon fault) depends on
   Claude Code re-spawning stdio MCP servers. Hardening:
   (a) the proxy is deliberately thin with a broad top-level guard so a
   daemon fault cannot propagate to a process crash;
   (b) **plan-stage verification task:** confirm Claude Code's stdio-server
   respawn behaviour; if it does not auto-respawn, document/automate it. This
   is also the seam the future always-on/reboot work plugs into.
4. **No silent degradation.** Transient errors are logged with a redacted
   reason so a *persistently* down daemon is diagnosable rather than an
   infinite quiet retry.

**Honest summary:** the daemon-bounce case (the actual #91) is zero-touch
self-healing by construction. The only residual that still needs a human (or
future automation) is a crash of the stdio child itself — surface minimized
and respawn behaviour explicitly verified in the plan.

## Testing strategy

1. **Transient-error contract (unit).** Backend connect/handshake failure →
   asserts structured `{transient:true,…}` result, reason redacted. Genuine
   backend tool exception → asserts verbatim passthrough, **not** relabeled
   transient. Fake backend mirrors real FastMCP refusal semantics strictly
   (test-fakes-mirror-real rule) so green tests cannot mask a real-lib gap.
2. **#91 regression (the proof).** One long-lived busproxy process: call a
   tool (succeeds) → stop & restart the backend daemon → call again →
   succeeds with no client-side re-handshake. Deterministic (in-process or
   ephemeral-port backend, **no real sleeps** — all backoff stays in the
   agent, not the proxy, avoiding the looptime no-yield pitfall from #76 T5).
   This test failing == #91 not fixed.
3. **Down-window (fail-fast).** Backend unreachable → call returns the
   transient error promptly (asserts no long hang) → backend back → next call
   succeeds.
4. **Tool-surface fidelity.** `tools/list` through the proxy == daemon's real
   surface; `notifications/tools/list_changed` (briefs plugin fires it)
   propagates so the agent re-enumerates.
5. **Wake untouched (regression).** `agent-core-channel`'s existing suite
   stays green; an interleave test confirms wake (`/notify`) + tool calls
   coexist with per-request sessions and the daemon's no-session
   `EndpointUnavailable` path.
6. **Manual acceptance.** Real test agent, real `daemon refresh`, zero-touch
   tool survival — runbook step the operator runs (same shape as #76 T7 /
   #79's manual step).

## Rollout & cutover

1. **Build phase (zero agents touched).** Ship `packages/agent-core-busproxy/`
   + test suite. No `.mcp.json` changes yet — pure addition, no blast radius.
2. **Test-agent validation gate.** A fresh throwaway agent gets the new
   shape. Bar: multiple real `daemon refresh` / `stop`+`start` cycles,
   asserting zero-touch tool survival *and* wake delivery across each, over a
   realistic span — not a single happy-path bounce.
3. **Pepper cutover (only after the gate passes).** Swap Pepper's `.mcp.json`
   `agent-core` block to the stdio busproxy command; `agent-core-channel`
   untouched. Rollback = copy back the backed-up `.mcp.json` (single file,
   one `Copy-Item` — same rollback pattern as the 2026-05-06 pepper-flip).
4. **Make it the default.** Update the canonical per-agent template /
   agent-init so every new agent is born resilient.
5. **Docs + close-out.** Replace the #91 stopgap warning in
   `docs/setup/daemon.md` ("live agent sessions must be restarted after a
   bounce") with the new behaviour; close #91. Unblocks the always-on/reboot
   work — the busproxy is the substrate it plugs into.

## Relationships

- **#79** — `daemon refresh` (daily flow) is what made #91 routine; this
  design removes that sharp edge. The #79 stopgap doc warning is replaced in
  step 5.
- **Always-on / reboot resilience** — this is a prerequisite; the busproxy is
  the substrate any auto-relaunched agent depends on.
- **#76** — reuses the URL/token redaction discipline for `detail`.

## Provenance

#91 surfaced 2026-05-15 by Pepper from inside the affected session during the
#79 migration; root-cause analysis and repro hers.
