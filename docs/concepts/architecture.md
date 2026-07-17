# Architecture

This page explains how agent-core's pieces fit together at runtime; the individual concept pages go deeper on each.

## The six core nouns

**Being** — an AI agent identity (a Claude Code session, or any process that talks to the bus). Each being has a home directory (`~/.<being>/`), a credentials vault, and one or more registered endpoints on the bus.

**Daemon** — the single long-running host process started with `agent-core daemon start`. It owns the bus, starts and supervises all configured endpoints, and exposes an HTTP API the sidecars connect to.

**Bus** — the in-process async message router inside the daemon. It reads `agent_core.yaml`, routes envelopes between registered endpoints, persists every envelope to a SQLite mailbox before delivery, and retries or dead-letters on failure.

**Endpoint** — an addressable participant registered on the bus. An endpoint implements three async methods (`start`, `deliver`, `stop`). The daemon hosts many: one `ClaudeCodeMCPEndpoint` per being, a `DiscordEndpoint`, a `SchedulerEndpoint`, etc. Third-party packages contribute additional endpoint types via Python entry points.

**Sidecar** — a lightweight stdio MCP server process each being runs alongside its AI session. Two sidecars per being: `agent-core-busproxy` (exposes bus tools — publish, inbox, ack) and `agent-core-channel` (inline-wake relay — wakes the agent when high-urgency mail arrives). Each sidecar proxies calls to the daemon over HTTP; each tool call opens a fresh connection so daemon restarts do not strand the agent.

**Envelope** — the universal wire format for all bus messages. Every message carries: `id`, `from_` (stamped by the bus), `to`, `kind`, `payload`, `urgency` (`green`/`yellow`/`red`), optional `expires_at`, and `correlation_id`.

## Runtime topology

```text
                ┌─────────────────────────────────────────────────────┐
                │                daemon  (one process)                 │
                │                                                      │
                │   ┌──────────────────────────────────────────────┐   │
                │   │           Bus  (SQLite mailbox)              │   │
                │   │                                              │   │
                │   │  ┌──────────────────┐  ┌─────────────────┐  │   │
                │   │  │ endpoint: wren    │  │ endpoint:       │  │   │
                │   │  │ ClaudeCodeMCP     │  │ discord         │  │   │
                │   │  └────────┬─────────┘  └────────┬────────┘  │   │
                │   └───────────│────────────────────  │  ─────────┘   │
                │               │ HTTP                  │ Discord API   │
                └───────────────│───────────────────────│───────────────┘
                                │                       └──────────▶ Discord
                                │
             ┌──────────────────┘
             ▼
       Being: wren
   ┌──────────────────────┐
   │ busproxy  (stdio MCP) │──▶ http://127.0.0.1:8789
   │ channel   (stdio MCP) │──▶ http://127.0.0.1:8789
   └──────────┬────────────┘
              │ MCP tools
              ▼
      Claude Code session

       Being: discord service
   ┌──────────────────────────┐
   │ busproxy  (stdio MCP)    │──▶ http://127.0.0.1:8789
   │ channel   (stdio MCP)    │──▶ http://127.0.0.1:8789
   └──────────────────────────┘
```

The daemon is the single process that owns everything: no being embeds the bus directly. Sidecars are the indirection layer that decouples beings from the daemon — a being's Claude Code session keeps running even if the daemon restarts, because each sidecar tool call opens a fresh HTTP connection rather than keeping a persistent socket alive. The HTTP boundary at `http://127.0.0.1:8789` is the only crossing point between beings and the daemon.

!!! note "Daemon restarts are transparent to beings"
    Because sidecars open a fresh connection per tool call, a daemon restart (for upgrades or config changes) does not strand an active agent session. The sidecar simply reconnects on the next call.

## The delivery path

An envelope's journey from sender to recipient: `handle.publish()` runs any registered `pre_publish` hooks (which may mutate or drop the envelope), then inserts the envelope into the SQLite mailbox with status `pending`. The bus then calls `endpoint.deliver(envelope)` — after running any `pre_deliver` hooks — and awaits the result. Once the endpoint processes the message it calls `bus.ack(envelope_id)`, which marks the row `acked` in the mailbox. If delivery raises `EndpointUnavailable` the envelope is requeued; any other exception moves it to `dead_letter`.

See [The bus](bus.md) for the full delivery lifecycle diagram, redelivery sweeps, TTL expiry, and dead-letter handling.

## Pluggable endpoints

Endpoint types are not hardcoded in agent-core. Third-party packages register new endpoint types by declaring an entry point under the `agent_core` group (`[project.entry-points."agent_core"]` in `pyproject.toml`). The daemon discovers and loads all registered entry points at startup, making the set of available endpoint types open-ended without modifying agent-core itself.

See [Extensions](extensions.md) for the full plugin contract: entry-point naming, how hooks and envelope kinds are contributed, and how to write your own extension.

## Read more

| Topic | Page |
|---|---|
| Bus delivery lifecycle, hooks, config | [The bus](bus.md) |
| Envelope fields and built-in kinds | [Envelopes](envelopes.md) |
| Endpoint protocol and supervision | [Endpoints](endpoints.md) |
| Plugin hooks and extension types | [Extensions](extensions.md) |
| Running the daemon | [Running the daemon](../getting-started/daemon.md) |
