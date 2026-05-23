"""Unit tests for _DiscordSendArgs — the strict canonical args model
for tool=discord_send (#114).

The model uses extra='forbid' as the second strict layer behind the
envelope-level shape_validator: a typo on the args side raises
ValidationError at dispatch, which translates to a yellow Ack via the
existing _ToolError path. No silent drop.
"""

import pytest
from agent_core_discord.args import _DiscordSendArgs
from pydantic import ValidationError


def test_canonical_minimal_text_send_accepted():
    """channel_id + text is the smallest valid canonical args."""
    args = _DiscordSendArgs(channel_id="123", text="hi")
    assert args.channel_id == "123"
    assert args.text == "hi"
    assert args.embeds is None
    assert args.files is None


def test_all_optional_fields_accepted_independently():
    """Each optional field is accepted on its own. Spot-check the field
    set rather than enumerating exhaustively."""
    _DiscordSendArgs(channel_id="1", embeds=[{"title": "x"}])
    _DiscordSendArgs(channel_id="1", files=["/tmp/a"])
    _DiscordSendArgs(channel_id="1", reply_to="456")
    _DiscordSendArgs(channel_id="1", allowed_mentions={"users": []})
    _DiscordSendArgs(channel_id="1", components=[{"type": 1}])
    _DiscordSendArgs(channel_id="1", cleanup_inbound_message_id="789")


def test_missing_channel_id_raises():
    with pytest.raises(ValidationError) as exc:
        _DiscordSendArgs(text="hi")
    assert "channel_id" in str(exc.value)


def test_empty_channel_id_raises():
    """channel_id is required and must be non-empty."""
    with pytest.raises(ValidationError):
        _DiscordSendArgs(channel_id="")


def test_extra_field_rejected_by_forbid():
    """extra='forbid' is the contract: any unknown field at the args
    level raises ValidationError. The shape_validator should catch this
    case BEFORE Pydantic, but this is the defense-in-depth layer."""
    with pytest.raises(ValidationError) as exc:
        _DiscordSendArgs(channel_id="123", text="hi", mystery_field="X")
    msg = str(exc.value)
    assert "mystery_field" in msg or "extra" in msg.lower()


def test_canonical_keys_in_sync_with_validator_catalog():
    """The canonical args field set must stay in lockstep with
    shape_validator._KNOWN_CANONICAL_SEND_ARGS. A drift between them
    would surface as a sender being rejected by one layer but accepted
    by the other."""
    from agent_core_discord.shape_validator import _KNOWN_CANONICAL_SEND_ARGS

    model_fields = set(_DiscordSendArgs.model_fields.keys())
    assert model_fields == _KNOWN_CANONICAL_SEND_ARGS


def test_legacy_keys_in_sync_with_validator_catalog():
    """The legacy args field set (_SendArgs, used by tool=send and the
    tool=send_discord_message alias) must stay in lockstep with
    shape_validator._KNOWN_LEGACY_SEND_ARGS. Drift here would mean the
    validator produces Unrecognized for envelopes that _dispatch
    happily routes — a silent split.

    Mirror of test_canonical_keys_in_sync_with_validator_catalog for
    the legacy surface. Per the final-review feedback on #114."""
    from agent_core_discord.args import _SendArgs
    from agent_core_discord.shape_validator import _KNOWN_LEGACY_SEND_ARGS

    model_fields = set(_SendArgs.model_fields.keys())
    assert model_fields == _KNOWN_LEGACY_SEND_ARGS
