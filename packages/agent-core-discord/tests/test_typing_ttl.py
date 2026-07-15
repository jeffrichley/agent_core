"""Tests for `_typing_while_pending` TTL safety net + self-heal (#84)."""

from __future__ import annotations

import asyncio
import time

import pytest
from agent_core_discord.testing.fakes import (
    FakeBusHandle,
    FakeChannel,
    FakeDiscordClient,
    FakeUser,
)


async def _started(monkeypatch):
    from agent_core_discord.endpoint import DiscordEndpoint

    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    fake.user = FakeUser(id="bot", display_name="bot", bot=True)
    handle = FakeBusHandle()
    ep = DiscordEndpoint(
        name="discord-test", target="agent-test", token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    return ep, handle, fake


@pytest.mark.asyncio
async def test_typing_evicts_after_ttl_when_no_cleanup_fires(monkeypatch):
    """Orphan entry exceeds TTL → polling loop evicts both sets and exits."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    ep._awaiting_reply_ids.add("m-old")
    ep._awaiting_reply_ids_timestamps["m-old"] = time.monotonic() - 100.0
    try:
        task = asyncio.create_task(ep._typing_while_pending(ch, "m-old"))
        # Deterministic: wait for the loop to evict + exit, rather than sleeping a
        # fixed wall-clock guess (which flakes under load). Times out (and fails)
        # if the loop genuinely hangs.
        await asyncio.wait_for(task, timeout=2.0)
        assert "m-old" not in ep._awaiting_reply_ids
        assert "m-old" not in ep._awaiting_reply_ids_timestamps
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_typing_does_not_evict_within_ttl_window(monkeypatch):
    """Fresh entry stays in both sets while polling loop runs."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    ep._awaiting_reply_ids.add("m-fresh")
    ep._awaiting_reply_ids_timestamps["m-fresh"] = time.monotonic() - 10.0
    try:
        task = asyncio.create_task(ep._typing_while_pending(ch, "m-fresh"))
        await asyncio.sleep(0.3)
        assert not task.done()
        assert "m-fresh" in ep._awaiting_reply_ids
        assert "m-fresh" in ep._awaiting_reply_ids_timestamps
        # Clean shutdown.
        ep._awaiting_reply_ids.discard("m-fresh")
        ep._awaiting_reply_ids_timestamps.pop("m-fresh", None)
        await asyncio.sleep(0.3)
        assert task.done()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_typing_evicts_immediately_on_missing_timestamp_self_heal(monkeypatch):
    """Self-heal: entry in set, no timestamp → `get(mid, 0)` → huge delta → immediate eviction."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    ep._awaiting_reply_ids.add("m-slipped")
    # Deliberately do NOT set _awaiting_reply_ids_timestamps["m-slipped"].
    try:
        task = asyncio.create_task(ep._typing_while_pending(ch, "m-slipped"))
        # Deterministic: wait for eviction + loop exit rather than sleeping a
        # fixed guess (this test flaked under load — #332's single-threaded gate
        # surfaced it). Times out (and fails) if eviction never happens.
        await asyncio.wait_for(task, timeout=2.0)
        assert "m-slipped" not in ep._awaiting_reply_ids
    finally:
        await ep.stop()
