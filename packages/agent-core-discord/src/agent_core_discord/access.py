"""DM-policy + channel-allowlist access gate.

Ported from Pepper's `pepper/integrations/discord/access.py`. The shape of
the JSON config (dmPolicy / allowFrom / channels / ackReaction / allowedBotIds)
is preserved verbatim so existing Pepper access configs migrate without rewrite.

The `allowedBotIds` field (added for agent_core#143) opt-in-grants specific
other-bot authors past the otherwise-default bot block — this is how Pepper
and Wren see each other on Discord without losing the default-deny posture
for unknown bots.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

DmPolicy = Literal["open", "deny", "allowlist"]
_VALID_DM_POLICIES = {"open", "deny", "allowlist"}


def _build_access_config(raw: dict[str, Any], source: str = "<unknown>") -> AccessConfig:
    """Build an AccessConfig from a pre-parsed JSON dict.

    Raises TypeError or ValueError if field values have unexpected types
    (e.g. ``channels`` is an int instead of a dict).  Callers are
    responsible for catching these and deciding how to recover.
    """
    dm_policy = raw.get("dmPolicy", "open")
    if dm_policy not in _VALID_DM_POLICIES:
        log.warning(
            "access config %s: unknown dmPolicy %r; falling back to 'deny'",
            source,
            dm_policy,
        )
        dm_policy = "deny"
    raw_allowed_bot_ids = raw.get("allowedBotIds", [])
    if not isinstance(raw_allowed_bot_ids, list):
        log.warning(
            "access config %s: allowedBotIds must be a list; got %s — falling back to empty",
            source,
            type(raw_allowed_bot_ids).__name__,
        )
        raw_allowed_bot_ids = []
    return AccessConfig(
        dm_policy=dm_policy,
        allow_from=list(raw.get("allowFrom", [])),
        channels=dict(raw.get("channels", {})),
        ack_reaction=raw.get("ackReaction", "👀"),
        allowed_bot_ids=[str(b) for b in raw_allowed_bot_ids],
    )


@dataclass
class AccessConfig:
    """Validated access policy for a single Discord bot."""

    dm_policy: DmPolicy = "open"
    allow_from: list[str] = field(default_factory=list)
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    ack_reaction: str = "👀"
    allowed_bot_ids: list[str] = field(default_factory=list)


@dataclass
class InboundContext:
    """Snapshot of an inbound Discord event for gate evaluation."""

    is_dm: bool
    author_id: str
    channel_id: str
    is_bot: bool


def load_access_config(path: Path | str | None) -> AccessConfig:
    """Load access policy from a JSON file. Permissive defaults if missing/empty."""
    if path is None:
        return AccessConfig()
    p = Path(path).expanduser()
    if not p.exists():
        log.info("access config not found at %s; using permissive defaults", p)
        return AccessConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.exception("failed to parse access config at %s; using defaults", p)
        return AccessConfig()
    return _build_access_config(raw, str(p))


def gate_message(cfg: AccessConfig, ctx: InboundContext) -> bool:
    """Return True if the inbound message passes the access gate.

    Bot-authored messages are default-blocked; the `allowed_bot_ids`
    allowlist opt-in-grants specific other-bot authors past the block.
    Empty allowlist preserves the historical "all bots blocked" default.

    DMs go through dm_policy:
        - "open"      → allow.
        - "deny"      → block.
        - "allowlist" → allow only if author_id is in allow_from.
    Guild messages go through the channel allowlist if non-empty:
        - empty channels dict → allow all guild channels.
        - non-empty           → allow only if channel_id is a key.

    The bot-block is a default-deny *guard* layered on top of the
    DM-or-channel checks, NOT a terminal answer. An allowlisted bot
    must STILL pass the channel filter for guild posts (and dm_policy
    for DMs) — same gate every other author goes through. Previously
    the bot-block returned early with `ctx.author_id in allowed_bot_ids`,
    which bypassed the channel allowlist entirely and let any
    allowlisted bot's posts in any channel through. Caught 2026-06-08
    after the Wren endpoint observed every Pepper-to-Jeff message in
    #pepper-chat (which is NOT in discord-wren's channel allowlist).
    """
    if ctx.is_bot and ctx.author_id not in cfg.allowed_bot_ids:
        return False
    if ctx.is_dm:
        if cfg.dm_policy == "open":
            return True
        if cfg.dm_policy == "deny":
            return False
        # allowlist
        return ctx.author_id in cfg.allow_from
    # Guild channel
    if not cfg.channels:
        return True
    return ctx.channel_id in cfg.channels
