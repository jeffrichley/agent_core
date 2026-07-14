"""Tests for the bounded _pending_acks structure: LRU + TTL + sweep."""

from __future__ import annotations

import time

import pytest
from agent_core_discord.endpoint import DiscordEndpoint
from agent_core_discord.testing.fakes import FakeChannel, FakeDiscordClient, FakeMessage

from agent_core.bus.envelope import EndpointInfo, Envelope


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]:
        return []

    def spawn(self, coro, *, name=None):
        import asyncio

        return asyncio.create_task(coro, name=name)


async def _make_endpoint(
    monkeypatch,
    *,
    pending_acks_max: int = 5000,
    pending_acks_ttl_seconds: float = 3600.0,
) -> tuple[DiscordEndpoint, FakeDiscordClient]:
    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        pending_acks_max=pending_acks_max,
        pending_acks_ttl_seconds=pending_acks_ttl_seconds,
        # Sweep loop period doesn't matter — tests call _sweep_pending_acks_once directly.
        pending_acks_sweep_seconds=3600.0,
        _client_factory=lambda **kw: fake,
    )
    await ep.start(_Recording())
    return ep, fake


@pytest.mark.asyncio
async def test_pending_acks_lru_eviction_caps_at_max(monkeypatch):
    """Inserting more than max evicts the oldest entry."""
    ep, fake = await _make_endpoint(monkeypatch, pending_acks_max=3)
    try:
        ep._track_pending_ack("m1", "👀", "200")
        ep._track_pending_ack("m2", "👀", "200")
        ep._track_pending_ack("m3", "👀", "200")
        assert list(ep._pending_acks.keys()) == ["m1", "m2", "m3"]
        ep._track_pending_ack("m4", "👀", "200")
        # Oldest (m1) evicted; cap holds.
        assert list(ep._pending_acks.keys()) == ["m2", "m3", "m4"]
        assert len(ep._pending_acks) == 3
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_pending_acks_lru_eviction_removes_remote_reaction(monkeypatch):
    """When LRU eviction fires, the bot's 👀 should be removed from the original message."""
    ep, fake = await _make_endpoint(monkeypatch, pending_acks_max=1)
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    msg = FakeMessage(id="m-evict", channel_id="200")
    await msg.add_reaction("👀")
    ch._messages["m-evict"] = msg
    try:
        ep._track_pending_ack("m-evict", "👀", "200")
        assert "👀" in msg.reactions
        # Inserting a second entry evicts m-evict.
        ep._track_pending_ack("m-keep", "👀", "200")
        # Eviction fires a fire-and-forget task; let it run.
        import asyncio

        for _ in range(3):
            await asyncio.sleep(0)
        assert "m-evict" not in ep._pending_acks
        assert "👀" not in msg.reactions
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_pending_acks_ttl_sweep_removes_stale(monkeypatch):
    """Entries older than ttl are evicted by _sweep_pending_acks_once()."""
    ep, fake = await _make_endpoint(monkeypatch, pending_acks_ttl_seconds=10.0)
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    fresh = FakeMessage(id="m-fresh", channel_id="200")
    stale = FakeMessage(id="m-stale", channel_id="200")
    await fresh.add_reaction("👀")
    await stale.add_reaction("👀")
    ch._messages["m-fresh"] = fresh
    ch._messages["m-stale"] = stale
    try:
        # Insert a stale entry by hand (old timestamp).
        ep._pending_acks["m-stale"] = ("👀", "200", time.monotonic() - 999.0)
        ep._track_pending_ack("m-fresh", "👀", "200")  # current time

        evicted = ep._sweep_pending_acks_once()
        assert evicted == 1
        assert "m-stale" not in ep._pending_acks
        assert "m-fresh" in ep._pending_acks

        # Eviction fires a fire-and-forget task; let it run.
        import asyncio

        for _ in range(3):
            await asyncio.sleep(0)
        assert "👀" not in stale.reactions
        assert "👀" in fresh.reactions
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_pending_acks_sweep_breaks_at_first_fresh(monkeypatch):
    """Sweep walks oldest-first and stops once it finds a non-stale entry."""
    ep, fake = await _make_endpoint(monkeypatch, pending_acks_ttl_seconds=10.0)
    try:
        # Build an OrderedDict ordering: stale, stale, fresh, stale-but-out-of-order.
        # Since we walk oldest-first by insertion order and break at first fresh,
        # the trailing stale-out-of-order entry is NOT evicted on this pass.
        now = time.monotonic()
        ep._pending_acks["m-old1"] = ("👀", "200", now - 999.0)
        ep._pending_acks["m-old2"] = ("👀", "200", now - 999.0)
        ep._pending_acks["m-fresh"] = ("👀", "200", now)
        ep._pending_acks["m-also-old"] = ("👀", "200", now - 999.0)

        evicted = ep._sweep_pending_acks_once()
        assert evicted == 2
        assert list(ep._pending_acks.keys()) == ["m-fresh", "m-also-old"]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_pending_acks_sweep_ignores_empty(monkeypatch):
    """Sweep on an empty dict is a no-op."""
    ep, fake = await _make_endpoint(monkeypatch)
    try:
        assert ep._sweep_pending_acks_once() == 0
        assert len(ep._pending_acks) == 0
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_pending_acks_clear_pending_ack_handles_tuple_format(monkeypatch):
    """_clear_pending_ack pops the tuple and removes the reaction."""
    ep, fake = await _make_endpoint(monkeypatch)
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    msg = FakeMessage(id="m1", channel_id="200")
    await msg.add_reaction("👀")
    ch._messages["m1"] = msg
    try:
        ep._track_pending_ack("m1", "👀", "200")
        await ep._clear_pending_ack(ch, "m1")
        assert "m1" not in ep._pending_acks
        assert "👀" not in msg.reactions
    finally:
        await ep.stop()
