---
title: "Memory Compiler Integration"
aliases: [memory-compiler, claude-memory-compiler]
tags: [agent-core, memory, knowledge-base]
sources:
  - "daily/2026-04-13.md"
created: 2026-04-13
updated: 2026-04-13
---

# Memory Compiler Integration

The memory compiler is a conversation-to-knowledge-base pipeline integrated into agent_core as a subdirectory (`memory-compiler/`). It was cloned from coleam00/claude-memory-compiler and adapted to work as a nested component rather than a standalone project.

## Key Points

- Cloned from coleam00/claude-memory-compiler, renamed to `memory-compiler/` subdirectory
- All path references in 7 files were updated to account for the extra directory nesting level
- Root-level `pyproject.toml` manages all dependencies — no nested pyproject.toml inside memory-compiler/
- Hooks are project-scoped (`.claude/settings.json`), not global (`~/.claude/settings.json`)
- Daily logs, reports, and runtime state files are gitignored

## Details

The original claude-memory-compiler assumes a flat directory structure where scripts and hooks live at the project root. When nesting it as a subdirectory of agent_core, all `Path(__file__).resolve().parent.parent` references needed an additional `.parent` call to reach the actual project root. This affected 7 files across the hooks and scripts directories.

The decision to keep memory-compiler as a distinct subdirectory (rather than flattening its contents into agent_core's source tree) preserves the separation of concerns between the hook tool framework and the knowledge compilation pipeline. Dependencies are consolidated at the root level to avoid nested dependency management complexity.

The hooks system forms a complete capture pipeline: SessionStart injects the knowledge base index, SessionEnd captures the conversation transcript, flush.py extracts salient information to daily logs, and compile.py synthesizes daily logs into structured knowledge articles. After 6 PM local time, flush.py auto-triggers compile.py for end-of-day compilation.

## Related Concepts

- [[concepts/memory-compiler-hooks]] - The hook system that drives automatic conversation capture
- [[concepts/pipeline-system]] - The agent_core pipeline that orchestrates hook tools
- [[concepts/agent-core-yaml-config]] - Configuration format used by agent_core

## Sources

- [[daily/2026-04-13.md]] - Initial setup of memory-compiler integration into agent_core repo
