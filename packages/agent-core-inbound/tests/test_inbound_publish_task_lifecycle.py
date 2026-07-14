"""Lifecycle test: _bus_publish_adapter spawned task is cancelled on drain."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from agent_core_inbound.endpoint import InboundEndpoint


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

    async def publish(self, *a, **kw):
        await asyncio.sleep(1000)  # hangs so task can be cancelled

    async def ack(self, *a, **kw): ...
    async def nack(self, *a, **kw): ...
    def endpoints(self): return []


def _make_ep(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_SECRET", "x")
    return InboundEndpoint(
        name="inbound",
        target_being="wren",
        listen_host="127.0.0.1",
        listen_port=18765,
        webhook_secret_env="TEST_SECRET",
        github_allowance_path=str(tmp_path / "g.toml"),
        audit_log_path=str(tmp_path / "audit.jsonl"),
        rate_limit_per_minute=30,
    )


@pytest.mark.asyncio
async def test_bus_publish_adapter_task_cancelled_on_drain(monkeypatch, tmp_path):
    """Task spawned by _bus_publish_adapter is cancelled when the bus drains."""
    ep = _make_ep(monkeypatch, tmp_path)
    handle = _TrackingHandle()
    ep._handle = handle  # inject directly, bypassing uvicorn start

    ep._bus_publish_adapter(
        to="wren",
        kind="Notification",
        payload={
            "kind": "Notification",
            "source": "github",
            "reason": "test",
            "body": {},
            "landed_at": datetime.now(UTC).isoformat(),
        },
        urgency="red",
    )

    # yield so the task registers on the event loop
    await asyncio.sleep(0)
    assert len(handle._tasks) == 1

    # Drain — simulates what Bus.stop() does after endpoint.stop().
    await handle._drain_tasks()

    assert len(handle._tasks) == 0
    assert handle.failures == []  # CancelledError is not a failure
