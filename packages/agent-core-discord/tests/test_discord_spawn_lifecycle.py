"""Lifecycle tests: typing and evict-ack tasks are cancelled on drain."""

from __future__ import annotations

import asyncio

import pytest

from agent_core_discord.endpoint import DiscordEndpoint
from agent_core_discord.testing.fakes import (
    FakeChannel,
    FakeDiscordClient,
    FakeMessage,
    FakeUser,
)


class _TrackingHandle:
    """Minimal handle stub with real spawn+drain semantics for lifecycle tests."""

    def __init__(self):
        self._tasks: set[asyncio.Task] = set()
        self.failures: list[BaseException] = []

    def spawn(self, coro, *, name=None):
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.failures.append(exc)

    async def _drain_tasks(self) -> None:
        tasks = list(self._tasks)
        for t in tasks:
            if not t.done():
                t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def publish(self, *a, **kw): ...
    async def ack(self, *a, **kw): ...
    async def nack(self, *a, **kw): ...
    def endpoints(self): return []


@pytest.mark.asyncio
async def test_typing_task_cancelled_on_drain(monkeypatch):
    """Typing task spawned by on_message is cancelled when the bus drains."""
    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    handle = _TrackingHandle()
    ep = DiscordEndpoint(
        name="d",
        target="agent",
        token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    ch = FakeChannel(id="100")
    fake.add_channel(ch)
    msg = FakeMessage(id="m1", channel_id="100", content="hello")
    msg.author = FakeUser(id="u1", display_name="user", bot=False)
    msg.guild = type("G", (), {"id": "g1"})()
    msg.channel = ch

    # Fire on_message — publishes to bus and spawns the typing-while-pending task.
    await fake.fire("on_message", msg)

    # At least one task outstanding (the typing-while-pending task).
    assert len(handle._tasks) >= 1

    # Drain — simulates Bus.stop() after endpoint.stop().
    await ep.stop()
    await handle._drain_tasks()

    assert len(handle._tasks) == 0
    assert handle.failures == []  # CancelledError is not a failure


@pytest.mark.asyncio
async def test_evict_ack_task_cancelled_on_drain(monkeypatch):
    """_remote_remove_ack tasks spawned by _track_pending_ack are cancelled on drain."""
    monkeypatch.setenv("X_TOK", "tok")
    fake = FakeDiscordClient()
    handle = _TrackingHandle()
    ep = DiscordEndpoint(
        name="d",
        target="agent",
        token_env="X_TOK",
        pending_acks_max=1,  # Force LRU eviction on second insert.
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    # First insert — no eviction.
    ep._track_pending_ack("m1", "👀", "100")
    assert len(handle._tasks) == 0

    # Second insert — LRU evicts m1 and spawns _remote_remove_ack task.
    ep._track_pending_ack("m2", "👀", "100")
    assert len(handle._tasks) == 1  # one evict-ack task

    # Drain — simulates Bus.stop() after endpoint.stop().
    await ep.stop()
    await handle._drain_tasks()

    assert len(handle._tasks) == 0
    assert handle.failures == []  # CancelledError is not a failure
