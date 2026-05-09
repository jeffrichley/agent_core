"""Issue #70: rendering pipeline for inline-content wake notifications.

Produces a string suitable for the `params.content` field of an
`notifications/claude/channel` notification. The relay's stdio server
then forwards that string to Claude Code, which renders it in the
agent's working context.

Encoding contract: arbitrary user-provided text (Discord messages, code,
unbalanced characters) cannot break the agent's parse. We use HTML escape
for body content; attribute values (kind, urgency, envelope_id, from) are
bounded enums or hex IDs and don't need escaping.
"""

from __future__ import annotations

import html


def encode_body(text: str) -> str:
    """HTML-escape body content for safe inclusion in an <inbox> tag.

    Applies escaping for ``&``, ``<``, ``>``, ``'``, ``"``. The result
    survives a downstream XML parse of the surrounding wrapper and round-
    trips back to the original after XML decoding.

    Not strictly idempotent (escaping ``&amp;`` produces ``&amp;amp;``);
    callers should escape exactly once per pass through the pipeline.
    """
    escaped = html.escape(text, quote=True)
    # html.escape uses &#x27; for single quotes, but we need &apos;
    escaped = escaped.replace("&#x27;", "&apos;")
    return escaped
