---
title: "HandoffWriter"
aliases: [handoff-writer, continuity-notes]
tags: [agent-core, hooks, tools, llm]
sources:
  - "docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md"
  - "daily/2026-04-13.md"
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
- HANDOFF_EMPTY sentinel for trivial sessions; HANDOFF_ERROR sentinel with safe fallback message for failed extractions
- agent_name param controls the LLM's perspective in the prompt
- Uses shared transcript reader from agent_core.transcript
- State file path derived from `output_path` param, not `__file__` traversal — avoids breakage when installed as a package
- Extracted `_build_header()` helper eliminates 3x duplication of header string construction

## Details

During code review, two critical issues were identified and fixed. First, the state file path was originally derived by walking up parent directories from `__file__`, which breaks when the package is installed via pip since the installed location differs from the development layout. The fix derives the state file path from the `output_path` parameter already available in the tool's YAML config. Second, when the LLM extraction call fails, the tool now writes a safe fallback message ("Handoff extraction failed") instead of raw error text, since the output file gets injected into future sessions via IdentityInjector.

A `_build_header()` helper method was extracted to eliminate triple duplication of the header string construction logic used across the normal, empty, and error code paths.

The `cwd` parameter for `ClaudeAgentOptions` in the `extract_handoff` call was noted as a spec deviation but not yet fixed — it's unclear whether the Agent SDK requires it. Additionally, if the pipeline runner ever becomes async, the `asyncio.run()` call in HandoffWriter would need to be replaced with `await`.

## Related Concepts

- [[concepts/file-injector]] - IdentityInjector loads the handoff note on next session
- [[concepts/transcript-reader]] - Shared utility for reading JSONL transcripts
- [[concepts/hook-tool-protocol]] - The protocol HandoffWriter implements

- [[concepts/llm-error-handling-patterns]] - Safe fallback pattern applied to HandoffWriter errors
- [[concepts/path-derivation-patterns]] - Why state file path was changed from __file__ traversal to config-based

## Sources

- [[docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md]] - Design spec
- [[daily/2026-04-13.md]] - Code review findings: state file path fix, error handling fix, _build_header extraction
