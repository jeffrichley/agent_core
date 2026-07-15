"""Lifecycle test: synthesis task spawned by deliver() is cancelled on drain."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core.bus.envelope import Envelope, EventPayload
from agent_core_voice.endpoint import VoiceEndpoint
from agent_core_voice.protocol import VoiceInfo


class _StubBackend:
    """Backend stub — synthesis is bypassed via monkeypatch."""

    SAMPLE_RATE_HZ = 24000
    SAMPLE_WIDTH_BYTES = 2

    def prepare_voice(self, voice_id, ref_wav, ref_text): ...
    def synthesize(self, voice_id, text, seed): ...
    def synthesize_batch(self, voice_id, texts, seed): return [], []


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
async def test_synthesis_task_cancelled_on_drain(monkeypatch, tmp_path, ref_wav):
    """deliver() spawns a synthesis task; it is cancelled when the bus drains."""
    from agent_core_voice.envelopes import SynthesisRequestPayload

    # Monkeypatch _handle_synthesis_request to a pure-asyncio stub that hangs
    # until cancelled, eliminating asyncio.to_thread and thread-pool involvement.
    async def _hanging_synthesis(self_ep, envelope, req):  # noqa: ARG001
        await asyncio.sleep(1000)

    monkeypatch.setattr(VoiceEndpoint, "_handle_synthesis_request", _hanging_synthesis)

    ep = VoiceEndpoint.for_test(
        name="voice",
        backend=_StubBackend(),
        voices={"alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    ep.register_agent("alice", "alice")
    handle = _TrackingHandle()
    await ep.start(handle)

    req_payload = SynthesisRequestPayload(text="hello")
    env = Envelope(
        id="e1",
        correlation_id="c1",
        from_="alice",
        to="voice",
        kind="Event",
        payload=EventPayload(type="SynthesisRequest", data=req_payload.model_dump()),
        created_at=datetime.now(UTC),
    )

    # deliver() acks immediately, then spawns the synthesis task.
    await ep.deliver(env)

    # One synthesis task is outstanding.
    assert len(handle._tasks) == 1

    # Drain — simulates Bus.stop() after endpoint.stop().
    await ep.stop()
    await handle._drain_tasks()

    assert len(handle._tasks) == 0
    assert handle.failures == []  # CancelledError is not a failure
