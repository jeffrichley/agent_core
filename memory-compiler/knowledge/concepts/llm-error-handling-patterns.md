---
title: "LLM Error Handling Patterns"
aliases: [llm-error-handling, safe-fallback-pattern]
tags: [patterns, llm, error-handling]
sources:
  - "daily/2026-04-13.md"
created: 2026-04-13
updated: 2026-04-13
---

# LLM Error Handling Patterns

When an LLM call fails in a tool that writes output consumed by future sessions, the error response must use a safe fallback message rather than raw error text. Writing raw error strings into files that get injected into future contexts can confuse downstream consumers and pollute the knowledge pipeline.

## Key Points

- Never write raw error text into files that will be injected into future LLM sessions
- Use a defined sentinel value (e.g., `HANDOFF_ERROR`) for programmatic detection of failures
- Write a human-readable safe fallback message (e.g., "Handoff extraction failed") for the content
- Log the full error details separately for debugging — don't discard them, just don't propagate them into context
- The `HANDOFF_EMPTY` sentinel pattern handles the trivial-session case distinctly from errors

## Details

The HandoffWriter tool uses the Claude Agent SDK to extract continuity notes from conversation transcripts. When this LLM call fails — due to API errors, rate limits, or malformed input — the original implementation wrote the raw error text directly into the handoff output file. Since this file gets read by IdentityInjector on the next SessionStart, the raw error would be injected into the new session's context, potentially confusing the LLM or triggering unexpected behavior.

The corrected pattern uses a two-tier approach: a sentinel value (`HANDOFF_ERROR`) that programmatic consumers can check, paired with a safe human-readable fallback message ("Handoff extraction failed") written to the output file. The full error details are logged via the standard logging system for debugging purposes but never propagated into the context injection pipeline.

This pattern generalizes to any tool in the pipeline that writes files consumed by future sessions. The principle is: output files are part of the LLM's future context, so they should only contain well-formed, intentional content. Error details belong in logs, not in context.

## Related Concepts

- [[concepts/handoff-writer]] - The tool where this pattern was applied
- [[concepts/file-injector]] - IdentityInjector reads the files that must contain safe content
- [[concepts/hook-tool-protocol]] - The protocol framework within which error handling operates

## Sources

- [[daily/2026-04-13.md]] - HANDOFF_ERROR safe fallback fix during code review remediation
