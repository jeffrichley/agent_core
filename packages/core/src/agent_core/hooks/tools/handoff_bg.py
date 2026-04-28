"""Background handoff extraction process.

Spawned by HandoffWriter as a detached process. Reads pre-extracted transcript
context from a temp file, calls the Claude Agent SDK to generate a structured
handoff note, and writes the result to the output path.

Usage:
    python handoff_bg.py <context_file> <output_path> <session_id> <event> <agent_name> <timezone>

The context_file is deleted after processing.
"""

from __future__ import annotations

# Recursion prevention: set BEFORE any imports that might trigger Claude
import os
os.environ["CLAUDE_INVOKED_BY"] = "handoff_writer"

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Debug log — same location as HandoffWriter
_DEBUG_LOG = Path.home() / ".pepper" / "Memory" / "pepper" / "handoff-debug.log"
LOG_FILE = Path(__file__).resolve().parent.parent.parent.parent.parent / "handoff-bg.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _debug(msg: str) -> None:
    try:
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_DEBUG_LOG, "a", encoding="utf-8") as f:
            ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] [bg] {msg}\n")
    except Exception:
        pass


def _build_header(timestamp: str, session_id: str, event: str) -> str:
    return (
        f"# Handoff Note\n"
        f"**Written:** {timestamp}\n"
        f"**Session:** {session_id}\n"
        f"**Event:** {event}\n\n"
    )


def _load_state(state_file: Path) -> dict:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state), encoding="utf-8")


async def extract_handoff(transcript_context: str, agent_name: str) -> str:
    """Call Claude via the Agent SDK to extract a structured handoff note."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        TextBlock,
        query,
    )

    prompt = f"""You are writing a handoff note for {agent_name} for continuity between sessions.
Write from {agent_name}'s perspective. Based on the conversation transcript below.

## Output Format

Your response must start with one of the ## section headings below, or with
EXACTLY the word HANDOFF_EMPTY (alone on its own line, nothing else).

## What We Were Working On
[Topics and tasks in progress — a few bullet points]

## Decisions Made
[Any decisions, agreements, or commitments — bullet points]

## Emotional Temperature
[One sentence: how was the conversation going? Casual? Deep work? Tense?]

## Open Threads
[Things started but not finished, unanswered questions — bullet points]

## Observations
[Patterns noticed, hunches worth remembering — bullet points]

## Rules

- Keep each section to 2-5 bullet points.
- Skip sections with nothing to report.
- If the transcript is genuinely trivial (only greetings, no real work), respond with
  EXACTLY the word HANDOFF_EMPTY and nothing else.
- HANDOFF_EMPTY means "there is literally nothing worth noting." Debugging sessions,
  code reviews, architecture discussions, config changes — these are ALL worth noting.
  When in doubt, write the handoff.

## Examples

### Good handoff (write something like this):

## What We Were Working On
- Refactored the auth middleware to use JWT tokens instead of session cookies
- Fixed a race condition in the connection pool that caused intermittent 500s

## Decisions Made
- Going with RS256 for JWT signing — Ed25519 would be faster but library support is spotty
- Connection pool max size set to 50 (was 20, kept hitting limits under load)

## Emotional Temperature
Focused deep work session. Jeff was in flow state — minimal back-and-forth, mostly execution.

## Open Threads
- JWT refresh token rotation not implemented yet — needed before deploy
- Load test results pending (kicked off at end of session)

## Observations
- The connection pool issue had been causing the intermittent errors Jeff mentioned last week
- Auth middleware is now the cleanest module in the codebase — good candidate for extracting to a shared lib

### Bad handoff (do NOT write like this):

## What We Were Working On
- We worked on some code changes
- Various debugging tasks

## Decisions Made
- Some decisions were made about the architecture

(This is too vague. Name the specific files, tools, errors, and decisions.)

### When to use HANDOFF_EMPTY:

User: "hey"
Assistant: "Hello! How can I help?"
User: "never mind"

This is trivial — respond with HANDOFF_EMPTY.

### When NOT to use HANDOFF_EMPTY:

User: "can you check why the tests are failing?"
Assistant: [runs tests, investigates, finds the issue]

This has real content — write a handoff even if the fix was small.

## Transcript

{transcript_context}"""

    response = ""

    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            allowed_tools=[],
            max_turns=2,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    response += block.text

    return response


def main():
    if len(sys.argv) < 7:
        logging.error(
            "Usage: %s <context_file> <output_path> <session_id> <event> <agent_name> <timezone>",
            sys.argv[0],
        )
        sys.exit(1)

    context_file = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    session_id = sys.argv[3]
    event = sys.argv[4]
    agent_name = sys.argv[5]
    tz_name = sys.argv[6]

    _debug(f"=== handoff_bg started: session={session_id} event={event}")
    logging.info("handoff_bg started for session %s", session_id)

    # Read context
    if not context_file.exists():
        _debug(f"context file not found: {context_file}")
        logging.error("Context file not found: %s", context_file)
        return

    transcript_context = context_file.read_text(encoding="utf-8").strip()
    if not transcript_context:
        _debug("context file is empty, skipping")
        context_file.unlink(missing_ok=True)
        return

    _debug(f"read {len(transcript_context)} chars from context file")

    # Deduplication
    state_file = output_path.parent / "handoff-state.json"
    state = _load_state(state_file)
    if (
        state.get("session_id") == session_id
        and time.time() - state.get("timestamp", 0) < 60
    ):
        _debug(f"skipping duplicate handoff for session {session_id}")
        logging.info("Skipping duplicate handoff for session %s", session_id)
        context_file.unlink(missing_ok=True)
        return

    # Timestamp
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("US/Eastern")
    now = datetime.now(timezone.utc).astimezone(tz)
    timestamp = now.strftime("%A, %B %d, %Y %I:%M %p %Z")
    header = _build_header(timestamp, session_id, event)

    # LLM extraction
    _debug(f"calling Agent SDK for {agent_name}")
    try:
        llm_response = asyncio.run(extract_handoff(transcript_context, agent_name))
        _debug(f"SDK returned {len(llm_response)} chars, starts with: {llm_response[:100]!r}")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        _debug(f"EXCEPTION {type(e).__name__}: {e}")
        _debug(f"traceback:\n{tb}")
        logging.error("Agent SDK error: %s\n%s", e, tb)
        llm_response = f"HANDOFF_ERROR: {type(e).__name__}: {e}"

    # Write result
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if llm_response.strip().splitlines()[0].strip() == "HANDOFF_EMPTY":
        content = header + "No significant content to hand off.\n"
        _debug("result: HANDOFF_EMPTY")
    elif "HANDOFF_ERROR" in llm_response:
        content = header + "Handoff extraction failed — no continuity note available.\n"
        _debug(f"result: HANDOFF_ERROR — {llm_response}")
    else:
        content = header + f"{llm_response}\n"
        _debug("result: success, writing handoff note")

    output_path.write_text(content, encoding="utf-8")
    _save_state(state_file, {"session_id": session_id, "timestamp": time.time()})

    # Cleanup
    context_file.unlink(missing_ok=True)

    _debug(f"handoff_bg complete for session {session_id}")
    logging.info("Handoff written to %s", output_path)


if __name__ == "__main__":
    main()
