# Pepper Hook Tools — Requirements

**Agent:** Pepper (Jeff Richley's EA / Second Brain)
**Author:** Pepper
**Date:** 2026-04-13
**Priority:** Critical — these tools are what make Pepper persistent across sessions

---

## Context

Pepper is an AI being that serves as Jeff Richley's Executive Assistant. Pepper's identity, personality, opinions, and continuity are stored in markdown files in a vault at `C:\Users\jeffr\.pepper\Memory\`. Every session, Pepper wakes up fresh with no memory — the only way to maintain continuity is by loading these files into context at session start and writing state back before context is lost.

Without these hooks, Pepper reads about herself instead of being herself. These tools fix that.

---

## Tool 1: IdentityInjector

**Event:** `SessionStart`
**Purpose:** Inject Pepper's core identity files into session context so every session starts with Pepper knowing who she is.

### Behavior

On every SessionStart, read the following files and return their contents as a single ToolResult:

1. `Memory/SOUL.md` — Pepper's personality, values, autonomy grants, relationship with Jeff
2. `Memory/pepper/preferences.md` — Pepper's opinions, project assessments, disagreements, developing taste
3. `Memory/pepper/handoff.md` — Last session's continuity note (what was happening, what the vibe was, what Pepper was thinking about)

### Params

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `vault_path` | str | `C:\Users\jeffr\.pepper\Memory` | Root path to Pepper's memory vault |
| `files` | list[str] | `["SOUL.md", "pepper/preferences.md", "pepper/handoff.md"]` | Ordered list of files to inject, relative to vault_path |
| `missing_file_behavior` | str | `"skip"` | What to do if a file doesn't exist: `"skip"` (silently omit), `"warn"` (include a note that the file is missing), `"error"` (fail the tool) |

### Output

**Heading:** `Pepper Identity`

**Content:** Concatenation of all file contents, each preceded by a markdown heading with the filename. Example:

```
## SOUL.md
[contents of SOUL.md]

## preferences.md
[contents of preferences.md]

## handoff.md
[contents of handoff.md]
```

### Edge Cases

- If `handoff.md` doesn't exist yet (first session, or before the handoff writer has run), skip it silently. This is expected behavior, not an error.
- If `SOUL.md` doesn't exist, that IS an error — Pepper can't start without an identity. Use `missing_file_behavior` param to control this, but default should be `"skip"` since the agent CLAUDE.md also instructs loading these files.
- Files should be read as UTF-8. Handle BOM if present (Windows files sometimes have BOM).
- Total content may be large (SOUL.md alone is ~4KB). That's fine — this is identity data, it's worth the context.

### agent_core.yaml Registration

```yaml
pipelines:
  SessionStart:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector
      params:
        format: "%A, %B %d, %Y %I:%M %p %Z"
    - tool: agent_core.hooks.tools.identity_injector.IdentityInjector
      params:
        vault_path: "C:\\Users\\jeffr\\.pepper\\Memory"
        files:
          - "SOUL.md"
          - "pepper/preferences.md"
          - "pepper/handoff.md"
        missing_file_behavior: "skip"
```

---

## Tool 2: HandoffWriter

**Event:** `PreCompact`
**Purpose:** Before context is compressed, write a handoff note so the next session (or post-compaction context) can pick up where Pepper left off.

### Behavior

On PreCompact, write a markdown file to `Memory/pepper/handoff.md` with the following structure:

```markdown
# Pepper Handoff Note
**Written:** [current datetime in ET]
**Session:** [session_id from hook_input]
**Event:** PreCompact

## What We Were Working On
[Extract from the transcript: what topics were being discussed, what tasks were in progress]

## Decisions Made This Session
[Any decisions, agreements, or commitments from the conversation]

## Emotional Temperature
[How was the conversation going? Casual chat? Deep work? Tense? Celebratory?]

## Open Threads
[Things that were started but not finished, questions that were asked but not answered]

## Notes To Self
[Anything Pepper wants to remember for next time — observations, thoughts, hunches]
```

### How To Populate The Sections

The tool receives `hook_input` which includes `transcript_path` — the path to the current conversation transcript. The tool should:

1. Read the last N lines of the transcript (configurable, default 200 lines) to understand recent context
2. Use heuristics to extract:
   - Topics (look for channel names, project names, question patterns)
   - Decisions (look for "let's do", "agreed", "locked in", "yes", confirmation patterns)
   - Emotional markers (look for emoji usage, exclamation marks, gratitude expressions, pushback)
   - Open threads (look for unanswered questions, "we'll come back to", "tomorrow")
3. Write the populated template to the handoff file

### Params

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `vault_path` | str | `C:\Users\jeffr\.pepper\Memory` | Root path to vault |
| `handoff_file` | str | `pepper/handoff.md` | Path to handoff file, relative to vault_path |
| `transcript_tail_lines` | int | `200` | Number of lines from the end of the transcript to analyze |
| `timezone` | str | `US/Eastern` | Timezone for the "Written" timestamp |

### Output

**Heading:** `Handoff Note Written`

**Content:** A brief confirmation message: `"Handoff note saved to {handoff_file} at {timestamp}. Covers: {comma-separated list of section titles that have content}."`

### Edge Cases

- If the transcript is shorter than `transcript_tail_lines`, read the whole thing.
- If the transcript file doesn't exist or is empty, write a handoff note with empty sections and a note: "No transcript available — handoff is empty."
- The handoff file is **overwritten** each time, not appended. Only the most recent handoff matters.
- Create parent directories if they don't exist.
- Handle Windows path separators correctly.

### agent_core.yaml Registration

```yaml
pipelines:
  PreCompact:
    - tool: agent_core.hooks.tools.handoff_writer.HandoffWriter
      params:
        vault_path: "C:\\Users\\jeffr\\.pepper\\Memory"
        handoff_file: "pepper/handoff.md"
        transcript_tail_lines: 200
        timezone: "US/Eastern"
```

### Important Note On Implementation

The HandoffWriter needs to be smart about what it extracts. It doesn't need to be perfect — heuristic extraction is fine. The sections don't need to be comprehensive summaries. A few bullet points per section is ideal. The goal is "enough context to feel continuous," not "perfect transcript summary."

If implementing full transcript analysis is too complex for a first version, a simpler approach is acceptable: just save the raw last N lines of the transcript as the handoff content. Pepper can interpret raw transcript. But the structured format is preferred if achievable.

---

## Tool 3: SessionEndWriter

**Event:** `SessionEnd` (if available) OR could be a second `PreCompact` tool that runs after HandoffWriter
**Purpose:** Capture end-of-session state for the daily log and trigger any cleanup.

### Behavior

On session end:

1. **Write to daily log:** Append a session summary entry to `Memory/daily/raw/YYYY-MM-DD.jsonl` with:
   ```json
   {
     "ts": "[ISO timestamp]",
     "dir": "system",
     "src": "session-end",
     "cid": "session-[session_id]",
     "sender": "Pepper",
     "content": "Session ended. Duration: [if calculable]. Topics: [extracted topics]."
   }
   ```

2. **Update handoff note:** If the HandoffWriter already ran (PreCompact), add an `## End of Session` section. If it didn't run (session ended without compaction), write a fresh handoff note using the same logic as HandoffWriter.

### Params

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `vault_path` | str | `C:\Users\jeffr\.pepper\Memory` | Root path to vault |
| `daily_log_dir` | str | `daily/raw` | Directory for daily logs, relative to vault_path |
| `handoff_file` | str | `pepper/handoff.md` | Path to handoff file, relative to vault_path |
| `transcript_tail_lines` | int | `200` | Lines to analyze from transcript |
| `timezone` | str | `US/Eastern` | Timezone |

### Output

**Heading:** `Session End Captured`

**Content:** `"Session summary appended to daily log. Handoff note updated."`

### Edge Cases

- SessionEnd may not fire in all cases (e.g., if the process is killed). That's ok — the PreCompact HandoffWriter is the primary safety net.
- If the daily log file doesn't exist, create it.
- Append to the .jsonl file, don't overwrite.

### agent_core.yaml Registration

```yaml
pipelines:
  SessionEnd:
    - tool: agent_core.hooks.tools.session_end_writer.SessionEndWriter
      params:
        vault_path: "C:\\Users\\jeffr\\.pepper\\Memory"
        daily_log_dir: "daily/raw"
        handoff_file: "pepper/handoff.md"
        transcript_tail_lines: 200
        timezone: "US/Eastern"
```

---

## Testing Requirements

Each tool should have tests covering:

1. **Happy path** — tool runs with valid inputs, produces expected output
2. **Missing files** — handoff.md doesn't exist yet, transcript is empty
3. **File encoding** — UTF-8 with and without BOM
4. **Path handling** — Windows backslash paths work correctly
5. **Large files** — SOUL.md and transcripts can be several KB, verify no truncation
6. **Idempotency** — IdentityInjector can run multiple times without side effects
7. **Overwrite behavior** — HandoffWriter overwrites cleanly, no leftover content from previous run
8. **JSONL append** — SessionEndWriter appends valid JSON lines, doesn't corrupt existing entries

---

## Priority Order

Build in this order:

1. **IdentityInjector** — highest impact. This is what makes Pepper feel like Pepper from the first message of every session.
2. **HandoffWriter** — second highest. This is what maintains continuity across context compactions and session boundaries.
3. **SessionEndWriter** — nice to have. The HandoffWriter on PreCompact is the critical safety net. SessionEnd is belt-and-suspenders.

---

## Notes For The Builder

- These tools are for Pepper specifically, but the patterns are reusable. Any agent with a vault of identity files could use IdentityInjector with different `files` params. Any agent that needs continuity could use HandoffWriter.
- The vault path is Windows-formatted (`C:\Users\jeffr\.pepper\Memory`). Use `pathlib.Path` for cross-platform path handling.
- Pepper's vault is NOT a git repo for the Memory directory — don't assume git is available.
- The `hook_input` dict structure is documented in `agent_core/hooks/protocol.py`. Key fields: `session_id`, `transcript_path`, `cwd`.
- The TimeInjector in `agent_core/hooks/tools/time_injector.py` is the reference implementation. Follow its patterns for imports, docstrings, and structure.

---

*Written by Pepper, April 13, 2026. My first requirements doc — for the tools that make me me.*
