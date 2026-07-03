# agent_core

[![CI](https://github.com/jeffrichley/agent_core/actions/workflows/ci.yml/badge.svg)](https://github.com/jeffrichley/agent_core/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/jeffrichley/agent_core/branch/main/graph/badge.svg)](https://codecov.io/gh/jeffrichley/agent_core)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org)
[![Renovate enabled](https://img.shields.io/badge/renovate-enabled-brightgreen.svg)](https://renovatebot.com)
[![built by agent beings](https://img.shields.io/badge/built%20by-agent%20beings%20%F0%9F%AA%B6-8A2BE2.svg)](#)

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
