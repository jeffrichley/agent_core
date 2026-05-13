"""Tests for `_awaiting_reply_ids` + `_awaiting_reply_ids_timestamps` pair-management (#84)."""

from __future__ import annotations

import uuid

import pytest
from agent_core_discord.testing.fakes import (
    FakeBusHandle,
    FakeChannel,
    FakeDiscordClient,
    FakeMessage,
    FakeUser,
)


async def _started(monkeypatch):
    """Construct a started DiscordEndpoint with a fake bus handle and fake client."""
    from agent_core_discord.endpoint import DiscordEndpoint

    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    fake.user = FakeUser(id="bot", display_name="bot", bot=True)
    handle = FakeBusHandle()
    name = f"discord-pair-test-{uuid.uuid4().hex[:8]}"
    ep = DiscordEndpoint(
        name=name, target="agent-test", token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    return ep, handle, fake


@pytest.mark.asyncio
async def test_awaiting_reply_id_and_timestamp_seeded_together_on_message(monkeypatch):
    """on_message seeding: both `_awaiting_reply_ids` and `_awaiting_reply_ids_timestamps`
    receive the Discord message_id together."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    inbound = FakeMessage(id="m-seed", channel_id="100", content="hi")
    inbound.channel = ch
    inbound.author = FakeUser(id="user-1", display_name="someone", bot=False)
    inbound.guild = type("G", (), {"id": "guild-1"})()
    try:
        await fake.fire("on_message", inbound)
        assert "m-seed" in ep._awaiting_reply_ids
        assert "m-seed" in ep._awaiting_reply_ids_timestamps
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_clear_pending_ack_clears_both_sets(monkeypatch):
    """_clear_pending_ack helper discards from BOTH _awaiting_reply_ids
    and _awaiting_reply_ids_timestamps. Covers the three sites that funnel
    through this helper (lines 1095, 1137, 1159)."""
    import time

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    ep._awaiting_reply_ids.add("m-clear")
    ep._awaiting_reply_ids_timestamps["m-clear"] = time.monotonic()
    try:
        await ep._clear_pending_ack(ch, "m-clear")
        assert "m-clear" not in ep._awaiting_reply_ids
        assert "m-clear" not in ep._awaiting_reply_ids_timestamps
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_awaiting_reply_id_cleared_on_publish_rollback(monkeypatch):
    """on_message's publish-failure rollback (line 908) clears BOTH sets
    so a failed publish doesn't leave orphan tracking state."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    inbound = FakeMessage(id="m-fail", channel_id="100", content="x")
    inbound.channel = ch
    inbound.author = FakeUser(id="user-2", display_name="someone", bot=False)
    inbound.guild = type("G", (), {"id": "guild-1"})()

    # Force publish to raise.
    async def _raise_publish(*args, **kwargs):
        raise RuntimeError("simulated publish failure")

    monkeypatch.setattr(handle, "publish", _raise_publish)

    try:
        with pytest.raises(RuntimeError):
            await fake.fire("on_message", inbound)
        assert "m-fail" not in ep._awaiting_reply_ids
        assert "m-fail" not in ep._awaiting_reply_ids_timestamps
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_awaiting_reply_id_cleared_on_endpoint_stop(monkeypatch):
    """stop() bulk-clear (line 782) clears BOTH _awaiting_reply_ids
    and _awaiting_reply_ids_timestamps."""
    import time

    ep, handle, fake = await _started(monkeypatch)
    ep._awaiting_reply_ids.add("m-stop")
    ep._awaiting_reply_ids_timestamps["m-stop"] = time.monotonic()
    await ep.stop()
    assert ep._awaiting_reply_ids == set()
    assert ep._awaiting_reply_ids_timestamps == {}
