"""Unit tests for shape_validator — the pure-function recognized-shape catalog
for the Discord adapter's strict-mode envelope validation.

The validator is PURE: no I/O, no Discord client, no chrome.* / discord.*
imports. These tests construct envelopes directly and assert on the
returned ShapeValidation value.
"""

from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import (
    Envelope,
    TextMessagePayload,
    ToolInvocationPayload,
)
from agent_core_discord.shape_validator import (
    Recognized,
    Unrecognized,
    validate,
)


def _make_env(
    *,
    kind: str,
    payload: object,
    metadata: dict[str, object] | None = None,
    from_: str = "test-sender",
) -> Envelope:
    """Helper: build an Envelope with sensible defaults for validator tests."""
    return Envelope(
        id="env-abc",
        correlation_id="corr-1",
        to="discord-test",
        kind=kind,
        payload=payload,
        metadata=metadata or {},
        created_at=datetime.now(UTC),
        from_=from_,
    )


def test_recognized_and_unrecognized_are_frozen_dataclasses():
    """Both ShapeValidation variants are frozen so test assertions can compare
    by value and Recognized/Unrecognized can be used as dict keys later."""
    r = Recognized(shape_name="x", deprecation_log_line=None)
    u = Unrecognized(fields=["a"], canonical_equivalent="b")
    with pytest.raises(AttributeError):
        r.shape_name = "y"  # frozen
    with pytest.raises(AttributeError):
        u.fields = ["c"]    # frozen


def test_canonical_discord_send_returns_recognized_no_deprecation():
    """The canonical tool=discord_send + canonical args is the new happy
    path; no deprecation log fires."""
    env = _make_env(
        kind="ToolInvocation",
        payload=ToolInvocationPayload(
            tool="discord_send",
            args={"channel_id": "123", "text": "hi"},
        ),
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "canonical_discord_send"
    assert result.deprecation_log_line is None


def test_legacy_tool_send_returns_recognized_with_deprecation():
    """tool=send is the pre-#114 internal canonical; ships as a legacy
    alias after #114 with a deprecation-log line."""
    env = _make_env(
        kind="ToolInvocation",
        payload=ToolInvocationPayload(
            tool="send",
            args={"channel_id": "123", "text": "hi"},
        ),
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "legacy_tool_send"
    assert result.deprecation_log_line is not None
    assert "tool=discord_send" in result.deprecation_log_line


def test_legacy_tool_send_discord_message_returns_recognized_with_deprecation():
    """tool=send_discord_message is the existing public alias; ships as a
    legacy alias after #114 with a deprecation-log line."""
    env = _make_env(
        kind="ToolInvocation",
        payload=ToolInvocationPayload(
            tool="send_discord_message",
            args={"channel_id": "123", "text": "hi", "embeds": [{"title": "x"}]},
        ),
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "legacy_tool_send_discord_message"
    assert result.deprecation_log_line is not None
    assert "tool=discord_send" in result.deprecation_log_line


def test_other_tool_invocation_is_non_send_tool():
    """Non-send tools (edit, react, fetch, etc.) are outside the
    validator's strict-mode scope. They return Recognized with shape
    'non_send_tool' so deliver() does not gate on them."""
    env = _make_env(
        kind="ToolInvocation",
        payload=ToolInvocationPayload(
            tool="edit",
            args={"channel_id": "123", "message_id": "456", "text": "edited"},
        ),
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "non_send_tool"
    assert result.deprecation_log_line is None


def test_textmessage_plain_text_returns_recognized_with_deprecation():
    """The most-common legacy shape: TextMessage with channel_id only."""
    env = _make_env(
        kind="TextMessage",
        payload=TextMessagePayload(text="hello"),
        metadata={"discord": {"channel_id": "123"}},
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "legacy_textmessage_plain"
    assert result.deprecation_log_line is not None


def test_textmessage_with_embeds_returns_recognized_with_deprecation():
    """The poster-child shape from #114 — routed since a278c68 but
    still legacy. Embed presence determines the shape_name."""
    env = _make_env(
        kind="TextMessage",
        payload=TextMessagePayload(text=""),
        metadata={"discord": {"channel_id": "123", "embeds": [{"title": "x"}]}},
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "legacy_textmessage_embeds"
    assert result.deprecation_log_line is not None


def test_textmessage_with_reply_to_returns_recognized_with_deprecation():
    """reply_to legacy shape — distinguished by the presence of reply_to
    in the metadata.discord block."""
    env = _make_env(
        kind="TextMessage",
        payload=TextMessagePayload(text="reply"),
        metadata={
            "discord": {"channel_id": "123", "reply_to": "456"},
        },
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "legacy_textmessage_reply"
    assert result.deprecation_log_line is not None


def test_textmessage_no_discord_metadata_is_non_discord():
    """TextMessage envelopes with no metadata.discord block at all are
    not discord-bound; validator returns Recognized so deliver() doesn't
    treat the absence of routing as a failure."""
    env = _make_env(
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        metadata={},
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "non_discord_text_message"
    assert result.deprecation_log_line is None


def test_textmessage_ambiguous_discord_meta_returns_legacy_ambiguous():
    """metadata.discord block with no recognized routing-discriminating
    keys (no channel_id, embeds, or reply_to) is reachable today via
    inbound-only keys like message_id slipping into an outbound. The
    branch returns Recognized so deliver() does not silently drop, but
    flags the shape as ambiguous in the structured log."""
    env = _make_env(
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        metadata={"discord": {"message_id": "789"}},
    )
    result = validate(env)
    assert isinstance(result, Recognized)
    assert result.shape_name == "legacy_textmessage_ambiguous"
    assert result.deprecation_log_line is not None
