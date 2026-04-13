---
title: "HandoffWriter"
aliases: [handoff-writer, continuity-notes]
tags: [agent-core, hooks, tools, llm]
sources:
  - "docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# HandoffWriter

HandoffWriter is a hook tool that writes structured continuity notes before context is lost. It uses LLM extraction via the Claude Agent SDK to analyze the conversation transcript and produce a handoff note covering topics, decisions, emotional temperature, open threads, and observations.

## Key Points

- Register on both PreCompact and SessionEnd for maximum coverage
- Uses Claude Agent SDK with allowed_tools=[] for text-only extraction
- Costs ~$0.02-0.05 per invocation — accurate but not free
- Deduplication prevents duplicate handoffs when both events fire for same session
- Recursion guard via CLAUDE_INVOKED_BY env var prevents infinite loops
- HANDOFF_EMPTY sentinel for trivial sessions
- agent_name param controls the LLM's perspective in the prompt
- Uses shared transcript reader from agent_core.transcript

## Related Concepts

- [[concepts/file-injector]] - IdentityInjector loads the handoff note on next session
- [[concepts/transcript-reader]] - Shared utility for reading JSONL transcripts
- [[concepts/hook-tool-protocol]] - The protocol HandoffWriter implements

## Sources

- [[docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md]] - Design spec
