---
title: "Pipeline System"
aliases: [pipeline, hook-pipeline]
tags: [agent-core, hooks, pipeline]
sources:
  - "docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# Pipeline System

The Pipeline class is the core engine of agent_core's hook tool system. It loads YAML configuration, dynamically imports tool classes, executes them in order, and compiles their outputs into a markdown document for Claude Code context injection.

## Key Points

- Pipeline reads agent_core.yaml and validates it with Pydantic (PipelineConfig model)
- Tools are dynamically imported from their fully qualified class paths at runtime
- Tools execute in the order declared in YAML — order matters
- Failed tools are logged and skipped — remaining tools still run
- Output is compiled markdown: ## headings separated by --- dividers

## Lifecycle

1. Pipeline(config_path) — load and validate YAML config
2. pipeline.run(event, hook_input) — instantiate and execute tools for an event
3. pipeline.render(results) — compile ToolResult list into markdown string

## Details

The pipeline is designed for resilience. If a tool can't be imported or raises an exception during execute(), the error is logged with Rich and that tool is skipped. Dynamic import uses Python's importlib. Each tool gets a fresh instance per run.

## Rendered Output Format

```markdown
## First Tool Heading

First tool content here.

---

## Second Tool Heading

Second tool content here.
```

## Related Concepts

- [[concepts/hook-tool-protocol]] - The interface tools must implement
- [[concepts/agent-core-yaml-config]] - Configuration format
- [[concepts/agent-core-cli]] - CLI that invokes the pipeline

## Sources

- [[docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md]] - Original design spec
