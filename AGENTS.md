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
│   ├── hooks/                  # Codex hooks (session-end, pre-compact)
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

Conversations are automatically captured via Codex hooks:
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

Pluggable tools that run at Codex lifecycle events. Tools are Python classes
implementing the HookTool protocol, declared in `agent_core.yaml`.

### CLI Commands

```bash
# Run tools for a lifecycle event (called by Codex hooks)
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

## Running Tests

`pytest` defaults include **always-on coverage measurement** for the whole
codebase (line + branch, gated at 85% combined), plus **random test order**.
This makes a single one-off test invocation slow (2–3 min) because coverage
instruments every file, not just the ones touched by your test.

**When to use which invocation:**

| Goal | Command |
|---|---|
| Quick local debug of one test | `uv run pytest --no-cov -x packages/<pkg>/tests/test_x.py::test_name` |
| Re-run with a specific random seed (reproducing a failure) | `uv run pytest --no-cov --randomly-seed=<N>` |
| Disable random order entirely | `uv run pytest -p no:randomly --no-cov` |
| Full pre-commit gate (matches CI) | `just check` |
| Just the suite with coverage report visible | `uv run pytest --cov=packages --cov-branch --cov-report=term-missing` |

**Rules of thumb:**
- Iterating on a single test? Always pass `--no-cov`. Coverage adds 100×
  overhead for a one-test run.
- Before committing? Run `just check` — that's the gate CI runs.
- Test failed and you suspect order-dependence? Pytest prints the random
  seed at the top of every run; rerun with `--randomly-seed=<seed>` to
  reproduce, then `-p no:randomly` to isolate.
- Hit a flaky timing assertion? Use the `Clock` seam in
  `agent_core.clock` (`FakeClock` in tests, `SystemClock` in production).
  Never assert against `datetime.now()` wall-clock jitter.

## CI Gates

- **Project coverage floor: 85% combined** (line + branch). Trips if a
  large chunk of test code is deleted or skipped.
- **Patch coverage floor: 80%** on PR diffs only (via `diff-cover`).
  Trips if your PR adds untested code paths.
- Both run only on Linux in CI (one OS is enough; coverage.xml is
  OS-agnostic). Windows CI runs the test suite but not the coverage gate.

## Conventions

- Python 3.12+, managed by uv
- Ruff for linting (line-length 100)
- Knowledge articles use Obsidian-style `[[wikilinks]]`
- Daily logs are append-only and never manually edited
