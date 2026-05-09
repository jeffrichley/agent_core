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
import json
from collections.abc import Callable


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


# ---------------------------------------------------------------------
# Per-kind renderers — each takes an envelope dict, returns body text.
# Body text is HTML-escaped before being placed inside the <inbox> tag.
# ---------------------------------------------------------------------


def _render_text_message_body(env: dict) -> str:
    text = env.get("payload", {}).get("text", "")
    return encode_body(str(text))


def _render_acknowledgment_body(env: dict) -> str:
    payload = env.get("payload", {}) or {}
    note = payload.get("note")
    if note is not None:
        return encode_body(str(note))
    return encode_body(json.dumps(payload, sort_keys=True, default=str))


def _render_event_body(env: dict) -> str:
    payload = env.get("payload", {}) or {}
    # Compact JSON of the event payload.
    return encode_body(json.dumps(payload, sort_keys=True, default=str))


def _render_generic_body(env: dict) -> str:
    payload = env.get("payload", {}) or {}
    return encode_body(json.dumps(payload, sort_keys=True, default=str))


def _render_fallback_body(env: dict) -> str:
    """Used for unknown kinds and when a renderer raises."""
    payload = env.get("payload", {}) or {}
    try:
        body = repr(payload)
    except Exception:
        body = f"<unrenderable payload for envelope {env.get('id', '?')}>"
    return encode_body(body)


_RENDERERS: dict[str, Callable[[dict], str]] = {
    "TextMessage": _render_text_message_body,
    "Acknowledgment": _render_acknowledgment_body,
    "Event": _render_event_body,
}

# Kinds that use the generic JSON payload renderer rather than the fallback marker.
_GENERIC_KINDS: frozenset[str] = frozenset(
    {"BriefRequest", "ToolInvocation", "Progress", "ComposeBrief"}
)


def render_envelope(env: dict) -> str:
    """Render one envelope as an <inbox>...</inbox> block with HTML-escaped body."""
    kind = env.get("kind", "Unknown")
    env_id = env.get("id", "")
    from_ = env.get("from", "")
    urgency = env.get("urgency", "green")
    in_reply_to = env.get("in_reply_to")

    renderer = _RENDERERS.get(kind)
    is_fallback = False
    if renderer is not None:
        try:
            body = renderer(env)
        except Exception:
            body = _render_fallback_body(env)
            is_fallback = True
    elif kind in _GENERIC_KINDS:
        try:
            body = _render_generic_body(env)
        except Exception:
            body = _render_fallback_body(env)
            is_fallback = True
    else:
        body = _render_fallback_body(env)
        is_fallback = True

    attrs = [
        f"kind='{kind}'",
        f"from='{from_}'",
        f"urgency='{urgency}'",
        f"envelope_id='{env_id}'",
    ]
    if in_reply_to:
        attrs.append(f"in_reply_to='{in_reply_to}'")
    if is_fallback:
        attrs.append("render='fallback'")

    return f"<inbox {' '.join(attrs)}>\n{body}\n</inbox>"


def render_item(item: dict) -> list[str]:
    """Render one item from consume()'s response — single, batch, or flat envelope.

    Returns a list of rendered <inbox> blocks (one per underlying envelope).
    Batch entries get a batch='N/M' attribute on each underlying envelope's tag.
    """
    if "type" not in item:
        # Flat envelope dict (consume(batch_window_seconds=0) shape).
        return [render_envelope(item)]
    if item["type"] == "single":
        return [render_envelope(item["envelope"])]
    if item["type"] == "batch":
        envelopes = item["envelopes"]
        total = len(envelopes)
        rendered: list[str] = []
        for i, env in enumerate(envelopes, start=1):
            block = render_envelope(env)
            # Inject batch attribute into the opening tag's whitespace area.
            prefixed = block.replace(
                "<inbox ", f"<inbox batch='{i}/{total}' ", 1
            )
            rendered.append(prefixed)
        return rendered
    # Unknown item shape — defensive fallback.
    return [_render_fallback_body(item)]
