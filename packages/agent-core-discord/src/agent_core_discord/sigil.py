"""Sigil-prefix urgency parser for Discord inbound messages.

Two sigils:
- "!" promotes to red urgency
- "?" promotes to yellow urgency
- Anything else stays green (default)

The sigil character is stripped from the published payload along with any
leading whitespace and at most one space after the sigil. See issue #38
and docs/superpowers/specs/2026-05-08-issue-38-discord-urgency-sigil-design.md.
"""

from __future__ import annotations

from typing import Literal

Urgency = Literal["red", "yellow", "green"]

_SIGIL_TO_URGENCY: dict[str, Urgency] = {"!": "red", "?": "yellow"}


def parse_sigil(content: str) -> tuple[Urgency, str]:
    """Parse a sigil-prefixed message into (urgency, stripped_text).

    Returns ("green", content) unchanged when no sigil is present so callers
    that expect the original text on the green path see no surprises.

    When a sigil matches, returns the elevated urgency and the text with
    leading whitespace + the sigil + at most one trailing space removed.
    """
    stripped = content.lstrip()
    if not stripped:
        return "green", content
    sigil = stripped[0]
    if sigil not in _SIGIL_TO_URGENCY:
        return "green", content
    rest = stripped[1:]
    if rest.startswith(" "):
        rest = rest[1:]
    return _SIGIL_TO_URGENCY[sigil], rest
