---
title: "agent_core.yaml Configuration"
aliases: [yaml-config, pipeline-config]
tags: [agent-core, config, yaml]
sources:
  - "docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# agent_core.yaml Configuration

The agent_core.yaml file declares which tools run at each Claude Code lifecycle event. It's the central configuration for the hook tool pipeline.

## Key Points

- YAML format — readable, supports comments, handles nesting well
- Validated at load time by Pydantic (PipelineConfig model)
- Maps event names to ordered lists of tool class paths
- Same tool class can appear in multiple events with different params
- Each agent brings its own config — agent_core is just the framework

## Format

```yaml
pipelines:
  <EventName>:
    - tool: <fully.qualified.ClassName>
      params:
        key: value
```

## Supported Events

| Event | When it fires |
|-------|--------------|
| SessionStart | Session begins or resumes |
| PreToolUse | Before a tool call executes |
| PostToolUse | After a tool call succeeds |
| PostToolUseFailure | After a tool call fails |
| SubagentStart | When a subagent is spawned |
| UserPromptSubmit | When user submits a prompt |

## Related Concepts

- [[concepts/hook-tool-protocol]] - The interface tools must implement
- [[concepts/pipeline-system]] - The engine that reads this config
- [[concepts/agent-core-cli]] - CLI that loads this config

## Sources

- [[docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md]] - Original design spec
