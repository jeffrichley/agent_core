"""shape_validator — pure-function recognized-shape catalog for the Discord
adapter's strict-mode envelope validation.

Closes the silent-drop class on the discord-send surface (#114). The
adapter calls validate(envelope) at the top of deliver(): on
Unrecognized, a yellow failed-delivery Acknowledgment is published to
the sender with the canonical equivalent named; on Recognized + a
deprecation_log_line, a structured log fires and dispatch proceeds; on
Recognized + None (the canonical shape), dispatch proceeds silently.

PURE module: no I/O, no Discord client, no global state. Unit-testable
in isolation. See docs/superpowers/specs/2026-05-23-issue-114-discord-
send-unified-envelope-design.md for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_core.bus.envelope import Envelope


@dataclass(frozen=True)
class Recognized:
    """Validator outcome: the envelope matches a known shape.

    shape_name: stable identifier for the matched shape, used as the
        aggregation key in deprecation-readiness telemetry.
    deprecation_log_line: human-readable message for the structured
        deprecation log when the shape is legacy. None for the canonical
        shape (no log emitted for the happy path).
    """

    shape_name: str
    deprecation_log_line: str | None


@dataclass(frozen=True)
class Unrecognized:
    """Validator outcome: the envelope carries fields the adapter does
    not route.

    fields: the unrecognized field path(s). For nested-path inputs
        ('metadata.discord.foo.bar.baz' with 'foo' unknown), this is
        the first unknown prefix ('metadata.discord.foo'), not the
        leaves.
    canonical_equivalent: human-readable hint for the sender's failed-
        delivery Acknowledgment note, naming the canonical way to send
        the same intent.
    """

    fields: list[str]
    canonical_equivalent: str


ShapeValidation = Recognized | Unrecognized

# Recognized arg names for the new canonical _DiscordSendArgs model.
# Keep in lockstep with packages/agent-core-discord/src/agent_core_discord/
# args.py:_DiscordSendArgs field set. The test_canonical_keys_in_sync test
# (Task 5) asserts the lockstep.
_KNOWN_CANONICAL_SEND_ARGS: frozenset[str] = frozenset({
    "channel_id",
    "text",
    "embeds",
    "files",
    "reply_to",
    "allowed_mentions",
    "components",
    "cleanup_inbound_message_id",
})

# Recognized arg names for the existing _SendArgs (legacy `tool=send` /
# `tool=send_discord_message` routes). Must stay in lockstep with
# args.py:_SendArgs. allowed_mentions and components are NOT in this
# set — those are canonical-only future-proof slots, not exposed on the
# legacy routes.
_KNOWN_LEGACY_SEND_ARGS: frozenset[str] = frozenset({
    "channel_id",
    "text",
    "embeds",
    "reply_to",
    "files",
    "cleanup_inbound_message_id",
})


def _validate_tool_invocation(envelope: Envelope) -> ShapeValidation:
    payload = envelope.payload
    tool = getattr(payload, "tool", "") or ""
    args = getattr(payload, "args", {}) or {}

    if tool == "discord_send":
        unrecognized = _unrecognized_arg_keys(args, _KNOWN_CANONICAL_SEND_ARGS)
        if unrecognized:
            return Unrecognized(
                fields=[f"args.{k}" for k in unrecognized],
                canonical_equivalent=(
                    "tool=discord_send accepts "
                    f"{sorted(_KNOWN_CANONICAL_SEND_ARGS)}"
                ),
            )
        return Recognized("canonical_discord_send", None)

    if tool in ("send", "send_discord_message"):
        unrecognized = _unrecognized_arg_keys(args, _KNOWN_LEGACY_SEND_ARGS)
        if unrecognized:
            return Unrecognized(
                fields=[f"args.{k}" for k in unrecognized],
                canonical_equivalent=(
                    f"tool=discord_send with {', '.join(unrecognized)} in args"
                ),
            )
        return Recognized(
            f"legacy_tool_{tool}",
            f"deprecated_shape: tool={tool} — use tool=discord_send",
        )

    # All other tools (edit, react, fetch, list_channels, create_poll, etc.)
    # are outside the strict-mode scope. deliver() does not block on them.
    return Recognized("non_send_tool", None)


def _unrecognized_arg_keys(args: object, known: frozenset[str]) -> list[str]:
    if not isinstance(args, dict):
        return []
    return sorted(set(args.keys()) - known)


# Recognized keys under metadata.discord on OUTBOUND TextMessage
# envelopes. INBOUND-only keys (message_id, guild_id, author_id,
# author_display_name, is_dm) are not in this set — they are set by
# the adapter when publishing inbound, and a sender that sets them on
# an outbound is doing something the adapter does not route. Flag as
# Unrecognized so the silent-drop class is closed (Task 4).
_KNOWN_DISCORD_META_OUTBOUND_KEYS: frozenset[str] = frozenset({
    "channel_id",
    "embeds",
    "reply_to",
})


def _validate_text_message(envelope: Envelope) -> ShapeValidation:
    metadata = envelope.metadata or {}
    discord_meta = metadata.get("discord")

    if discord_meta is None:
        # TextMessage without metadata.discord is not discord-bound.
        # Adapter's existing routing handles this via outbound_channel_id
        # fallback or _ToolError.
        return Recognized("non_discord_text_message", None)

    if not isinstance(discord_meta, dict):
        # metadata.discord is present but malformed (string, list, etc.).
        return Unrecognized(
            fields=["metadata.discord"],
            canonical_equivalent=(
                "tool=discord_send with channel_id in args (metadata.discord "
                "must be an object)"
            ),
        )

    # Unrecognized-key detection lands in Task 4. For now, recognize
    # legacy shapes by the most-discriminating field present.
    if "embeds" in discord_meta:
        return Recognized(
            "legacy_textmessage_embeds",
            "deprecated_shape: TextMessage + metadata.discord.embeds — "
            "use tool=discord_send with embeds in args",
        )
    if "reply_to" in discord_meta:
        return Recognized(
            "legacy_textmessage_reply",
            "deprecated_shape: TextMessage + metadata.discord.reply_to — "
            "use tool=discord_send with reply_to in args",
        )
    if "channel_id" in discord_meta:
        return Recognized(
            "legacy_textmessage_plain",
            "deprecated_shape: TextMessage + metadata.discord.channel_id — "
            "use tool=discord_send with text in args",
        )
    # Ambiguous discord_meta block (e.g., empty {} or only message_id).
    return Recognized(
        "legacy_textmessage_ambiguous",
        "deprecated_shape: TextMessage + metadata.discord without channel_id",
    )


def validate(envelope: Envelope) -> ShapeValidation:
    """Validate that the Discord adapter has routing for every field on
    envelope. Returns Recognized(shape_name, deprecation_log_line_or_None)
    or Unrecognized(fields, canonical_equivalent).

    ToolInvocation and TextMessage shapes covered; unrecognized-field
    detection (multi-field, nested) tightens in Task 4.
    """
    if envelope.kind == "ToolInvocation":
        return _validate_tool_invocation(envelope)
    if envelope.kind == "TextMessage":
        return _validate_text_message(envelope)
    return Recognized("non_send_kind", None)
