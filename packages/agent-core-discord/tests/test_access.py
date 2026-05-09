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


def test_gate_blocks_bot_authors_unconditionally():
    cfg = AccessConfig(dm_policy="open")
    ctx = InboundContext(is_dm=False, author_id="100", channel_id="200", is_bot=True)
    assert gate_message(cfg, ctx) is False


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
