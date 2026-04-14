---
title: "Memory Compiler Hooks"
aliases: [session-hooks, conversation-capture-hooks]
tags: [agent-core, memory, hooks]
sources:
  - "daily/2026-04-13.md"
created: 2026-04-13
updated: 2026-04-13
---

# Memory Compiler Hooks

The memory compiler uses three Claude Code lifecycle hooks to automatically capture conversation context and compile it into the knowledge base. These hooks are configured in `.claude/settings.json` at the project level.

## Key Points

- Three hooks: SessionStart (inject knowledge), SessionEnd (capture transcript), PreCompact (safety net before context loss)
- Hooks are project-scoped — configured in `.claude/settings.json`, not the global settings
- flush.py runs as a fully detached background process spawned by SessionEnd and PreCompact
- After 6 PM local time, flush.py auto-triggers compile.py for end-of-day compilation
- Recursion guard via `CLAUDE_INVOKED_BY` env var prevents infinite hook loops

## Details

The three hooks form a complete conversation capture pipeline. SessionStart runs at the beginning of every session and injects the knowledge base index into context, giving Claude awareness of what has already been learned. SessionEnd fires when a session closes and captures the full conversation transcript. PreCompact fires before Claude Code's automatic context compaction, which is critical for long sessions where intermediate context would otherwise be lost to summarization.

Both SessionEnd and PreCompact spawn flush.py as a fully detached background process. On Windows this uses `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` flags; on Mac/Linux it uses `start_new_session=True`. This ensures the extraction process survives after Claude Code's hook process exits. Deduplication logic prevents the same session from being flushed twice within 60 seconds.

The auto-compilation behavior is a key design feature: when flush.py runs after 6 PM and detects that the day's log has changed since its last compilation (tracked via SHA-256 hash in `state.json`), it spawns compile.py as another detached process. This eliminates the need for cron jobs or manual compilation triggers.

## Related Concepts

- [[concepts/memory-compiler-integration]] - How the memory compiler fits into agent_core
- [[concepts/handoff-writer]] - Another hook tool that captures context before loss
- [[concepts/transcript-reader]] - Utility used to parse the JSONL transcripts captured by these hooks

## Sources

- [[daily/2026-04-13.md]] - Setup of hooks during initial integration, details on auto-compilation behavior
