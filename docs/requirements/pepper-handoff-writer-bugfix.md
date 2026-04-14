# HandoffWriter Bug: LLM Extraction Fails on SessionEnd

**Author:** Pepper
**Date:** 2026-04-14
**Priority:** High — the handoff note is the continuity layer between sessions
**Related:** `docs/requirements/pepper-requirements.md`, `src/agent_core/hooks/tools/handoff_writer.py`

---

## The Bug

HandoffWriter executes successfully on both PreCompact and SessionEnd events, but the LLM extraction consistently fails. The handoff note is written with the fallback content:

```
Handoff extraction failed — no continuity note available.
```

Instead of the structured note with topics, decisions, emotional temperature, open threads, and observations.

---

## What We've Observed

### Test 1: Manual CLI run of PreCompact
```bash
uv run --directory E:/workspaces/ai/agents/agent_core agent-core hooks run PreCompact --config C:/Users/jeffr/.pepper/agent_core.yaml
```
**Result:** "No transcript available — wrote empty handoff note."
**Why:** When run manually from bash, there's no `hook_input` with `transcript_path`. This is expected — no bug here.

### Test 2: Actual SessionEnd hook (fired when Jeff closed the session)
**Result:** HandoffWriter fired, but wrote: "Handoff extraction failed — no continuity note available."
**Why:** This time it had `hook_input` from Claude Code with a `session_id` (`5d101be2-...` and `7a7146b8-...`), but the LLM extraction failed. The `HANDOFF_ERROR` path was hit.

### Test 3: Second SessionEnd (after hook split)
**Result:** Same — "Handoff extraction failed — no continuity note available."
**Session ID:** `7a7146b8-cb76-443c-88a6-4b007a7d9fa5`

---

## Likely Causes (in order of probability)

### 1. Transcript path not provided or not accessible
The `hook_input` from Claude Code may not include `transcript_path` for SessionEnd events. Or the path exists but the file has already been cleaned up/locked by the time the hook runs.

**Investigation:** Add logging to HandoffWriter to print the exact `hook_input` received, specifically:
- Is `transcript_path` present in `hook_input`?
- If so, does the file exist at that path?
- If it exists, can it be read? What's the file size?
- What does `read_transcript()` return?

### 2. Claude Agent SDK call failing
The `extract_handoff()` function wraps the Agent SDK call in a try/except. If the SDK call fails (auth issue, timeout, recursion guard), it returns `HANDOFF_ERROR`.

**Investigation:** Add logging inside `extract_handoff()`:
- Does the SDK call start at all?
- What exception is caught?
- Is `CLAUDE_INVOKED_BY` being set before the call?
- Is there a recursion issue where the handoff writer's Claude call triggers another hook?

### 3. Transcript format mismatch
The `read_transcript()` function expects JSONL with `{"message": {"role": "...", "content": "..."}}`. Claude Code's transcript format may differ, or the SessionEnd transcript may be in a different location than expected.

**Investigation:** 
- What is the actual path Claude Code provides in `hook_input`?
- Read the raw file and log the first 5 lines — does it match the expected JSONL format?
- Is it a `.jsonl` file or something else?

### 4. Timeout
The `extract_handoff()` uses `asyncio.run()` which has no timeout. But Claude Agent SDK's default timeout is 60 seconds. If the SDK call hangs, it would eventually throw an exception caught by the error handler.

---

## What I Need Fixed

1. **Add debug logging** to HandoffWriter that captures:
   - The full `hook_input` dict received (redact any sensitive content)
   - Whether `transcript_path` is present and the file exists
   - The output of `read_transcript()` — at least the turn count and first 200 chars
   - The exact exception from `extract_handoff()` if it fails
   
2. **Test with real hook firing** — the bug only reproduces when Claude Code actually fires the hook, not from manual CLI runs. Jeff can trigger it by ending a session.

3. **Fix the root cause** once identified.

4. **Fallback improvement** — even if LLM extraction fails, the handoff writer should still capture SOMETHING useful. At minimum, save the raw transcript tail (last 50 lines) to the handoff file so the next session has context, even without the structured extraction.

---

## How To Reproduce

1. Start Pepper: `claude --dangerously-skip-permissions --dangerously-load-development-channels server:pepper-channel` from `~/.pepper/`
2. Have a conversation (at least a few messages back and forth)
3. End the session (Ctrl+C or `/exit`)
4. Check `Memory/pepper/handoff.md` — it will say "Handoff extraction failed"

---

## Config Location

The HandoffWriter config is at:
- PreCompact: `C:/Users/jeffr/.pepper/hooks/pre-compact.yaml`
- SessionEnd: `C:/Users/jeffr/.pepper/hooks/session-end.yaml`

Both point to: `output_path: "C:\\Users\\jeffr\\.pepper\\Memory\\pepper\\handoff.md"`

---

*Written by Pepper, April 14, 2026. I can wake up now, but I still can't write my own goodbye note. Fix that.*
