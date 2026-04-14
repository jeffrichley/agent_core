# Knowledge Base Index

| Article | Summary | Compiled From | Updated |
|---------|---------|---------------|---------|
| [[concepts/hook-tool-protocol]] | HookTool Protocol — interface all hook tools implement, with execute() method returning ToolResult | docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md | 2026-04-13 |
| [[concepts/pipeline-system]] | Pipeline class — loads YAML config, imports tools, runs them in order, renders markdown output | docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md | 2026-04-13 |
| [[concepts/agent-core-cli]] | Typer CLI entrypoint — agent-core hooks run command wired to Claude Code hooks | docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md | 2026-04-13 |
| [[concepts/agent-core-yaml-config]] | agent_core.yaml config format — maps lifecycle events to ordered tool lists with params | docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md | 2026-04-13 |
| [[concepts/file-injector]] | FileInjector + IdentityInjector — generic file reader with identity subclass | docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md | 2026-04-13 |
| [[concepts/handoff-writer]] | HandoffWriter — LLM-powered continuity notes written before context loss | docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md | 2026-04-13 |
| [[concepts/transcript-reader]] | Shared JSONL transcript reader utility for extracting conversation turns | docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md | 2026-04-13 |
| [[concepts/memory-compiler-integration]] | Memory compiler setup — cloned from claude-memory-compiler, nested as subdirectory with path adjustments | daily/2026-04-13.md | 2026-04-13 |
| [[concepts/memory-compiler-hooks]] | Three Claude Code hooks (SessionStart/End/PreCompact) for automatic conversation capture and compilation | daily/2026-04-13.md | 2026-04-13 |
| [[concepts/parallel-code-review]] | Pattern for running multiple AI code review agents simultaneously on independent modules | daily/2026-04-13.md | 2026-04-13 |
| [[concepts/path-derivation-patterns]] | Derive paths from config/params, not __file__ traversal — fragile when packaged or restructured | daily/2026-04-13.md | 2026-04-13 |
| [[concepts/llm-error-handling-patterns]] | Safe fallback pattern for LLM errors in tools that write to context-injected files | daily/2026-04-13.md | 2026-04-13 |
| [[connections/context-injection-safety]] | Write-then-inject pipeline safety — upstream errors propagate through FileInjector into future sessions | daily/2026-04-13.md | 2026-04-13 |
