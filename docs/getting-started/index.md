# Getting Started

agent-core is a message-bus runtime for AI agent beings. It lets multiple agents and services exchange typed envelopes over a durable SQLite-backed bus, with a config-driven endpoint model and a supervised daemon that survives reboots and rolling releases.

You can adopt it as a human developer building an agentic system, or as an AI agent instrumenting your own communication layer. Both are first-class uses.

## Prerequisites

- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/) — agent-core is a uv workspace monorepo

## Install

```bash
uv sync
```

This resolves and installs the full workspace, including the core package (`agent-core`, importable as `agent_core`), from `packages/core/src/agent_core/`.

## Configure

Agent-core reads an `agent_core.yaml` at your project root. The top-level shape is:

```yaml
bus:
  storage_path: "~/.agent-core/bus.sqlite"   # tilde expansion supported

http:
  bind_host: "127.0.0.1"
  bind_port: 8789

endpoints:
  - type: builtin.claude_code_mcp   # endpoint type — builtin or plugin-provided
    name: my-agent
    params:
      mount: /mcp/my-agent

  - type: builtin.stub              # in-memory adapter useful for dev/test
    name: echo
```

Each entry in `endpoints[]` declares one addressable participant on the bus. The `type:` field selects the adapter; built-in types include `builtin.stub`, `builtin.discord`, `builtin.claude_code_mcp`, and others contributed by plugins. The daemon resolves plugin types at startup via Python entry points.

!!! tip "Real-world examples"
    See [`docs/examples/pepper-agent-core.yaml`](https://github.com/jeffrichley/agent_core/blob/main/docs/examples/pepper-agent-core.yaml) for a production-shaped config with session hooks, handoff pipelines, and bus logging.

## Next steps

| Page | What it covers |
|---|---|
| [Your first agent](first-agent.md) | Write and run a minimal endpoint; publish and receive an envelope |
| [Running the daemon](daemon.md) | One-time prod setup, release upgrades, and the source-iteration loop |
| [Concepts](../concepts/index.md) | Bus, envelopes, endpoints, hooks — the mental model |
