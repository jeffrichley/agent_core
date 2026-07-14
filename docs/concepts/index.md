# Concepts

agent-core is built around one central idea: **endpoints on a bus, work as envelopes**.

Every participant in the system — an AI agent, a Discord adapter, a scheduler, a stub for testing — is an **endpoint**: an addressable name that can receive and send messages. Those messages are **envelopes**: a universal wire format carrying a typed payload, routing metadata, and an urgency level. The **bus** is the in-process router that connects them: it persists every envelope to a durable SQLite mailbox, dispatches to the target endpoint, waits for acknowledgment, and retries or dead-letters if delivery fails. Finally, **extensions** let third-party packages contribute new endpoint types, new envelope kinds, bus hooks, and CLI subcommands — without modifying agent-core itself.

These four concepts build on each other in order:

| Concept | What it does |
|---|---|
| [Bus](bus.md) | Routes, persists, retries, and supervises everything |
| [Envelopes](envelopes.md) | The universal wire format all participants share |
| [Endpoints](endpoints.md) | Addressable participants; the deliver/ack contract |
| [Extensions](extensions.md) | How plugins contribute new types and behaviors |

Read the pages in order if you are new to agent-core. If you are looking for exact field names and method signatures, see the [API Reference](../reference/index.md).
