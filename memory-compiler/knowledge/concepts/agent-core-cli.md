---
title: "agent-core CLI"
aliases: [cli, agent-core-command]
tags: [agent-core, cli, typer]
sources:
  - "docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# agent-core CLI

The agent-core CLI is the Typer-based command-line interface for the agent_core framework. It serves as the entrypoint that Claude Code hooks call to run the tool pipeline.

## Key Points

- Built with Typer for automatic help text and argument parsing
- Primary command: agent-core hooks run <event>
- Reads hook_input from stdin (JSON from Claude Code), outputs JSON to stdout
- Config file defaults to agent_core.yaml in cwd, overridable with --config flag
- Registered as a console script in pyproject.toml

## Commands

### agent-core hooks run

```bash
agent-core hooks run SessionStart
agent-core hooks run SessionStart --config /path/to/agent_core.yaml
echo '{"session_id": "test"}' | agent-core hooks run SessionStart
```

### Output Format

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "## Current Time\n\nMonday, April 13, 2026..."
  }
}
```

## Related Concepts

- [[concepts/pipeline-system]] - The engine the CLI invokes
- [[concepts/agent-core-yaml-config]] - Config file the CLI loads

## Sources

- [[docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md]] - Original design spec
