"""Tests for the DM-policy + channel-allowlist access gate."""

from __future__ import annotations

import json

from agent_core_discord.access import (
    AccessConfig,
    InboundContext,
    gate_message,
    load_access_config,
)


def test_load_access_config_returns_defaults_for_missing_path():
    cfg = load_access_config(None)
    assert cfg.dm_policy == "open"
    assert cfg.allow_from == []
    assert cfg.channels == {}
    assert cfg.ack_reaction == "👀"


def test_load_access_config_returns_defaults_for_missing_file(tmp_path):
    cfg = load_access_config(tmp_path / "missing.json")
    assert cfg.dm_policy == "open"
    assert cfg.ack_reaction == "👀"


def test_load_access_config_unknown_dm_policy_falls_back_to_deny(tmp_path, caplog):
    """Defense in depth: unknown dmPolicy values fail closed, not open."""
    import logging

    p = tmp_path / "access.json"
    p.write_text(json.dumps({"dmPolicy": "Open", "allowFrom": ["999"]}), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        cfg = load_access_config(p)
    assert cfg.dm_policy == "deny"
    assert cfg.allow_from == ["999"]
    assert any("unknown dmPolicy" in rec.message for rec in caplog.records)


def test_load_access_config_parses_json(tmp_path):
    p = tmp_path / "access.json"
    p.write_text(
        json.dumps(
            {
                "dmPolicy": "allowlist",
                "allowFrom": ["100"],
                "channels": {"200": {}},
                "ackReaction": "👁️",
            }
        ),
        encoding="utf-8",
    )
    cfg = load_access_config(p)
    assert cfg.dm_policy == "allowlist"
    assert cfg.allow_from == ["100"]
    assert cfg.channels == {"200": {}}
    assert cfg.ack_reaction == "👁️"


def _ctx(*, is_dm: bool, author_id: str = "100", channel_id: str = "200") -> InboundContext:
    return InboundContext(is_dm=is_dm, author_id=author_id, channel_id=channel_id, is_bot=False)


def test_gate_blocks_bot_authors_by_default():
    """Empty allowed_bot_ids preserves the historical 'all bots blocked' default."""
    cfg = AccessConfig(dm_policy="open")
    ctx = InboundContext(is_dm=False, author_id="100", channel_id="200", is_bot=True)
    assert gate_message(cfg, ctx) is False


def test_gate_allows_bot_in_allowlist():
    cfg = AccessConfig(dm_policy="open", allowed_bot_ids=["1480938766246871050"])
    ctx = InboundContext(
        is_dm=False, author_id="1480938766246871050", channel_id="200", is_bot=True
    )
    assert gate_message(cfg, ctx) is True


def test_gate_blocks_bot_not_in_allowlist():
    cfg = AccessConfig(dm_policy="open", allowed_bot_ids=["1480938766246871050"])
    ctx = InboundContext(is_dm=False, author_id="999", channel_id="200", is_bot=True)
    assert gate_message(cfg, ctx) is False


def test_load_access_config_reads_allowed_bot_ids(tmp_path):
    p = tmp_path / "access.json"
    p.write_text(
        json.dumps({"allowedBotIds": ["123", "456"]}),
        encoding="utf-8",
    )
    cfg = load_access_config(p)
    assert cfg.allowed_bot_ids == ["123", "456"]


def test_load_access_config_missing_allowed_bot_ids_defaults_empty(tmp_path):
    p = tmp_path / "access.json"
    p.write_text(json.dumps({"dmPolicy": "open"}), encoding="utf-8")
    cfg = load_access_config(p)
    assert cfg.allowed_bot_ids == []


def test_load_access_config_non_list_allowed_bot_ids_falls_back_to_empty(tmp_path, caplog):
    """A non-list value (string, dict, etc.) for allowedBotIds is rejected
    and the loader falls back to empty. Prevents a stringified id from
    being iterated as characters and silently admitting bots whose id is
    any single matching character."""
    import logging

    p = tmp_path / "access.json"
    p.write_text(
        json.dumps({"allowedBotIds": "1480938766246871050"}),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        cfg = load_access_config(p)
    assert cfg.allowed_bot_ids == []
    assert any("allowedBotIds must be a list" in rec.message for rec in caplog.records)


def test_load_access_config_coerces_int_bot_ids_to_str(tmp_path):
    """Discord ids are conceptually strings but JSON authors may write them
    as integers. The loader coerces so equality against `ctx.author_id`
    (which is always str) doesn't silently fail."""
    p = tmp_path / "access.json"
    p.write_text(
        json.dumps({"allowedBotIds": [1480938766246871050]}),
        encoding="utf-8",
    )
    cfg = load_access_config(p)
    assert cfg.allowed_bot_ids == ["1480938766246871050"]


def test_gate_open_dm_policy_allows_any_dm():
    cfg = AccessConfig(dm_policy="open")
    assert gate_message(cfg, _ctx(is_dm=True)) is True


def test_gate_deny_dm_policy_blocks_dms():
    cfg = AccessConfig(dm_policy="deny")
    assert gate_message(cfg, _ctx(is_dm=True)) is False


def test_gate_allowlist_dm_policy_passes_for_listed_user():
    cfg = AccessConfig(dm_policy="allowlist", allow_from=["100"])
    assert gate_message(cfg, _ctx(is_dm=True, author_id="100")) is True


def test_gate_allowlist_dm_policy_blocks_unlisted_user():
    cfg = AccessConfig(dm_policy="allowlist", allow_from=["100"])
    assert gate_message(cfg, _ctx(is_dm=True, author_id="999")) is False


def test_gate_with_no_channel_map_accepts_all_guild_channels():
    cfg = AccessConfig(dm_policy="open", channels={})
    assert gate_message(cfg, _ctx(is_dm=False, channel_id="ANY")) is True


def test_gate_with_channel_allowlist_accepts_only_listed():
    cfg = AccessConfig(dm_policy="open", channels={"200": {}})
    assert gate_message(cfg, _ctx(is_dm=False, channel_id="200")) is True
    assert gate_message(cfg, _ctx(is_dm=False, channel_id="201")) is False


def test_gate_dm_policy_does_not_apply_to_guild_messages():
    """A 'deny' DM policy still allows guild channel messages."""
    cfg = AccessConfig(dm_policy="deny")
    assert gate_message(cfg, _ctx(is_dm=False, channel_id="200")) is True


def test_gate_allowlist_dm_policy_does_not_block_guild_messages():
    """allowlist DM policy applies only to DMs, not guild posts."""
    cfg = AccessConfig(dm_policy="allowlist", allow_from=["100"])
    assert gate_message(cfg, _ctx(is_dm=False, author_id="999", channel_id="200")) is True


def test_gate_allowlisted_bot_must_still_pass_channel_filter():
    """Regression: an allowlisted other-bot in a NON-allowlisted channel must
    be blocked. The bot-id allowlist opts a bot past the default bot-block,
    not past the channel filter — both checks must apply for guild posts.

    Pre-fix bug: `gate_message` returned `ctx.author_id in cfg.allowed_bot_ids`
    as a terminal answer at the top, short-circuiting the channel filter for
    every allowlisted bot regardless of where it posted. Pepper's bot posts
    in #pepper-chat (which is NOT in discord-wren's `channels` allowlist)
    reached the wren endpoint anyway. The bot-block must layer: deny
    non-allowlisted bots, then fall through to the same channel + DM checks
    every other author goes through.
    """
    cfg = AccessConfig(
        dm_policy="open",
        channels={"allowed-channel": {}},
        allowed_bot_ids=["pepper-bot"],
    )
    ctx_allowed = InboundContext(
        is_dm=False,
        author_id="pepper-bot",
        channel_id="allowed-channel",
        is_bot=True,
    )
    ctx_wrong_channel = InboundContext(
        is_dm=False,
        author_id="pepper-bot",
        channel_id="other-channel",
        is_bot=True,
    )
    assert gate_message(cfg, ctx_allowed) is True
    assert gate_message(cfg, ctx_wrong_channel) is False


def test_gate_allowlisted_bot_dm_still_goes_through_dm_policy():
    """An allowlisted bot DMing must still respect dm_policy. With
    `allowlist` dm_policy and the bot's id not in `allow_from`, deny.
    The bot-id allowlist opens up bot authorship, not DM access."""
    cfg = AccessConfig(
        dm_policy="allowlist",
        allow_from=["jeff-user"],
        allowed_bot_ids=["pepper-bot"],
    )
    ctx = InboundContext(
        is_dm=True,
        author_id="pepper-bot",
        channel_id="dm",
        is_bot=True,
    )
    assert gate_message(cfg, ctx) is False


def test_load_access_config_silently_ignores_legacy_urgency_red_regex(tmp_path):
    """Migration: existing access JSON files with urgencyRedRegex set must
    still load cleanly under the post-#38 AccessConfig (which doesn't have
    the field). The key is silently ignored — no warning, no error.
    """
    p = tmp_path / "access.json"
    p.write_text(
        json.dumps(
            {
                "dmPolicy": "open",
                "ackReaction": "👀",
                "urgencyRedRegex": r"(?i)\b(urgent|now|stop)\b",
            }
        ),
        encoding="utf-8",
    )
    cfg = load_access_config(p)
    assert cfg.dm_policy == "open"
    assert cfg.ack_reaction == "👀"
    assert not hasattr(cfg, "urgency_red_regex")
