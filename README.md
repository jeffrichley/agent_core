# agent_core

Core infrastructure for AI agents. Consolidates memory, knowledge compilation, and shared tooling.

## Setup

```bash
uv sync
```

- **Running the bus daemon:** see [docs/setup/daemon.md](docs/setup/daemon.md) for the one-time setup and the `daemon refresh` daily flow.

## Memory Compiler

Automatic conversation capture and knowledge base compilation powered by Claude Code hooks. See `memory-compiler/AGENTS.md` for the full technical reference.

## Plugins & extensions

The bus is content-agnostic. Plugins can register first-class envelope kinds + renderers (e.g., `Desire`, `Thought`, …) without modifying agent_core. See [docs/extensions.md](docs/extensions.md) for the plugin-author quickstart and the dispatch contract.
