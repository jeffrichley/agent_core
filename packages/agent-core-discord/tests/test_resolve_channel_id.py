"""Tests for DiscordEndpoint._resolve_channel_id (auto-echo chain for #83)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from agent_core_discord.endpoint import DiscordEndpoint, _ToolError
from agent_core_discord.testing.fakes import FakeDiscordClient

from agent_core.bus.envelope import Envelope, TextMessagePayload


async def _make_endpoint(monkeypatch):
    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="d", target="t", token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    return ep


def _outbound(*, channel_id=None, in_reply_to=None, eid="out-1"):
    md = {}
    if channel_id is not None:
        md["discord"] = {"channel_id": channel_id}
    return Envelope(
        id=eid, correlation_id="c", from_="agent", to="d",
        kind="TextMessage", payload=TextMessagePayload(text=""),
        in_reply_to=in_reply_to, metadata=md,
        created_at=datetime.now(UTC),
    )


def _inbound(*, eid="in-1", channel_id="1491"):
    return Envelope(
        id=eid, correlation_id="c", from_="d", to="agent",
        kind="TextMessage", payload=TextMessagePayload(text="hi"),
        metadata={"discord": {"channel_id": channel_id}},
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_resolve_explicit_channel_id_wins_over_cache_hit(monkeypatch):
    """Topic-override invariant: explicit always wins, in_reply_to ignored."""
    ep = await _make_endpoint(monkeypatch)
    ep._record_inbound(_inbound(eid="in-1", channel_id="from-cache"))
    out = _outbound(channel_id="explicit-channel", in_reply_to="in-1")
    assert ep._resolve_channel_id(out) == "explicit-channel"


@pytest.mark.asyncio
async def test_resolve_returns_cached_channel_id_on_cache_hit_when_explicit_missing(monkeypatch):
    ep = await _make_endpoint(monkeypatch)
    ep._record_inbound(_inbound(eid="in-1", channel_id="from-cache"))
    out = _outbound(in_reply_to="in-1")
    assert ep._resolve_channel_id(out) == "from-cache"


@pytest.mark.asyncio
async def test_resolve_raises_and_logs_when_neither_explicit_nor_in_reply_to_set(monkeypatch, caplog):
    ep = await _make_endpoint(monkeypatch)
    out = _outbound()  # no channel, no in_reply_to
    with caplog.at_level(logging.WARNING):
        with pytest.raises(_ToolError, match="cannot determine channel"):
            ep._resolve_channel_id(out)
    assert "no_explicit_no_in_reply_to" in caplog.text


@pytest.mark.asyncio
async def test_resolve_raises_and_logs_on_cache_miss_never_recorded(monkeypatch, caplog):
    ep = await _make_endpoint(monkeypatch)
    out = _outbound(in_reply_to="never-was")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(_ToolError):
            ep._resolve_channel_id(out)
    assert "cache_miss" in caplog.text


@pytest.mark.asyncio
async def test_resolve_raises_and_logs_when_cached_inbound_has_no_channel_id(monkeypatch, caplog):
    ep = await _make_endpoint(monkeypatch)
    # Cached inbound with empty channel_id (defensive).
    bad = Envelope(
        id="in-1", correlation_id="c", from_="d", to="agent", kind="TextMessage",
        payload=TextMessagePayload(text=""),
        metadata={"discord": {"channel_id": ""}},
        created_at=datetime.now(UTC),
    )
    ep._record_inbound(bad)
    out = _outbound(in_reply_to="in-1")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(_ToolError):
            ep._resolve_channel_id(out)
    assert "cached_inbound_missing_channel_id" in caplog.text


@pytest.mark.asyncio
async def test_resolve_error_message_is_unified_across_sub_causes(monkeypatch):
    """Same _ToolError message regardless of which sub-cause triggered it."""
    ep = await _make_endpoint(monkeypatch)
    # no_explicit case
    with pytest.raises(_ToolError) as exc_neither:
        ep._resolve_channel_id(_outbound())
    # cache_miss case
    with pytest.raises(_ToolError) as exc_miss:
        ep._resolve_channel_id(_outbound(in_reply_to="ghost"))
    assert str(exc_neither.value) == str(exc_miss.value)


@pytest.mark.asyncio
async def test_resolve_raises_and_logs_on_cache_miss_after_ttl_eviction(monkeypatch, caplog):
    ep = await _make_endpoint(monkeypatch)
    ep._recent_inbounds_ttl_seconds = 10.0
    ep._record_inbound(_inbound(eid="aged", channel_id="X"))
    import time
    ep._recent_inbounds_timestamps["aged"] = time.monotonic() - 999.0
    ep._sweep_recent_inbounds_once()
    out = _outbound(in_reply_to="aged")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(_ToolError):
            ep._resolve_channel_id(out)
    assert "cache_miss" in caplog.text


@pytest.mark.asyncio
async def test_resolve_raises_and_logs_on_cache_miss_after_lru_eviction(monkeypatch, caplog):
    ep = await _make_endpoint(monkeypatch)
    ep._recent_inbounds_max = 1
    ep._record_inbound(_inbound(eid="first", channel_id="X"))
    ep._record_inbound(_inbound(eid="second", channel_id="Y"))  # evicts first
    out = _outbound(in_reply_to="first")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(_ToolError):
            ep._resolve_channel_id(out)
    assert "cache_miss" in caplog.text


@pytest.mark.asyncio
async def test_resolve_raises_and_logs_on_cache_miss_after_daemon_restart(monkeypatch, caplog):
    """Fresh endpoint instance simulates cold start; cache empty."""
    ep = await _make_endpoint(monkeypatch)
    out = _outbound(in_reply_to="pre-restart")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(_ToolError):
            ep._resolve_channel_id(out)
    assert "cache_miss" in caplog.text
