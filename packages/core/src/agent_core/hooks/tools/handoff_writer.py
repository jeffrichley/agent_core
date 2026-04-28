"""HandoffWriter — spawns a detached claude CLI process to write continuity notes.

The hook itself is fast (<1 second): it reads the transcript, writes context
to a temp file, and spawns `claude -p` as a detached background process.
The claude process reads the context, generates a structured handoff note,
and writes it to the output path.

This uses the Claude CLI directly (subscription auth), not the Agent SDK.
The detached process survives Ctrl+C during session exit.

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

import json
import logging
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_core.models import ToolResult
from agent_core.transcript import read_transcript

logger = logging.getLogger("agent_core.hooks.tools.handoff_writer")

# Debug log file for diagnosing hook failures (hooks swallow stderr)
_DEBUG_LOG = Path.home() / ".pepper" / "Memory" / "pepper" / "handoff-debug.log"


def _debug(msg: str) -> None:
    """Append a timestamped debug line to the debug log file."""
    try:
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_DEBUG_LOG, "a", encoding="utf-8") as f:
            ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _state_file_for(output_path: Path) -> Path:
    """Derive the deduplication state file path from the output path."""
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


def _build_prompt(context_file: str, output_path: str, agent_name: str,
                  session_id: str, event: str, timestamp: str) -> str:
    """Build the prompt for the detached claude process."""
    return f"""Read the conversation transcript from the file at {context_file}.

Then write a handoff note for {agent_name} to the file at {output_path}.

The handoff note must start with this exact header:

# Handoff Note
**Written:** {timestamp}
**Session:** {session_id}
**Event:** {event}

Then write from {agent_name}'s perspective covering these sections:

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

Rules:
- Keep each section to 2-5 bullet points.
- Skip sections with nothing to report.
- Be specific — name files, tools, errors, and decisions. Not vague summaries.
- If the transcript is genuinely trivial (only greetings, no real work), write a note
  that just says "No significant content to hand off." under the header.

After writing the handoff note, delete the context file at {context_file}.

Write the state file at {Path(output_path).parent / 'handoff-state.json'} with this JSON:
{{"session_id": "{session_id}", "timestamp": {int(time.time())}}}

After everything is written, send a desktop notification by running this exact command:
uv run --directory E:/workspaces/ai/agents/agent_core agent-core notify "{agent_name}" "Handoff note written"

If the notify command fails, that's fine — the handoff note is the important part."""


class HandoffWriter:
    """Spawns a detached claude CLI process to write a continuity note."""

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        """Read transcript, write to temp file, spawn claude -p in background.

        Returns immediately (<1 second). The detached claude process handles
        the LLM generation and writes the handoff note.
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

        _debug(f"=== HandoffWriter fired: event={event} session={session_id}")
        _debug(f"transcript_path={transcript_path_str!r}")

        # Quick deduplication check
        state_file = _state_file_for(output_path)
        state = _load_state(state_file)
        if (
            state.get("session_id") == session_id
            and time.time() - state.get("timestamp", 0) < 60
        ):
            _debug(f"skipping duplicate handoff for session {session_id}")
            return ToolResult(
                heading="Handoff Note Written",
                content="Handoff already written for this session.",
            )

        # Check transcript exists
        if not transcript_path_str or not Path(transcript_path_str).exists():
            _debug(f"transcript not available: {transcript_path_str!r}")
            return ToolResult(
                heading="Handoff Note Written",
                content="No transcript available.",
            )

        # Read transcript (fast — local file I/O only)
        transcript_context, turn_count = read_transcript(
            Path(transcript_path_str), max_turns=tail_lines
        )
        _debug(f"read_transcript: {turn_count} turns, {len(transcript_context)} chars")

        if not transcript_context.strip():
            _debug("transcript empty after read")
            return ToolResult(
                heading="Handoff Note Written",
                content="Transcript empty — nothing to hand off.",
            )

        # Timestamp
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("US/Eastern")
        now = datetime.now(timezone.utc).astimezone(tz)
        timestamp = now.strftime("%A, %B %d, %Y %I:%M %p %Z")

        # Write context to temp file for the claude process
        ts_str = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
        context_file = output_path.parent / f"handoff-context-{session_id[:8]}-{ts_str}.md"
        context_file.write_text(transcript_context, encoding="utf-8")
        _debug(f"wrote context to {context_file}")

        # Build prompt
        prompt = _build_prompt(
            context_file=str(context_file),
            output_path=str(output_path),
            agent_name=agent_name,
            session_id=session_id,
            event=event,
            timestamp=timestamp,
        )

        # Find claude binary
        claude_bin = shutil.which("claude")
        if not claude_bin:
            _debug("claude binary not found on PATH")
            return ToolResult(
                heading="Handoff Note Written",
                content="claude CLI not found — cannot write handoff note.",
            )

        # Spawn detached claude process
        cmd = [
            claude_bin,
            "-p", prompt,
            "--allowedTools", "Read,Write,Edit,Bash",
            "--max-turns", "5",
        ]

        # Detach from parent so it survives Ctrl+C
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        kwargs = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True

        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                cwd=str(output_path.parent),
                **kwargs,
            )
            _debug(f"spawned claude -p for session {session_id}")
        except Exception as e:
            _debug(f"FAILED to spawn claude: {e}")
            logger.error("Failed to spawn claude: %s", e)
            return ToolResult(
                heading="Handoff Note Written",
                content=f"Failed to spawn claude process: {e}",
            )

        # Save state immediately to prevent duplicate spawns
        _save_state(state_file, {"session_id": session_id, "timestamp": time.time()})

        return ToolResult(
            heading="Handoff Note Written",
            content=f"Background handoff extraction spawned for session {session_id} ({turn_count} turns).",
        )
