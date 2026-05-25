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
from xml.sax.saxutils import quoteattr


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


def _humanize_bytes(n: int) -> str:
    """Powers of 1024, two significant figures, suffix B/KB/MB/GB."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0 B"
    if n < 1024:
        return f"{n} B"
    units = ["KB", "MB", "GB"]
    size = float(n)
    for unit in units:
        size /= 1024.0
        if size < 1024.0 or unit == "GB":
            if size >= 100:
                return f"{int(round(size))} {unit}"
            return f"{size:.1f}".rstrip("0").rstrip(".") + f" {unit}"
    return f"{int(round(size))} GB"


def _render_attachments_block(env: dict) -> str:
    """Return an escaped attachment block for an envelope, or '' if none.

    Never raises: a malformed metadata['attachments'] degrades to '' (or
    skips bad entries) rather than routing the whole envelope to fallback
    rendering and losing the text body.
    """
    try:
        metadata = env.get("metadata") or {}
        if not isinstance(metadata, dict):
            return ""
        atts = metadata.get("attachments")
        if not isinstance(atts, list) or not atts:
            return ""
        lines: list[str] = []
        for a in atts:
            if not isinstance(a, dict):
                continue
            filename = str(a.get("filename", "?"))
            ctype = str(a.get("content_type", "unknown"))
            size = _humanize_bytes(a.get("size_bytes", 0))
            local_path = a.get("local_path")
            if local_path:
                lines.append(
                    f"[attachment: {filename} ({ctype}, {size}) "
                    f"→ {local_path}]"
                )
            else:
                err = str(a.get("download_error", "unknown error"))
                url = str(a.get("url", ""))
                lines.append(
                    f"[attachment: {filename} ({ctype}, {size}) "
                    f"— download failed ({err}); "
                    f"CDN url may be expired: {url}]"
                )
        if not lines:
            return ""
        return "\n\n" + encode_body("\n".join(lines))
    except Exception:
        return ""


def _render_text_message_body(env: dict) -> str:
    text = env.get("payload", {}).get("text", "")
    return encode_body(str(text)) + _render_attachments_block(env)


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

# Plugin-registered renderers for extension envelope kinds (Desire, Thought, …).
# Populated by the runner during bootstrap via set_plugin_renderers(), sourced
# from agent_core.plugins.manager.get_envelope_renderers(pm). Plugin renderers
# take precedence over built-in _RENDERERS on kind collision (operator-aware
# override). See docs/superpowers/specs/2026-05-25-envelope-extension-hookspec-design.md.
_PLUGIN_RENDERERS: dict[str, Callable[[dict], str]] = {}


def set_plugin_renderers(renderers: dict[str, Callable[[dict], str]]) -> None:
    """Set the plugin-registered envelope-renderer dispatch table.

    Called by the bus runner during bootstrap after plugin discovery. The
    runner aggregates plugin contributions via
    ``agent_core.plugins.manager.get_envelope_renderers(pm)`` and hands the
    mapping in here. Replacing (not merging) the table makes registration
    explicit + testable; callers always pass the full intended state.

    Idempotent across re-registration. Pass an empty dict to clear (used
    in tests).
    """
    global _PLUGIN_RENDERERS
    _PLUGIN_RENDERERS = dict(renderers)


# Kinds that use the generic JSON payload renderer rather than the fallback marker.
_GENERIC_KINDS: frozenset[str] = frozenset(
    {"BriefRequest", "ToolInvocation", "Progress", "ComposeBrief"}
)


def _inbox_attrs(env: dict) -> list[str]:
    """Build the `<inbox>` framework attribute list for an envelope.

    Framework attrs: kind, from, urgency, envelope_id, optional in_reply_to.
    Mode flags (preview, render='fallback', batch) are appended by callers
    after this helper returns.
    """
    kind = env.get("kind", "Unknown")
    env_id = env.get("id", "")
    from_ = env.get("from", "")
    urgency = env.get("urgency", "green")
    in_reply_to = env.get("in_reply_to")

    attrs = [
        f"kind='{kind}'",
        f"from='{from_}'",
        f"urgency='{urgency}'",
        f"envelope_id='{env_id}'",
    ]
    if in_reply_to:
        attrs.append(f"in_reply_to='{in_reply_to}'")
    # Per-namespace preview surfacing. Add cases as namespaces earn them
    # via documented agent symptoms (rule-of-three before any registry).
    metadata = env.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    discord = metadata.get("discord") or {}
    if not isinstance(discord, dict):
        discord = {}
    if discord:
        cid = discord.get("channel_id")
        if cid:
            attrs.append(f"channel_id={quoteattr(str(cid))}")
            cname = discord.get("channel_name")
            if cname:
                attrs.append(f"channel_name={quoteattr(str(cname))}")
    return attrs


def render_envelope(env: dict) -> str:
    """Render one envelope as an <inbox>...</inbox> block with HTML-escaped body.

    Dispatch order:
      1. Plugin-registered renderer for ``kind`` (allows override of built-ins).
      2. Built-in ``_RENDERERS`` for ``kind``.
      3. ``_GENERIC_KINDS`` JSON-payload renderer.
      4. Fallback (marker block, ``render='fallback'`` attribute).
    """
    kind = env.get("kind", "Unknown")

    is_fallback = False
    plugin_renderer = _PLUGIN_RENDERERS.get(kind)
    if plugin_renderer is not None:
        try:
            body = plugin_renderer(env)
        except Exception:
            body = _render_fallback_body(env)
            is_fallback = True
    else:
        renderer = _RENDERERS.get(kind)
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

    attrs = _inbox_attrs(env)
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
    # Unknown item shape — defensive fallback. Emit an inert diagnostic block
    # so an agent reading this knows the relay couldn't render the item.
    item_type = item.get("type", "<missing>")
    diagnostic = encode_body(
        f"unrecognized consume() item shape: type={item_type!r}; raw={item!r}"
    )
    return [
        f"<inbox kind='Unknown' render='fallback' reason='unrecognized_item_shape'>\n"
        f"{diagnostic}\n"
        f"</inbox>"
    ]


# ---------------------------------------------------------------------
# Circuit breaker, truncation marker, and redelivery tracker (issue #70)
# ---------------------------------------------------------------------

from collections import OrderedDict  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Literal  # noqa: E402


def truncation_marker(envelope_id: str) -> str:
    """The body replacement for envelopes that exceed the per-envelope cap.

    Tells the agent how to fetch the full payload via the peek() tool
    (issue #70).
    """
    return f"[content elided; envelope_id='{envelope_id}'; call peek('{envelope_id}') for full payload]"


@dataclass(frozen=True)
class InlineAll:
    """Circuit breaker passed — emit the rendered envelopes inline."""

    rendered: list[str]
    inlined_ids: list[str]
    inlined_envelopes_summary: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class FallbackToBare:
    """Circuit breaker tripped — emit today's bare wake; agent calls consume() manually."""

    reason: Literal["envelope_count", "total_bytes"]


CircuitBreakerResult = InlineAll | FallbackToBare


def _is_failure_envelope(env: dict) -> bool:
    """Failure envelopes (yellow/red urgency, or notes prefixed 'error:')
    bypass the BATCH circuit-breaker (loudness invariant). Per-envelope
    cap still applies."""
    urgency = env.get("urgency", "green")
    if urgency in ("yellow", "red"):
        return True
    payload = env.get("payload", {}) or {}
    note = payload.get("note") if isinstance(payload, dict) else None
    if isinstance(note, str) and note.startswith("error:"):
        return True
    return False


def _envelopes_from_item(item: dict) -> list[dict]:
    """Flatten an item from consume()'s response into its underlying envelopes."""
    if "type" not in item:
        return [item]  # flat envelope dict
    if item["type"] == "single":
        return [item["envelope"]]
    if item["type"] == "batch":
        return list(item["envelopes"])
    # Unknown shape — synthesize a placeholder envelope so apply_circuit_breaker
    # can pair it 1:1 with render_item's defensive diagnostic block.
    return [
        {
            "id": "<unknown>",
            "kind": "Unknown",
            "from": "<unknown>",
            "urgency": "green",
            "in_reply_to": None,
            "payload": {},
        }
    ]


def _render_with_truncation(env: dict, *, body: str, fallback: bool) -> str:
    """Render an envelope's <inbox> tag with a custom body (truncated/preview)."""
    attrs = _inbox_attrs(env)
    if fallback:
        attrs.append("render='fallback'")

    return f"<inbox {' '.join(attrs)}>\n{body}\n</inbox>"


def _render_preview(env: dict, *, preview_chars: int = 200) -> str:
    """Preview mode: <inbox preview='true'> with first N chars + truncation marker."""
    env_id = env.get("id", "")

    # Get the fully-rendered body, then take a preview prefix.
    full_block = render_envelope(env)
    body_start = full_block.find(">\n") + 2
    body_end = full_block.rfind("\n</inbox>")
    body = full_block[body_start:body_end]
    preview_body = body[:preview_chars]
    if len(body) > preview_chars:
        preview_body += "…"
    body_with_marker = f"{preview_body}\n{truncation_marker(env_id)}"

    attrs = _inbox_attrs(env)
    attrs.append("preview='true'")
    return f"<inbox {' '.join(attrs)}>\n{body_with_marker}\n</inbox>"


def apply_circuit_breaker(
    items: list[dict],
    *,
    max_envelopes: int,
    max_total_bytes: int,
    max_per_envelope_bytes: int,
    mode: Literal["full", "preview"],
) -> CircuitBreakerResult:
    """Decide what to inline vs. elide vs. fallback.

    Decision order:
      1. If total envelope count exceeds max_envelopes → FallbackToBare.
      2. Render each envelope. If a single rendered body exceeds
         max_per_envelope_bytes, replace its body with the truncation
         marker (failure envelopes also respect this).
      3. If total rendered bytes exceeds max_total_bytes AND no failure
         envelopes are present → FallbackToBare. Failure envelopes
         bypass this cap (loudness invariant).
      4. Otherwise → InlineAll.
    """
    import re as _re

    # Flatten items to underlying envelopes for count check.
    all_envelopes: list[dict] = []
    for item in items:
        all_envelopes.extend(_envelopes_from_item(item))

    if len(all_envelopes) > max_envelopes:
        return FallbackToBare(reason="envelope_count")

    has_failure = any(_is_failure_envelope(e) for e in all_envelopes)

    rendered_blocks: list[str] = []
    summaries: list[dict] = []
    inlined_ids: list[str] = []

    # We render through render_item to preserve batch-prefix semantics, then
    # apply per-envelope-cap truncation by post-processing each block.
    for item in items:
        envs = _envelopes_from_item(item)
        item_blocks = render_item(item)
        for env, block in zip(envs, item_blocks, strict=True):
            if mode == "preview":
                final_block = _render_preview(env)
            else:
                # Measure full-block bytes (tags included) so the per-envelope
                # cap is consistent with the summary bytes field.
                if len(block.encode("utf-8")) > max_per_envelope_bytes:
                    # Replace body with truncation marker, preserving attrs.
                    had_fallback = "render='fallback'" in block
                    final_block = _render_with_truncation(
                        env,
                        body=truncation_marker(env["id"]),
                        fallback=had_fallback,
                    )
                    # Preserve the batch attribute if present in original block.
                    if "batch=" in block:
                        m = _re.search(r"batch='[^']*'", block)
                        if m:
                            final_block = final_block.replace(
                                "<inbox ", f"<inbox {m.group(0)} ", 1
                            )
                else:
                    final_block = block

            rendered_blocks.append(final_block)
            inlined_ids.append(env["id"])
            summaries.append(
                {
                    "id": env["id"],
                    "kind": env.get("kind"),
                    "from": env.get("from"),
                    "urgency": env.get("urgency", "green"),
                    "bytes": len(final_block.encode("utf-8")),
                }
            )

    total_bytes = sum(s["bytes"] for s in summaries)
    if total_bytes > max_total_bytes and not has_failure:
        return FallbackToBare(reason="total_bytes")

    return InlineAll(
        rendered=rendered_blocks,
        inlined_ids=inlined_ids,
        inlined_envelopes_summary=summaries,
    )


# ---------------------------------------------------------------------
# Redelivery tracker — bounded LRU cache.
# Records seen envelope IDs so a redelivered envelope gets a
# [RESEND #N] marker.
# ---------------------------------------------------------------------


class RedeliveryTracker:
    """Bounded LRU cache of envelope IDs. Returns the [RESEND #N] marker
    on second-and-subsequent sightings of the same id (None on first)."""

    def __init__(self, capacity: int = 200) -> None:
        self._capacity = capacity
        self._counts: OrderedDict[str, int] = OrderedDict()

    def note_and_get_marker(self, envelope_id: str) -> str | None:
        if envelope_id in self._counts:
            self._counts[envelope_id] += 1
            self._counts.move_to_end(envelope_id)
            n = self._counts[envelope_id]
            return f"[RESEND #{n}] "
        self._counts[envelope_id] = 1
        self._counts.move_to_end(envelope_id)
        if len(self._counts) > self._capacity:
            self._counts.popitem(last=False)
        return None
