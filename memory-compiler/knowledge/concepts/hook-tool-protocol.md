---
title: "HookTool Protocol"
aliases: [hook-tool, tool-protocol]
tags: [agent-core, hooks, protocol]
sources:
  - "docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# HookTool Protocol

The HookTool protocol is the interface that all hook tools in agent_core must implement. It uses Python's structural subtyping (typing.Protocol) — tools don't inherit from a base class, they just need an execute() method with the correct signature.

## Key Points

- Tools are plain Python classes with a single required method: execute()
- The protocol is @runtime_checkable, so the pipeline verifies compliance at load time
- Tools receive three arguments: the event name, hook_input from Claude Code, and params from YAML config
- Tools return a ToolResult(heading, content) — the pipeline compiles these into markdown
- Tools are stateless — each invocation gets a fresh instance

## The Protocol

```python
from typing import Protocol, runtime_checkable
from agent_core.models import ToolResult

@runtime_checkable
class HookTool(Protocol):
    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        ...
```

## Writing a Tool

A minimal tool that satisfies the protocol:

```python
from agent_core.models import ToolResult

class GreetingTool:
    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        name = params.get("name", "World")
        return ToolResult(heading="Greeting", content=f"Hello, {name}!")
```

Register it in agent_core.yaml:

```yaml
pipelines:
  SessionStart:
    - tool: my_tools.GreetingTool
      params:
        name: Jeff
```

## Arguments

- **event** (str): The lifecycle event name — SessionStart, PreToolUse, PostToolUse, PostToolUseFailure, SubagentStart, or UserPromptSubmit.
- **hook_input** (dict): Data from Claude Code. Always includes session_id, transcript_path, cwd, permission_mode. Event-specific fields vary.
- **params** (dict): Tool-specific config from agent_core.yaml. Empty dict if no params declared.

## Related Concepts

- [[concepts/pipeline-system]] - The runner that loads and executes tools
- [[concepts/agent-core-yaml-config]] - How tools are registered in YAML

## Sources

- [[docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md]] - Original design spec
