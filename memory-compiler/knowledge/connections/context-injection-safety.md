---
title: "Connection: Context Injection Safety"
connects:
  - "concepts/file-injector"
  - "concepts/handoff-writer"
  - "concepts/llm-error-handling-patterns"
sources:
  - "daily/2026-04-13.md"
created: 2026-04-13
updated: 2026-04-13
---

# Connection: Context Injection Safety

## The Connection

FileInjector reads files and injects their contents into LLM session context. HandoffWriter writes files that FileInjector later reads. When HandoffWriter encounters an error, whatever it writes ends up in the next session's context via FileInjector. This creates a pipeline where error handling in writers directly affects the quality of future session context.

## Key Insight

The write-then-inject pipeline means that every tool writing files consumed by FileInjector (or its subclasses like IdentityInjector) must treat its output as future LLM context, not as a debug log. Raw error messages, stack traces, or malformed content written by upstream tools propagate silently through FileInjector into future sessions, where they can confuse the LLM or trigger unexpected behavior. The safe fallback pattern — using sentinel values for programmatic detection and clean human-readable messages for content — emerged from this realization.

## Evidence

During code review of the HandoffWriter implementation, it was discovered that LLM extraction failures caused raw error text to be written to the handoff output file. Since IdentityInjector (a FileInjector subclass) reads this file on SessionStart, the error text would be injected as identity context in the next session. The fix introduced the HANDOFF_ERROR sentinel and a safe fallback message, establishing the pattern that output files in the injection pipeline must only contain well-formed, intentional content.

## Related Concepts

- [[concepts/file-injector]] - The reader side of the pipeline
- [[concepts/handoff-writer]] - The writer side where the issue was found
- [[concepts/llm-error-handling-patterns]] - The generalized pattern extracted from this discovery
