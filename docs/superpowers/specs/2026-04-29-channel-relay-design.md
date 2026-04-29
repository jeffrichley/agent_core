# agent-core-channel — Stdio Channel Relay (Sub-project I, Part 2)

> **Companion spec:** [`2026-04-29-responsive-inbox-design.md`](2026-04-29-responsive-inbox-design.md) covers the daemon-side push pipeline. Together the two specs deliver sub-project I: responsive inbox.

## Why this exists

The responsive-inbox PR (8 daemon-side commits on `feat/responsive-inbox`) ships a working push pipeline: when an envelope arrives for an agent, the daemon emits a `notifications/claude/channel` notification on the agent's MCP SSE stream. The integration test in that PR confirms the notification is correctly delivered on the wire.

Live testbot validation revealed that **plain Claude Code drops the notification**. Standard `mcp.client.session.ClientSession` validates incoming notifications against a strict `ServerNotification` discriminated union and silently discards anything not in it.

Claude Code has a built-in mechanism for handling custom notifications — **channels**. A channel is an MCP server that:

1. Runs over **stdio** (Claude Code spawns it as a subprocess).
2. Declares `capabilities.experimental['claude/channel']: {}` in its handshake.
3. Loads via `--dangerously-load-development-channels server:<name>`.
4. Emits `notifications/claude/channel` events that Claude Code wakes the agent's turn on.

Pepper has a working version of this in her monolith (`channel/server.py`, ~624 lines including HTTP server + pipeline glue). For agent-core, we need a **generic, narrow** version that bridges the daemon's HTTP/SSE pushes to Claude Code's channel mechanism.

## Goals

- Plain Claude Code agents (testbot, future agents, eventually a port of Pepper) wake autonomously on bus arrivals.
- The relay is small and single-purpose: one inbound SSE consumer, one outbound stdio MCP emitter.
- The daemon's existing HTTP MCP host stays unchanged for tool calls. Only notifications go through the relay.
- Future portability: the package name (`agent-core-channel`) is generic; future CLIs with similar wake mechanisms can be additional entry points or variants.

## Non-goals

- **Not a full MCP proxy.** Claude Code keeps its existing HTTP MCP connection to the daemon for tool calls (`send`, `list_pending`, `handle`, etc.). The relay does not see tool traffic.
- **Not a replacement for `list_pending` polling.** Polling stays authoritative — the relay is a latency optimization that wakes the agent so it polls promptly.
- **Not multi-agent.** One relay subprocess per Claude Code session, scoped to one agent name on the bus.
- **Not authenticated.** Inherits the existing HTTPHost's loopback-only constraint.

## Architecture

```
[ Daemon machine ]                   [ Agent machine ]
                                     ┌──────────────────────────────────────────────┐
                                     │   ┌────────────┐                             │
                                     │   │            │                             │
┌───────────────────┐  HTTP MCP      │   │  Claude    │                             │
│                   │  (tools)       │   │  Code      │                             │
│  agent_core       │◄───────────────┼───│            │                             │
│  daemon           │                │   │            │                             │
│  port 8788        │                │   │            │                             │
│                   │                │   │            │   stdio MCP                 │
│  /mcp/<agent>     │                │   │            │   (channel notifications)   │
│  /notify/<agent>  │                │   │            │◄──────────────────┐         │
│                   │                │   └────────────┘                   │         │
│                   │  HTTP/SSE      │                                    │         │
│                   │  (notifications)                                    │         │
│                   │◄───────────────┼────────────────────┐               │         │
│                   │                │                    │               │         │
│                   │                │              ┌─────┴───────────────┴────┐    │
│                   │                │              │ agent-core-channel       │    │
│                   │                │              │ (subprocess of Claude)   │    │
│                   │                │              └──────────────────────────┘    │
└───────────────────┘                └──────────────────────────────────────────────┘
```

Three flows per agent:

1. **Tools (existing, unchanged):** Claude Code → daemon `/mcp/<agent>` over HTTP MCP.
2. **Notifications inbound:** daemon → relay over `/notify/<agent>` HTTP/SSE. JSON-encoded summary per push.
3. **Notifications stdio:** relay → Claude Code over stdio MCP. Re-emit as `notifications/claude/channel` so Claude Code's channel mechanism wakes the agent's turn.

## Daemon-side additions (in `packages/core`)

### `NotificationBroker`

New class in `agent_core/bus/notify_broker.py`. Maintains `dict[agent_name, set[asyncio.Queue]]`. Single instance per daemon, owned by the bus runner.

```python
class NotificationBroker:
    """Fan-out broker for per-agent notification subscribers."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, agent: str) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=128)
        async with self._lock:
            self._subs.setdefault(agent, set()).add(q)
        return q

    async def unsubscribe(self, agent: str, q: asyncio.Queue[dict]) -> None:
        async with self._lock:
            subs = self._subs.get(agent)
            if subs:
                subs.discard(q)
                if not subs:
                    del self._subs[agent]

    async def publish(self, agent: str, summary: dict) -> None:
        async with self._lock:
            subs = list(self._subs.get(agent, ()))
        for q in subs:
            try:
                q.put_nowait(summary)
            except asyncio.QueueFull:
                log.warning("notify broker: dropped event for %s (slow consumer)", agent)
```

Slow-consumer drop is intentional: list_pending stays authoritative; missing one push isn't catastrophic — the next push will still wake the consumer.

### `/notify/<agent>` Starlette route

Mounted on the existing `HTTPHost` (loopback-only HTTP server already serving `/mcp/<agent>`). Returns SSE.

```python
async def _notify_handler(request: Request) -> Response:
    agent = request.path_params["agent"]
    queue = await broker.subscribe(agent)

    async def event_stream():
        try:
            initial = bus.snapshot_for_agent(agent)
            if initial and initial["meta"]["count"] > 0:
                yield f"data: {json.dumps(initial)}\n\n"
            while True:
                summary = await queue.get()
                yield f"data: {json.dumps(summary)}\n\n"
        finally:
            await broker.unsubscribe(agent, queue)

    return EventSourceResponse(event_stream())
```

**Initial wake on connect** is intentional: when a relay first connects (e.g., agent reconnecting after offline), if there's already pending mail the daemon emits a snapshot immediately. Without this, an offline-then-online agent has to poll once before push behavior kicks in.

### Push hook in `_fire_after_debounce`

The existing push pipeline (`ClaudeCodeMCPEndpoint._fire_after_debounce` in `claude_code_mcp.py`) gains one extra line: after `await session.send_message(message)`, also call `broker.publish(self.name, summary)`. Both Claude Code's HTTP MCP session AND any subscribed relays receive the same summary.

This is **non-destructive to the existing responsive-inbox PR**. The HTTP MCP session push stays exactly as designed. The broker is purely additive.

### Failure modes (daemon-side)

| Scenario | Behavior |
|---|---|
| Slow consumer fills bounded queue | Drop with WARN log; list_pending stays authoritative. |
| Relay disconnects (SSE stream closes) | `finally:` runs `unsubscribe` cleanly. |
| Daemon stops | Broker drained; SSE responses naturally terminate when the ASGI server shuts down. |
| `/notify/<agent>` request for unknown agent | Subscribe still succeeds (broker is dict-of-sets); never receives events because nothing publishes to that key. Acceptable — a future agent of that name will register and start producing. |

## Relay package: `agent-core-channel`

### Package layout

```
packages/agent-core-channel/
├── pyproject.toml
├── src/
│   └── agent_core_channel/
│       ├── __init__.py
│       ├── __main__.py          # entry point — `agent-core-channel` CLI (Typer)
│       ├── stdio_server.py      # MCP-over-stdio server with claude/channel capability
│       └── sse_client.py        # SSE consumer for /notify/<agent>
└── tests/
    ├── test_stdio_server.py
    ├── test_sse_client.py
    └── test_end_to_end_relay.py # in-process e2e: real bus + real relay coroutine + fake stdio
```

Same shape as `agent-core-discord`, `agent-core-credentials`, `agent-core-scheduler`.

### `pyproject.toml` essentials

```toml
[project]
name = "agent-core-channel"
dependencies = [
    "mcp>=1.0",          # low-level Server (FastMCP doesn't expose experimental capabilities)
    "anyio>=4",
    "httpx>=0.27",
    "typer>=0.12",
]

[project.scripts]
agent-core-channel = "agent_core_channel.__main__:app"
```

### CLI entry point

Typer (matches the rest of the workspace).

```python
import anyio
import typer

from agent_core_channel.stdio_server import run_relay

app = typer.Typer(add_completion=False)


@app.command()
def main(
    agent: str = typer.Option(..., "--agent", help="agent name on the bus"),
    daemon_url: str = typer.Option(
        "http://127.0.0.1:8788",
        "--daemon-url",
        help="agent_core daemon URL",
    ),
) -> None:
    """Run the agent-core stdio channel relay."""
    anyio.run(run_relay, agent, daemon_url)
```

### Core loop (`stdio_server.py`)

Two concurrent tasks via `anyio.create_task_group`:

1. **MCP stdio server task** — runs `mcp.server.lowlevel.Server.run(read, write, init_options)` with `init_options.capabilities.experimental = {"claude/channel": {}}`. Handles the MCP handshake, advertises the channel capability, exposes zero tools/resources/prompts.
2. **SSE consumer task** — opens `httpx.AsyncClient.stream("GET", f"{daemon_url}/notify/{agent}")`, parses each `data: <json>\n\n` event, builds a `JSONRPCNotification(method="notifications/claude/channel", params=summary)`, wraps in `SessionMessage`, writes to the MCP write stream so Claude Code receives it.

The two tasks share the write stream. Clean shutdown via task group: if either task raises, the group cancels the other. Claude Code closing stdin → MCP server returns from `run()` → SSE consumer cancelled → relay process exits.

### Why low-level MCP, not FastMCP

FastMCP doesn't expose a way to declare custom experimental capabilities. Pepper's `channel/server.py` already established this pattern using the low-level `mcp.server.lowlevel.server.Server`. We follow the same approach.

### Failure modes (relay-side)

| Scenario | Behavior |
|---|---|
| Daemon down at startup | SSE consumer retries connection with exponential backoff (2s, 4s, 8s, capped at 30s). MCP stdio still works — Claude Code sees the channel server up but receives no notifications. |
| Daemon goes down mid-stream | Same retry loop. Buffered events lost; agent catches up via list_pending poll. |
| Claude Code closes stdin | MCP `Server.run()` returns; task group cancels SSE consumer; relay process exits cleanly. |
| Relay crashes | Claude Code sees the channel MCP server disconnect. No automatic restart from Claude Code's side; user re-launches Claude Code session. |
| Slow consumer (Claude Code's stdin pipe full) | `await write_stream.send(msg)` backpressures. Acceptable — events queue in OS pipe buffer; daemon-side debounce already coalesces. |

## testbot's `.mcp.json` after this lands

```json
{
  "mcpServers": {
    "agent-core": {
      "type": "http",
      "url": "http://localhost:8788/mcp/agent-testbot"
    },
    "agent-core-channel": {
      "command": "uv",
      "args": ["run", "agent-core-channel", "--agent", "agent-testbot"]
    }
  }
}
```

Two `mcpServers` entries. Two separate connections from Claude Code. Two responsibilities cleanly split.

testbot launch command (gains a flag):

```bash
cd ~/.testbot && claude --dangerously-load-development-channels server:agent-core-channel
```

## Testing strategy

### Layer 1 — Daemon-side unit tests (in `packages/core/tests/`)

- `test_notify_broker.py` — `subscribe` / `unsubscribe` / `publish`. Fan-out, slow-consumer drop, queue cleanup on unsubscribe.
- `test_notify_route.py` — Starlette test client against `/notify/<agent>`; subscribe, push a summary via the broker, assert SSE event arrives.
- `test_initial_wake_on_connect.py` — pre-populate `_pending` on a `ClaudeCodeMCPEndpoint`, subscribe via `/notify/<agent>`, assert immediate snapshot event.

### Layer 2 — Relay-side unit tests (in `packages/agent-core-channel/tests/`)

- `test_sse_client.py` — mock `httpx.AsyncClient.stream` with a sequence of `data: {...}\n\n` events plus a connection drop; assert events parsed and forwarded, plus reconnect with backoff.
- `test_stdio_server.py` — drive the MCP server with a fake stdio pair; complete the initialize handshake; assert `experimental.claude/channel` is declared, zero tools/resources/prompts advertised.
- `test_emit_channel_notification.py` — call the helper that writes a `notifications/claude/channel` to the stdio write stream; assert wire-level `JSONRPCNotification` envelope shape.

### Layer 3 — Cross-package integration test (in `packages/agent-core-channel/tests/`)

- `test_end_to_end_relay.py` — real bus + HTTPHost + `/notify/<agent>` route + a real `ClaudeCodeMCPEndpoint`; spawn the relay's `run_relay` coroutine in-process with `MemoryObjectStream` pairs replacing stdin/stdout. Inject an envelope via the stub endpoint → assert the fake "Claude Code stdin" receives a `notifications/claude/channel` event with the expected summary. Analogue of the responsive-inbox PR's Task 8 — proves the **full wire path** without needing real Claude Code.

### Layer 4 — Live testbot validation (the responsive-inbox plan's deferred Task 9)

The only thing that can verify "Claude Code actually wakes its turn loop on receipt of the channel notification" — that behavior is entirely a Claude Code internal.

1. Stop daemon and testbot.
2. Add `agent-core-channel` to `~/.testbot/.mcp.json` alongside `agent-core`.
3. Restart daemon. Confirm `/notify/<agent>` is reachable: `curl http://127.0.0.1:8788/notify/agent-testbot` should hold open.
4. Launch testbot with `claude --dangerously-load-development-channels server:agent-core-channel`.
5. Re-run the 5 validation prompts from the responsive-inbox plan's Task 9. STEP 1 (autonomous wake on single envelope) is now expected to PASS.

## Coordination with the responsive-inbox PR

The channel relay is **the second half of sub-project I**, not a separate sub-project. Either piece alone is incomplete: the daemon push without the relay puts notifications on the wire that Claude Code drops; the relay without the daemon push has nothing to forward.

**Branching:**
Stay on `feat/responsive-inbox`. The existing 9 commits (foundation: urgency, persistence, sort, batching, registry, push pipeline, Discord regex, integration test, the SessionRegistry fix) are the daemon-side half. Channel-relay commits add on top.

**Spec docs:**
Two design docs that cross-reference each other:
- `2026-04-29-responsive-inbox-design.md` — daemon-side push pipeline (already committed).
- `2026-04-29-channel-relay-design.md` — agent-side relay (this doc).

The responsive-inbox spec gets a brief addendum at the top noting the dependency.

**Plan docs:**
Two plans, one shared branch:
- `2026-04-29-responsive-inbox.md` — Tasks 1–9 (Tasks 1–8 done; Task 9 deferred until relay lands).
- `2026-04-29-channel-relay.md` — implementation tasks for the relay (writing-plans skill produces this next).

**Joint validation gate:**
Task 9 of the responsive-inbox plan re-runs only after the channel relay is implemented and registered in testbot's `.mcp.json`. STEP 1 (autonomous wake) flipping from FAIL → PASS is the green light to merge.

**PR:**
Single PR for the combined work. Title: `feat: responsive inbox (sub-project I) — push pipeline + channel relay`. The PR's diff against `main` shows both halves; reviewers get one coherent story.

**Roadmap:**
Sub-project I gets marked 🟢 only when the joint PR merges. ROADMAP's row for I expands to mention both packages (`agent-core` push pipeline, `agent-core-channel` relay).

## Deferred to BACKLOG

Items considered and intentionally NOT included, with the trigger for picking each up:

### Auth on `/notify/<agent>`

Same trigger as the existing BACKLOG entry for the MCP HTTP host: the moment `bind_host` becomes non-loopback. Today the route inherits "loopback only, no token" — sufficient for v1.

### Multiple relays per agent (multi-Claude-Code-instance fan-out)

The broker's set-of-queues already supports it on the daemon side. What's missing is figuring out *whether* it's useful (e.g., a desktop Claude Code AND a mobile Claude Code both backing the same agent identity?) and how to disambiguate which one should "own" the agent's identity.

- **Trigger:** First real use case where a single agent identity backs multiple concurrent Claude Code sessions.

### Relay auto-restart on crash

Claude Code spawns the subprocess; if it crashes, the user relaunches the Claude Code session.

- **Trigger:** First observed in-the-wild crash with no obvious bug, where re-launching the whole session is friction.

### Notification batching at the broker level

Daemon already debounces at 50ms before pushing. The broker fans out one event per 50ms.

- **Trigger:** First observed relay-side overload (slow consumer drops).

### TLS / wss for `/notify/<agent>`

Same trigger as the broader bus-federation question. Loopback HTTP is fine until daemon and agent are on different machines.

### Replacing `--dangerously-load-development-channels`

The flag is by definition dangerous and might get renamed/removed by Anthropic. The relay's stdio MCP handshake stays the same; only the user-facing launch flag changes.

- **Trigger:** When the channels API stabilizes / the flag is renamed in Claude Code.

### Bidirectional channel API

Pepper's `channel/server.py` has some of this for tool calls that bypass the bus. We don't want bypass-the-bus tools in agent-core.

- **Trigger:** A real use case shows up that requires the relay to participate in agent → daemon messaging beyond what the existing HTTP MCP tools cover.

## Open questions

None at design time. All architectural decisions resolved during the brainstorm:

- **Q1 (relay scope):** Notifications-only (option A).
- **Q2 (subscription mechanism):** Dedicated `/notify/<agent>` SSE endpoint (option 2).
- **Q3 (package layout):** New package (option A).

## Acceptance criteria

The joint sub-project I is shippable when:

1. All Layer 1 / 2 / 3 tests pass (CI green).
2. Layer 4 live testbot validation Task 9 STEP 1 (autonomous wake on single envelope) PASSES — testbot's turn fires without human prompting on a single self-published envelope, within ~1s.
3. STEPS 2–5 of the live validation also PASS (burst coalescing, urgency ordering, same-sender batching, mailbox-authoritative-on-reconnect).
4. Daemon's `/notify/<agent>` route survives a relay disconnect-and-reconnect cycle without leaking subscribers.
5. ROADMAP and BACKLOG updated.
