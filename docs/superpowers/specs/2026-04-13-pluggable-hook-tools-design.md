# Pluggable Hook Tools — Design Spec

**Date:** 2026-04-13
**Status:** Approved

## Overview

A pluggable tool system for agent_core that runs registered Python tools at Claude Code lifecycle events. Tools inject context (heading + content) into sessions via the `additionalContext` mechanism. Tools are declared in a YAML config file and implement a well-defined protocol.

agent_core is a distributable Python library. Each agent that uses it brings its own config file declaring which tools it wants at each lifecycle event. For now, agent_core itself has a local config for development.

## Supported Events

Tools can be registered for any Claude Code hook event that supports `additionalContext` injection:

| Event | When it fires |
|-------|--------------|
| `SessionStart` | Session begins or resumes |
| `PreToolUse` | Before a tool call executes |
| `PostToolUse` | After a tool call succeeds |
| `PostToolUseFailure` | After a tool call fails |
| `SubagentStart` | When a subagent is spawned |
| `UserPromptSubmit` | When user submits a prompt |

## Project Layout

```
agent_core/
├── src/
│   └── agent_core/
│       ├── __init__.py
│       ├── models.py              # Shared Pydantic models (ToolResult, ToolConfig, PipelineConfig)
│       ├── hooks/
│       │   ├── __init__.py
│       │   ├── protocol.py        # HookTool protocol definition
│       │   ├── pipeline.py        # Pipeline class (load, run, render)
│       │   └── tools/             # Built-in hook tools
│       │       ├── __init__.py
│       │       └── time_injector.py
│       └── cli.py                 # Typer CLI entrypoint
├── agent_core.yaml                # Local pipeline config (per-agent in the future)
├── memory-compiler/               # Existing, unchanged
├── pyproject.toml
└── CLAUDE.md
```

## Tool Protocol

Every hook tool implements the `HookTool` protocol:

```python
from typing import Protocol
from agent_core.models import ToolResult

class HookTool(Protocol):
    """Protocol that all hook tools must implement.

    Hook tools are registered in agent_core.yaml and executed by the pipeline
    when their associated lifecycle event fires. Each tool returns a heading
    and content that get compiled into the markdown context document.
    """

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        """Execute the tool and return a result to inject into context.

        Args:
            event: The lifecycle event name (e.g., "SessionStart").
            hook_input: Data from Claude Code — varies by event. Always includes
                session_id, transcript_path, cwd, permission_mode. Event-specific
                fields include tool_name (PreToolUse), agent_type (SubagentStart),
                prompt (UserPromptSubmit), etc.
            params: Tool-specific configuration from agent_core.yaml. Defined per
                tool registration, allowing the same tool class to behave
                differently in different pipelines.

        Returns:
            ToolResult with heading (markdown heading text) and content
            (markdown body for that section).
        """
        ...
```

## Pydantic Models

```python
from pydantic import BaseModel

class ToolResult(BaseModel):
    """The output of a single hook tool execution.

    Each tool returns a heading and content. The pipeline compiles these
    into a markdown document with ## headings separated by --- dividers.
    """
    heading: str
    content: str

class ToolConfig(BaseModel):
    """Configuration for a single tool registration in the pipeline.

    Declared in agent_core.yaml under a pipeline event.
    """
    tool: str       # Fully qualified class path, e.g. "agent_core.hooks.tools.time_injector.TimeInjector"
    params: dict = {}  # Tool-specific parameters passed to execute()

class PipelineConfig(BaseModel):
    """Root configuration model, validated from agent_core.yaml.

    Maps lifecycle event names to ordered lists of tool configurations.
    """
    pipelines: dict[str, list[ToolConfig]]
```

## Pipeline

The `Pipeline` class is the core engine:

```python
class Pipeline:
    """Loads tool configuration, instantiates tools, and runs them for lifecycle events.

    The pipeline is the bridge between Claude Code hooks and the registered tools.
    It reads agent_core.yaml, validates the config with Pydantic, dynamically imports
    tool classes, and executes them in declared order.
    """

    def __init__(self, config_path: Path):
        """Load and validate pipeline configuration from YAML."""
        ...

    def run(self, event: str, hook_input: dict) -> list[ToolResult]:
        """Execute all tools registered for an event, in declared order.

        Tools that are not registered for the given event are skipped.
        Each tool is instantiated fresh for every run.
        """
        ...

    def render(self, results: list[ToolResult]) -> str:
        """Compile tool results into a single markdown document.

        Each ToolResult becomes a ## heading followed by its content,
        separated by --- dividers.
        """
        ...
```

### Rendered Output Example

For a SessionStart event with two registered tools, the pipeline produces:

```markdown
## Current Time

Monday, April 13, 2026 10:45 AM CDT

---

## Knowledge Base Index

| Article | Summary | Compiled From | Updated |
|---------|---------|---------------|---------|
```

This markdown string is placed into the `additionalContext` field of the hook JSON output that Claude Code consumes.

## CLI

Typer-based CLI serves as the entrypoint for Claude Code hooks:

```python
app = typer.Typer()
hooks_app = typer.Typer()
app.add_typer(hooks_app, name="hooks")

@hooks_app.command("run")
def run_hook(
    event: str,
    config: Path = typer.Option("agent_core.yaml", help="Path to pipeline config"),
):
    """Execute all tools registered for a hook event.

    Reads hook_input from stdin (JSON from Claude Code), runs the pipeline,
    and outputs the formatted JSON response to stdout.
    """
    ...
```

### CLI Usage

```bash
# Default config (agent_core.yaml in cwd)
agent-core hooks run SessionStart

# Explicit config path (for per-agent configs)
agent-core hooks run SessionStart --config /path/to/my-agent/agent_core.yaml
```

### Hook JSON Output

The CLI outputs the JSON structure Claude Code expects:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "## Current Time\n\nMonday, April 13, 2026 10:45 AM CDT\n\n---\n\n## Knowledge Base Index\n\n..."
  }
}
```

## YAML Config Format

```yaml
# agent_core.yaml — Pipeline configuration
#
# Each event maps to an ordered list of tools that run when that event fires.
# Tools are Python classes implementing the HookTool protocol.
# The same tool can appear in multiple events with different params.

pipelines:
  SessionStart:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector
      params:
        format: "%A, %B %d, %Y %I:%M %p %Z"

  PreToolUse:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector

  SubagentStart:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector
```

## Claude Code Hook Config

`.claude/settings.json` calls the CLI instead of individual Python scripts:

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "uv run agent-core hooks run SessionStart",
        "timeout": 15
      }]
    }],
    "PreToolUse": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "uv run agent-core hooks run PreToolUse",
        "timeout": 10
      }]
    }],
    "PostToolUse": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "uv run agent-core hooks run PostToolUse",
        "timeout": 10
      }]
    }],
    "PostToolUseFailure": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "uv run agent-core hooks run PostToolUseFailure",
        "timeout": 10
      }]
    }],
    "SubagentStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "uv run agent-core hooks run SubagentStart",
        "timeout": 10
      }]
    }],
    "UserPromptSubmit": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "uv run agent-core hooks run UserPromptSubmit",
        "timeout": 10
      }]
    }]
  }
}
```

## Built-in Tool: TimeInjector

The first tool, serving as the reference implementation:

```python
class TimeInjector:
    """Injects the current date and time into the session context.

    Solves the common problem of agents not knowing what time it is.
    Configurable format string via params.

    Example YAML config:
        - tool: agent_core.hooks.tools.time_injector.TimeInjector
          params:
            format: "%A, %B %d, %Y %I:%M %p %Z"

    Example output:
        ToolResult(
            heading="Current Time",
            content="Monday, April 13, 2026 10:45 AM CDT"
        )
    """

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        fmt = params.get("format", "%A, %B %d, %Y %I:%M %p %Z")
        now = datetime.now(timezone.utc).astimezone()
        return ToolResult(
            heading="Current Time",
            content=now.strftime(fmt),
        )
```

## Logging

Rich logging throughout the framework:

- Pipeline loading logs which tools were registered for which events
- Each tool execution logs timing and success/failure
- Errors in individual tools are logged but don't crash the pipeline — other tools still run
- Log level configurable via CLI flag or environment variable

## Dependencies

Added to `pyproject.toml`:

- `pydantic` — models and config validation
- `typer` — CLI framework
- `rich` — logging and terminal output
- `pyyaml` — config file parsing

## Documentation

Every module, class, and method gets thorough docstrings. Additionally, knowledge wiki articles will be created in `memory-compiler/knowledge/concepts/` covering:

- `hook-tool-protocol` — how to write a tool, with complete examples
- `pipeline-system` — how the pipeline loads, runs, and renders
- `agent-core-yaml-config` — config format reference with examples
- `agent-core-cli` — CLI commands and usage

## Future Direction

- **Per-agent configs:** Each agent brings its own `agent_core.yaml`. The `--config` flag already supports this.
- **PyPI packaging:** agent_core becomes an installable library. Agents declare it as a dependency.
- **Mutation tools:** Tools that transform context flowing through the pipeline (deferred — current design is inject-only).
- **Additional tool types:** Beyond hooks — skills, memory, integrations — as sibling packages under `agent_core`.
