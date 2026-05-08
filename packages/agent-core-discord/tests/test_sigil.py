"""Unit tests for the sigil-prefix urgency parser.

The parser must:
- Map "!" to red, "?" to yellow, anything else to green.
- Strip the sigil and at most one trailing space from the published text.
- Tolerate leading whitespace before the sigil.
- Treat sigils only at position 0 (after lstrip); mid-message sigils are text.
- Consume at most one sigil; "!!" leaves a literal "!" in the payload.
"""

from __future__ import annotations

import pytest

from agent_core_discord.sigil import parse_sigil


@pytest.mark.parametrize(
    "content, expected",
    [
        # Simple cases
        ("!hello", ("red", "hello")),
        ("?hello", ("yellow", "hello")),
        ("hello", ("green", "hello")),
        # Leading whitespace tolerated
        ("  !hello", ("red", "hello")),
        ("\n!hello", ("red", "hello")),
        ("\t?status", ("yellow", "status")),
        ("  hello", ("green", "  hello")),  # green messages preserve leading whitespace
        # Optional single space after sigil
        ("! hello", ("red", "hello")),
        ("? hello", ("yellow", "hello")),
        # Only ONE space is eaten — second space preserved
        ("!  hello", ("red", " hello")),
        # Multi-char sigil — only the first is consumed
        ("!!hello", ("red", "!hello")),
        ("??hello", ("yellow", "?hello")),
        # First sigil wins; second becomes literal text
        ("!?hello", ("red", "?hello")),
        ("?!hello", ("yellow", "!hello")),
        # Mid-message sigil is not interpreted
        ("hello !world", ("green", "hello !world")),
        ("hello ?world", ("green", "hello ?world")),
        # Edge cases
        ("", ("green", "")),
        ("   ", ("green", "   ")),  # whitespace-only preserved verbatim
        ("!", ("red", "")),  # sigil alone, empty text
        ("?", ("yellow", "")),
        # Sigil + Discord-internal markup preserved
        ("!<@123> help", ("red", "<@123> help")),
        ("?:fire: status", ("yellow", ":fire: status")),
        ("!**bold**", ("red", "**bold**")),
    ],
)
def test_parse_sigil(content: str, expected: tuple[str, str]) -> None:
    assert parse_sigil(content) == expected
