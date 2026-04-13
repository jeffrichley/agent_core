---
title: "Transcript Reader"
aliases: [transcript-reader, jsonl-reader]
tags: [agent-core, utility, transcript]
sources:
  - "docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# Transcript Reader

The transcript reader (agent_core.transcript) is a shared utility that extracts conversation turns from Claude Code's JSONL transcript format. It replaces duplicated extraction logic in the memory-compiler hooks.

## Key Points

- Parses JSONL line by line, extracts user and assistant messages
- Handles content as string or list of content blocks
- Filters to user/assistant roles only, skips system messages
- Configurable max_turns and max_chars limits
- Returns (formatted_markdown, turn_count) tuple
- Gracefully handles missing files, empty transcripts, malformed JSON
- Located at src/agent_core/transcript.py

## Related Concepts

- [[concepts/handoff-writer]] - Primary consumer of this utility
- [[concepts/pipeline-system]] - Part of the agent_core framework

## Sources

- [[docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md]] - Design spec
