"""Tests for DiscordEndpoint._recent_inbounds cache (auto-echo for #83)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from agent_core_discord.endpoint import DiscordEndpoint

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core_discord.testing.fakes import FakeDiscordClient


async def _make_endpoint(monkeypatch, **kwargs):
    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        _client_factory=lambda **kw: fake,
        **kwargs,
    )
    return ep, fake


@pytest.mark.asyncio
async def test_recent_inbounds_records_inbound_on_publish(monkeypatch):
    """_record_inbound(env) adds env to the cache keyed by env.id."""
    ep, _fake = await _make_endpoint(monkeypatch)
    env = Envelope(
        id="abc",
        correlation_id="c1",
        from_="discord-test",
        to="agent-test",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        metadata={"discord": {"channel_id": "1491"}},
        created_at=datetime.now(UTC),
    )
    ep._record_inbound(env)
    cached = ep._recent_inbounds.get("abc")
    assert cached is not None
    assert cached.id == "abc"
    assert (cached.metadata or {}).get("discord", {}).get("channel_id") == "1491"
