# agent-core

**Core infrastructure for AI agents.** A durable, in-process message bus with a
typed envelope format, pluggable endpoints, and a plugin system — the substrate
you build an agent on when it needs to talk to other agents, tools, and humans
reliably.

!!! note "Built and run by agent beings"
    agent-core is developed and operated by AI agent beings (Wren, Pepper, and
    others) alongside their human partner. The docs are written honestly with
    that in mind: the "you" reading this might be a human developer *or* an
    agent adopting the framework — both are first-class readers.

## What it gives you

- **A message bus** that routes typed [envelopes](concepts/envelopes.md) between
  [endpoints](concepts/endpoints.md), with durable persistence, acknowledgements,
  redelivery, TTL sweeps, and per-endpoint supervision.
- **Endpoints** — the adapters that connect your agent to the outside world
  (Discord, an MCP surface, an inbound webhook, or your own). You implement a
  small `Endpoint` protocol; the bus handles delivery, retries, and identity.
- **A plugin system** — register new endpoint types, envelope kinds, renderers,
  and CLI subcommands from your own package without forking agent-core.
- **A daemon** — a long-running host process that keeps your endpoints alive and
  survives releases and restarts.

## Who it's for

- **Adopting a framework to build an agent on?** Start with
  [Getting Started](getting-started/index.md).
- **An AI agent pulling this in?** The whole documentation surface is available
  as machine-readable [`llms.txt`](https://jeffrichley.github.io/agent_core/llms.txt)
  and [`llms-full.txt`](https://jeffrichley.github.io/agent_core/llms-full.txt) for
  single-fetch ingestion.
- **Want the mental model first?** Read [Concepts](concepts/index.md).
- **Need exact signatures?** See the [API Reference](reference/index.md).

## The 60-second picture

An agent-core system is a set of **endpoints** registered on one **bus**. Each
endpoint is an adapter with a `name`, a `start()`, a `deliver()`, and a
`stop()`. Work moves between them as **envelopes** — a universal wire format
carrying a `kind`, a `from`/`to`, a `payload`, and metadata. You publish an
envelope through a `BusHandle`; the bus persists it, delivers it to the target
endpoint, waits for an ack, and retries or dead-letters on failure. A
long-running **daemon** hosts the whole thing in production.

```text
   your code ──publish──▶  Bus  ──deliver──▶  Endpoint (Discord, MCP, inbound, …)
                           │  ▲                   │
                     persist  └──── ack/nack ─────┘
                           ▼
                     SQLite mailbox (durable, redelivered, TTL-swept)
```

Ready? [Get started →](getting-started/index.md)
