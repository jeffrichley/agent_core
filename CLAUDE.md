# agent_core

Core infrastructure for AI agents. This repo consolidates agent tooling, memory systems, and shared components.

## Project Structure

```
agent_core/
├── src/
│   └── agent_core/             # Installable Python package
│       ├── models.py           # Shared Pydantic models (ToolResult, ToolConfig, PipelineConfig)
│       ├── cli.py              # Typer CLI entrypoint
│       └── hooks/              # Pluggable hook tool system
│           ├── protocol.py     # HookTool protocol definition
│           ├── pipeline.py     # Pipeline class (load, run, render)
│           └── tools/          # Built-in hook tools
│               └── time_injector.py
├── memory-compiler/            # Conversation -> knowledge base pipeline
│   ├── hooks/                  # Claude Code hooks (session-end, pre-compact)
│   ├── scripts/                # CLI tools (compile, query, lint, flush)
│   ├── daily/                  # Daily conversation logs (gitignored, auto-generated)
│   ├── knowledge/              # Compiled knowledge base (LLM-owned)
│   │   ├── index.md            # Master catalog
│   │   ├── log.md              # Build log
│   │   ├── concepts/           # Atomic knowledge articles
│   │   ├── connections/        # Cross-cutting insights
│   │   └── qa/                 # Filed query answers
│   ├── reports/                # Lint reports (gitignored)
│   └── AGENTS.md               # Schema for the knowledge base compiler
├── agent_core.yaml             # Pipeline config (which tools run at which events)
└── tests/                      # Test suite
```

## Memory Compiler

Conversations are automatically captured via Claude Code hooks:
- **SessionStart** injects the knowledge base index into every session
- **SessionEnd** extracts conversation context and flushes to daily logs
- **PreCompact** captures context before auto-compaction discards it

### CLI Commands

```bash
# Compile daily logs into knowledge articles
uv run python memory-compiler/scripts/compile.py

# Query the knowledge base
uv run python memory-compiler/scripts/query.py "your question here"

# Lint the knowledge base
uv run python memory-compiler/scripts/lint.py
```

## Hook Tool Pipeline

Pluggable tools that run at Claude Code lifecycle events. Tools are Python classes
implementing the HookTool protocol, declared in `agent_core.yaml`.

### CLI Commands

```bash
# Run tools for a lifecycle event (called by Claude Code hooks)
agent-core hooks run SessionStart

# Run with explicit config
agent-core hooks run SessionStart --config /path/to/config.yaml
```

### Writing a Tool

Tools implement the HookTool protocol — a single `execute()` method:

```python
from agent_core.models import ToolResult

class MyTool:
    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        return ToolResult(heading="My Heading", content="My content")
```

Register in `agent_core.yaml`:

```yaml
pipelines:
  SessionStart:
    - tool: my_package.my_module.MyTool
      params:
        key: value
```

## Conventions

- Python 3.12+, managed by uv
- Ruff for linting (line-length 100)
- Knowledge articles use Obsidian-style `[[wikilinks]]`
- Daily logs are append-only and never manually edited
