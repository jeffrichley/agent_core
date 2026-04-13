# FileInjector + HandoffWriter — Design Spec

**Date:** 2026-04-13
**Status:** Approved

## Overview

Two new hook tools for the agent_core pipeline:

1. **FileInjector** — generic base tool that reads a configurable list of files and injects their contents into session context. **IdentityInjector** is a thin subclass with identity-appropriate defaults.
2. **HandoffWriter** — writes a structured continuity note before context is lost, using LLM extraction via the Claude Agent SDK to analyze the conversation transcript.

Both originated from Pepper's requirements (`docs/requirements/pepper-requirements.md`) but are designed as generic, reusable tools any agent can configure.

## Tool 1: FileInjector + IdentityInjector

### Purpose

Read a list of files from a base path and inject their concatenated contents into the session as a single ToolResult. This is the generic "load files into context" tool.

### Class Hierarchy

```
FileInjector (base)          — generic file reader, fully configurable via params
  └── IdentityInjector       — thin subclass, sets identity-appropriate defaults
```

### FileInjector

Implements the HookTool protocol. Reads files listed in `params["files"]` relative to `params["base_path"]`, concatenates them with markdown headings, and returns a single ToolResult.

```python
class FileInjector:
    """Reads a list of files and injects their contents into session context.

    Generic tool for loading any set of files into a Claude Code session.
    Files are read in order and concatenated with ## headings derived from
    the filename. Configurable via YAML params.

    Example config:
        - tool: agent_core.hooks.tools.file_injector.FileInjector
          params:
            base_path: "/path/to/files"
            files: ["readme.md", "config/settings.md"]
            heading: "Project Context"
            missing_file_behavior: "skip"
    """

    # Default values — subclasses override these
    DEFAULT_HEADING = "Injected Files"
    DEFAULT_MISSING_BEHAVIOR = "skip"

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        ...
```

#### Params

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `base_path` | str | (required) | Root directory for file resolution |
| `files` | list[str] | (required) | Ordered list of file paths relative to base_path |
| `heading` | str | `"Injected Files"` | The ToolResult heading |
| `missing_file_behavior` | str | `"skip"` | `"skip"` = silently omit, `"warn"` = include a note, `"error"` = raise exception |

#### Output

**Heading:** Value of `heading` param

**Content:** Each file's contents preceded by a `## filename` heading:

```markdown
## SOUL.md

[contents of SOUL.md]

## preferences.md

[contents of preferences.md]
```

#### Behavior

- Files are read as UTF-8. Handle BOM if present (Windows files sometimes have BOM).
- If a file doesn't exist, behavior depends on `missing_file_behavior`:
  - `"skip"` — silently omit the file from output
  - `"warn"` — include a section with the heading and content `"(file not found: {path})"`
  - `"error"` — raise an exception (pipeline will log and skip this tool)
- If ALL files are missing and behavior is `"skip"`, return a ToolResult with empty content.
- Use `pathlib.Path` for cross-platform path handling.
- `base_path` and `files` are both required params. If either is missing, raise a clear error.

### IdentityInjector

Thin subclass that overrides defaults for identity injection:

```python
class IdentityInjector(FileInjector):
    """Injects agent identity files into session context.

    Thin subclass of FileInjector with identity-appropriate defaults.
    Use this when loading personality, preferences, and continuity files
    that define who an agent is.

    Example config:
        - tool: agent_core.hooks.tools.identity_injector.IdentityInjector
          params:
            base_path: "C:\\Users\\jeffr\\.pepper\\Memory"
            files: ["SOUL.md", "pepper/preferences.md", "pepper/handoff.md"]
    """

    DEFAULT_HEADING = "Identity"
    DEFAULT_MISSING_BEHAVIOR = "skip"
```

That's it — no logic override. It's configuration-as-code. The subclass exists so Pepper's YAML reads `IdentityInjector` instead of `FileInjector`, and so the defaults are identity-appropriate without needing to spell them out in every config.

### Example YAML (Pepper)

```yaml
pipelines:
  SessionStart:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector
      params:
        format: "%A, %B %d, %Y %I:%M %p %Z"
    - tool: agent_core.hooks.tools.identity_injector.IdentityInjector
      params:
        base_path: "C:\\Users\\jeffr\\.pepper\\Memory"
        files:
          - "SOUL.md"
          - "pepper/preferences.md"
          - "pepper/handoff.md"
```

---

## Tool 2: HandoffWriter

### Purpose

Before context is lost (PreCompact or SessionEnd), read the conversation transcript, use LLM extraction to summarize what was happening, and write a structured handoff note to a file so the next session has continuity.

### Architecture

HandoffWriter uses the Claude Agent SDK (same pattern as `memory-compiler/scripts/flush.py`) to call Claude with the transcript and get back a structured handoff note. This costs ~$0.02-0.05 per invocation but produces accurate, nuanced extraction that captures topics, decisions, emotional tone, and open threads.

```python
class HandoffWriter:
    """Writes a structured continuity note before context is lost.

    Uses LLM extraction via the Claude Agent SDK to analyze the conversation
    transcript and produce a handoff note with topics, decisions, emotional
    temperature, and open threads. The note is written to a file that gets
    loaded by IdentityInjector on the next session start.

    This tool should be registered on both PreCompact and SessionEnd events
    to maximize coverage.

    Example config:
        - tool: agent_core.hooks.tools.handoff_writer.HandoffWriter
          params:
            output_path: "C:\\Users\\jeffr\\.pepper\\Memory\\pepper\\handoff.md"
            transcript_tail_lines: 200
            timezone: "US/Eastern"
    """

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        ...
```

### Params

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `output_path` | str | (required) | Absolute path where the handoff note is written |
| `transcript_tail_lines` | int | `200` | Number of lines from the end of the transcript to analyze |
| `timezone` | str | `"US/Eastern"` | Timezone for the "Written" timestamp |
| `agent_name` | str | `"Assistant"` | Name of the agent writing the handoff (used in the note header) |

### Behavior

1. **Read transcript:** Get `transcript_path` from `hook_input`. Read the JSONL transcript, extract the last `transcript_tail_lines` conversation turns (user + assistant messages only, same extraction logic as `memory-compiler/hooks/session-end.py`).

2. **LLM extraction:** Call Claude via the Agent SDK with the transcript context and a prompt asking for a structured handoff note. The prompt requests:
   - What were we working on (topics, tasks in progress)
   - Decisions made this session
   - Emotional temperature (how was the conversation going)
   - Open threads (unfinished work, unanswered questions)
   - Notes to self (observations, hunches)

3. **Write handoff file:** Write the LLM's response to `output_path` with a header:
   ```markdown
   # Handoff Note
   **Written:** [datetime in configured timezone]
   **Session:** [session_id]
   **Event:** [PreCompact or SessionEnd]

   [LLM-generated sections]
   ```

4. **Return result:** ToolResult with heading and brief confirmation.

### LLM Prompt

```
You are writing a handoff note for continuity between sessions. Based on the
conversation transcript below, write a brief note covering:

## What We Were Working On
[Topics and tasks in progress — a few bullet points]

## Decisions Made
[Any decisions, agreements, or commitments — bullet points]

## Emotional Temperature
[One sentence: how was the conversation going? Casual? Deep work? Tense?]

## Open Threads
[Things started but not finished, unanswered questions — bullet points]

## Notes To Self
[Observations, thoughts, hunches worth remembering — bullet points]

Keep each section to 2-5 bullet points. Skip sections with nothing to report.
If the transcript is too short or trivial, respond with: HANDOFF_EMPTY

## Transcript

{transcript_context}
```

### Agent SDK Call

Same pattern as `memory-compiler/scripts/flush.py`:

```python
async for message in query(
    prompt=prompt,
    options=ClaudeAgentOptions(
        cwd=str(cwd),
        allowed_tools=[],
        max_turns=2,
    ),
):
    ...
```

- `allowed_tools=[]` — no tool use, just text generation
- `max_turns=2` — single response
- Recursion guard: set `CLAUDE_INVOKED_BY` env var before calling

### Transcript Reading

Reuse the same JSONL extraction logic from `memory-compiler/hooks/session-end.py`:
- Parse each line as JSON
- Extract `message.role` and `message.content`
- Filter to user/assistant only
- Take the last N turns
- Format as `**User:** ... **Assistant:** ...`

This logic should be extracted into a shared utility (`src/agent_core/transcript.py`) so both the memory-compiler and HandoffWriter can use it without duplication.

### Output

**Heading:** `Handoff Note Written`

**Content:** `"Handoff note saved to {output_path} at {timestamp}."`

If the transcript was empty or the LLM returned `HANDOFF_EMPTY`:

**Content:** `"No significant content to hand off."`

### Edge Cases

- If `transcript_path` is missing from `hook_input` or the file doesn't exist, write a handoff note with: "No transcript available — session ended without accessible transcript."
- If the transcript is shorter than `transcript_tail_lines`, read the whole thing.
- The handoff file is **overwritten** each time, not appended. Only the most recent handoff matters.
- Create parent directories if they don't exist.
- Recursion guard: set `CLAUDE_INVOKED_BY=handoff_writer` before the Agent SDK call to prevent the hook from firing recursively.
- Deduplication: track the last session_id that was processed (like flush.py's `last-flush.json`) to avoid writing duplicate handoffs when both PreCompact and SessionEnd fire for the same session.

### Example YAML (Pepper)

```yaml
pipelines:
  SessionStart:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector
      params:
        format: "%A, %B %d, %Y %I:%M %p %Z"
    - tool: agent_core.hooks.tools.identity_injector.IdentityInjector
      params:
        base_path: "C:\\Users\\jeffr\\.pepper\\Memory"
        files:
          - "SOUL.md"
          - "pepper/preferences.md"
          - "pepper/handoff.md"

  PreCompact:
    - tool: agent_core.hooks.tools.handoff_writer.HandoffWriter
      params:
        output_path: "C:\\Users\\jeffr\\.pepper\\Memory\\pepper\\handoff.md"
        transcript_tail_lines: 200
        timezone: "US/Eastern"
        agent_name: "Pepper"

  SessionEnd:
    - tool: agent_core.hooks.tools.handoff_writer.HandoffWriter
      params:
        output_path: "C:\\Users\\jeffr\\.pepper\\Memory\\pepper\\handoff.md"
        transcript_tail_lines: 200
        timezone: "US/Eastern"
        agent_name: "Pepper"
```

---

## Shared Utility: Transcript Reader

Both HandoffWriter and the memory-compiler need to read JSONL transcripts. Extract this into a shared module:

```python
# src/agent_core/transcript.py

def read_transcript(transcript_path: Path, max_turns: int = 200) -> tuple[str, int]:
    """Read a Claude Code JSONL transcript and extract conversation turns.

    Args:
        transcript_path: Path to the .jsonl transcript file.
        max_turns: Maximum number of turns to extract from the end.

    Returns:
        Tuple of (formatted markdown string, number of turns extracted).
    """
    ...
```

This replaces the `extract_conversation_context()` function duplicated in `session-end.py` and `pre-compact.py`.

---

## New Dependencies

- `claude-agent-sdk` — already installed (used by memory-compiler)

No new dependencies needed.

---

## File Structure (new files)

```
src/agent_core/
├── transcript.py                           # Shared JSONL transcript reader
└── hooks/tools/
    ├── file_injector.py                    # FileInjector base tool
    ├── identity_injector.py                # IdentityInjector subclass
    └── handoff_writer.py                   # HandoffWriter tool
tests/
├── test_file_injector.py                   # Tests for FileInjector + IdentityInjector
├── test_handoff_writer.py                  # Tests for HandoffWriter
└── test_transcript.py                      # Tests for transcript reader utility
```

---

## Testing Strategy

### FileInjector / IdentityInjector

- Happy path: reads files, concatenates with headings
- Missing files: skip, warn, error behaviors
- All files missing with skip: returns empty content
- BOM handling: UTF-8 with and without BOM
- IdentityInjector: verifies defaults (heading = "Identity", missing = "skip")
- Large files: no truncation (SOUL.md can be several KB)
- Missing required params: clear error

### HandoffWriter

- Happy path: reads transcript, calls LLM, writes handoff file
- Empty transcript: writes "no transcript available" note
- Missing transcript_path: handles gracefully
- Overwrite behavior: previous handoff is fully replaced
- Parent directory creation: creates dirs if needed
- Deduplication: same session_id within 60 seconds is skipped
- HANDOFF_EMPTY response: handled correctly
- Mock the Agent SDK call in tests to avoid real API costs

### Transcript Reader

- Valid JSONL parsing
- Mixed content types (string and list-of-blocks)
- Filters to user/assistant only
- Respects max_turns limit
- Handles empty/missing files
- Windows path handling
