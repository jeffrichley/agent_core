"""DM-policy + channel-allowlist access gate.

Ported from Pepper's `pepper/integrations/discord/access.py`. The shape of
the JSON config (dmPolicy / allowFrom / channels / ackReaction) is preserved
verbatim so existing Pepper access configs migrate without rewrite.
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


@dataclass
class AccessConfig:
    """Validated access policy for a single Discord bot."""

    dm_policy: DmPolicy = "open"
    allow_from: list[str] = field(default_factory=list)
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    ack_reaction: str = "👀"


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
    dm_policy = raw.get("dmPolicy", "open")
    if dm_policy not in _VALID_DM_POLICIES:
        # Fail-closed on unknown values rather than fall through gate_message
        # branches that would otherwise default to allowlist semantics.
        log.warning(
            "access config %s: unknown dmPolicy %r; falling back to 'deny'",
            p,
            dm_policy,
        )
        dm_policy = "deny"
    return AccessConfig(
        dm_policy=dm_policy,  # type: ignore[arg-type]
        allow_from=list(raw.get("allowFrom", [])),
        channels=dict(raw.get("channels", {})),
        ack_reaction=raw.get("ackReaction", "👀"),
    )


def gate_message(cfg: AccessConfig, ctx: InboundContext) -> bool:
    """Return True if the inbound message passes the access gate.

    Bot-authored messages are always blocked. DMs go through dm_policy:
        - "open"      → allow.
        - "deny"      → block.
        - "allowlist" → allow only if author_id is in allow_from.
    Guild messages go through the channel allowlist if non-empty:
        - empty channels dict → allow all guild channels.
        - non-empty           → allow only if channel_id is a key.
    """
    if ctx.is_bot:
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
