"""Shared JSONL transcript reader utility.

Extracts conversation turns from Claude Code's JSONL transcript format.
Used by HandoffWriter and potentially by the memory-compiler hooks.

Claude Code stores conversations as .jsonl files where each line is a JSON
object with a 'message' key containing 'role' and 'content'. Content can
be a string or a list of content blocks ({type: "text", text: "..."}).

Example:
    >>> from pathlib import Path
    >>> context, count = read_transcript(Path("transcript.jsonl"), max_turns=50)
    >>> print(f"Extracted {count} turns")

See Also:
    agent_core.hooks.tools.handoff_writer.HandoffWriter: Uses this to read transcripts.
"""

import json
from pathlib import Path


def read_transcript(
    transcript_path: Path,
    max_turns: int = 200,
    max_chars: int = 15_000,
) -> tuple[str, int]:
    """Read a Claude Code JSONL transcript and extract conversation turns.

    Args:
        transcript_path: Path to the .jsonl transcript file.
        max_turns: Maximum number of turns to extract from the end. Default: 200.
        max_chars: Maximum total character count. Truncated from beginning at turn boundary. Default: 15,000.

    Returns:
        Tuple of (formatted markdown string, number of turns extracted).
        Returns ("", 0) if the file doesn't exist or is empty.
    """
    if not transcript_path.exists():
        return "", 0

    turns: list[str] = []

    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = entry.get("message", {})
            if isinstance(msg, dict):
                role = msg.get("role", "")
                content = msg.get("content", "")
            else:
                role = entry.get("role", "")
                content = entry.get("content", "")

            if role not in ("user", "assistant"):
                continue

            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = "\n".join(text_parts)

            if isinstance(content, str) and content.strip():
                label = "User" if role == "user" else "Assistant"
                turns.append(f"**{label}:** {content.strip()}\n")

    recent = turns[-max_turns:]
    context = "\n".join(recent)

    if len(context) > max_chars:
        context = context[-max_chars:]
        boundary = context.find("\n**")
        if boundary > 0:
            context = context[boundary + 1:]

    return context, len(recent)
