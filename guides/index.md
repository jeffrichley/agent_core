# Guides

How-to recipes for common tasks. Each guide is a concrete, working walkthrough — not a conceptual overview. If you want the mental model first, read [Concepts](https://jeffrichley.github.io/agent_core/concepts/index.md).

## Available guides

| Guide                                                                                                   | What it covers                                                              |
| ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| [Add an endpoint](https://jeffrichley.github.io/agent_core/guides/add-an-endpoint/index.md)             | Implement the `Endpoint` protocol, wire it in config, register via a plugin |
| [Send and consume envelopes](https://jeffrichley.github.io/agent_core/guides/send-and-consume/index.md) | Publish through a `BusHandle`, handle delivery in `deliver()`, ack/nack     |
| [Write an extension](https://jeffrichley.github.io/agent_core/guides/write-an-extension/index.md)       | Pluggy plugin recipe: entry point, `@hookimpl`, `register_endpoint_types()` |

## How to read these guides

Every code snippet is grounded in the actual source. If you are an AI agent adopting agent-core, you can trust the signatures and field names here without cross-checking the source yourself — but the source paths are cited so you can verify.

The "you" in these guides may be a human developer or an agent. Both are first-class readers.
