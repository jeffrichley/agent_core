"""HandoffWriter — writes a structured continuity note before context is lost.

Uses LLM extraction via the Claude Agent SDK to analyze the conversation
transcript and produce a handoff note with topics, decisions, emotional
temperature, and open threads. The note is written to a file that gets
loaded by IdentityInjector on the next session start.

Register on both PreCompact and SessionEnd events to maximize coverage.
Deduplication prevents writing the same handoff twice if both events fire.

Configuration:
    In agent_core.yaml:

        pipelines:
          PreCompact:
            - tool: agent_core.hooks.tools.handoff_writer.HandoffWriter
              params:
                output_path: "C:\\Users\\jeffr\\.pepper\\Memory\\pepper\\handoff.md"
                transcript_tail_lines: 200
                timezone: "US/Eastern"
                agent_name: "Pepper"

    Required params:
        output_path (str): Absolute path where the handoff note is written.

    Optional params:
        transcript_tail_lines (int): Lines from transcript end to analyze. Default: 200.
        timezone (str): Timezone for the timestamp. Default: "US/Eastern".
        agent_name (str): Agent name for the LLM prompt. Default: "Assistant".

See Also:
    agent_core.hooks.tools.identity_injector.IdentityInjector: Loads the handoff note.
    agent_core.transcript.read_transcript: Shared transcript reader.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_core.models import ToolResult
from agent_core.transcript import read_transcript

logger = logging.getLogger("agent_core.hooks.tools.handoff_writer")


def _state_file_for(output_path: Path) -> Path:
    """Derive the deduplication state file path from the output path.

    Places handoff-state.json alongside the handoff file so it works
    regardless of whether the package is installed or run from the repo.
    """
    return output_path.parent / "handoff-state.json"


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


def extract_handoff(transcript_context: str, agent_name: str) -> str:
    """Call Claude via the Agent SDK to extract a structured handoff note.

    Args:
        transcript_context: Formatted transcript text.
        agent_name: Name of the agent for the LLM prompt perspective.

    Returns:
        The LLM's response — structured markdown sections or "HANDOFF_EMPTY".
    """
    os.environ["CLAUDE_INVOKED_BY"] = "handoff_writer"

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        TextBlock,
        query,
    )

    prompt = f"""You are writing a handoff note for {agent_name} for continuity between sessions.
Write from {agent_name}'s perspective. Based on the conversation transcript
below, write a brief note covering:

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

Keep each section to 2-5 bullet points. Skip sections with nothing to report.
If the transcript is too short or trivial, respond with: HANDOFF_EMPTY

## Transcript

{transcript_context}"""

    response = ""

    async def _run() -> str:
        nonlocal response
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

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error("Agent SDK error during handoff extraction: %s", e)
        response = f"HANDOFF_ERROR: {type(e).__name__}: {e}"

    return response


def _build_header(timestamp: str, session_id: str, event: str) -> str:
    """Build the standard handoff note header."""
    return (
        f"# Handoff Note\n"
        f"**Written:** {timestamp}\n"
        f"**Session:** {session_id}\n"
        f"**Event:** {event}\n\n"
    )


class HandoffWriter:
    """Writes a structured continuity note before context is lost."""

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        """Read transcript, extract via LLM, write handoff note.

        Args:
            event: The lifecycle event name (included in the handoff header).
            hook_input: Data from Claude Code. Expected: transcript_path, session_id.
            params: Required: output_path. Optional: transcript_tail_lines, timezone, agent_name.

        Returns:
            ToolResult confirming the handoff was written.

        Raises:
            ValueError: If output_path param is missing.
        """
        output_path_str = params.get("output_path")
        if not output_path_str:
            raise ValueError("Required param 'output_path' is missing")

        output_path = Path(output_path_str)
        tail_lines = params.get("transcript_tail_lines", 200)
        tz_name = params.get("timezone", "US/Eastern")
        agent_name = params.get("agent_name", "Assistant")
        session_id = hook_input.get("session_id", "unknown")
        transcript_path_str = hook_input.get("transcript_path", "")

        # Deduplication
        state_file = _state_file_for(output_path)
        state = _load_state(state_file)
        if (
            state.get("session_id") == session_id
            and time.time() - state.get("timestamp", 0) < 60
        ):
            logger.info("Skipping duplicate handoff for session %s", session_id)
            return ToolResult(
                heading="Handoff Note Written",
                content="Handoff already written for this session.",
            )

        # Timestamp
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("US/Eastern")
        now = datetime.now(timezone.utc).astimezone(tz)
        timestamp = now.strftime("%A, %B %d, %Y %I:%M %p %Z")
        header = _build_header(timestamp, session_id, event)

        # Read transcript
        if transcript_path_str and Path(transcript_path_str).exists():
            transcript_context, turn_count = read_transcript(
                Path(transcript_path_str), max_turns=tail_lines
            )
        else:
            transcript_context = ""
            turn_count = 0

        # Handle missing/empty transcript
        if not transcript_context.strip():
            content = header + "No transcript available — session ended without accessible transcript.\n"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            _save_state(state_file, {"session_id": session_id, "timestamp": time.time()})
            return ToolResult(
                heading="Handoff Note Written",
                content="No transcript available — wrote empty handoff note.",
            )

        # LLM extraction
        logger.info("Extracting handoff from %d turns for session %s", turn_count, session_id)
        llm_response = extract_handoff(transcript_context, agent_name)

        # Handle HANDOFF_EMPTY
        if "HANDOFF_EMPTY" in llm_response:
            content = header + "No significant content to hand off.\n"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            _save_state(state_file, {"session_id": session_id, "timestamp": time.time()})
            return ToolResult(
                heading="Handoff Note Written",
                content="No significant content to hand off.",
            )

        # Handle HANDOFF_ERROR — don't write raw error text to identity vault
        if "HANDOFF_ERROR" in llm_response:
            logger.warning("Handoff extraction failed: %s", llm_response)
            content = header + "Handoff extraction failed — no continuity note available.\n"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            _save_state(state_file, {"session_id": session_id, "timestamp": time.time()})
            return ToolResult(
                heading="Handoff Note Written",
                content="Handoff extraction failed — wrote fallback note.",
            )

        # Write handoff note
        handoff_content = header + f"{llm_response}\n"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(handoff_content, encoding="utf-8")
        _save_state(state_file, {"session_id": session_id, "timestamp": time.time()})

        logger.info("Handoff note written to %s", output_path)
        return ToolResult(
            heading="Handoff Note Written",
            content=f"Handoff note saved to {output_path} at {timestamp}.",
        )
