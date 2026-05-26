"""Bus-end-to-end tests for SynthesisRequest → Ready / Failed.

These tests wire a real Bus with a VoiceEndpoint + a StubEndpoint
(used as the caller), publish a SynthesisRequest, and assert the
correlated reply envelope arrives in the stub's inbox with the right
shape.

The whole flow runs on a single asyncio event loop; we drive it by
spinning briefly with ``await asyncio.sleep(0)`` /
``asyncio.wait_for`` after publish so the spawn-and-forget background
task in ``VoiceEndpoint.deliver`` has a chance to publish its reply.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from agent_core_voice.endpoint import VoiceEndpoint
from agent_core_voice.envelopes import (
    SynthesisFailedPayload,
    SynthesisReadyPayload,
    SynthesisRequestPayload,
)
from agent_core_voice.protocol import (
    GPUOOMError,
    VoiceInfo,
)
from madrigal.engine import FakeTTSBackend

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, EventPayload
from agent_core.endpoints.stub import StubEndpoint


class _SlowBackend:
    """TTSBackend that sleeps in ``synthesize`` so we can drive the timeout path.

    ``time.sleep`` (not ``asyncio.sleep``) is correct here because
    ``madrigal.generate`` calls the backend synchronously and the
    endpoint wraps it in ``asyncio.to_thread`` — so the sleep happens
    in the worker thread, which is what ``asyncio.wait_for`` cancels
    against (well — actually wait_for cancels the awaitable in the
    event loop; the thread keeps running but the future raises
    ``TimeoutError`` and we never see the late result).
    """

    SAMPLE_RATE_HZ = FakeTTSBackend.SAMPLE_RATE_HZ
    SAMPLE_WIDTH_BYTES = FakeTTSBackend.SAMPLE_WIDTH_BYTES

    def __init__(self, *, delay_s: float = 0.5) -> None:
        self._delay_s = delay_s
        self._prepared: set[str] = set()
        self._inner = FakeTTSBackend()

    def prepare_voice(self, voice_id: str, ref_wav: Path, ref_text: str) -> None:
        self._inner.prepare_voice(voice_id, ref_wav, ref_text)
        self._prepared.add(voice_id)

    def synthesize(self, voice_id: str, text: str, seed: int) -> tuple[bytes, float]:
        import time

        time.sleep(self._delay_s)
        return self._inner.synthesize(voice_id, text, seed)

    def synthesize_batch(
        self, voice_id: str, texts: list[str], seed: int
    ) -> tuple[list[bytes], list[float]]:
        import time

        time.sleep(self._delay_s)
        return self._inner.synthesize_batch(voice_id, texts, seed)


class _OOMBackend:
    """TTSBackend that always raises GPUOOMError — exercises VoiceError mapping."""

    SAMPLE_RATE_HZ = FakeTTSBackend.SAMPLE_RATE_HZ
    SAMPLE_WIDTH_BYTES = FakeTTSBackend.SAMPLE_WIDTH_BYTES

    def __init__(self) -> None:
        self._inner = FakeTTSBackend()

    def prepare_voice(self, voice_id: str, ref_wav: Path, ref_text: str) -> None:
        self._inner.prepare_voice(voice_id, ref_wav, ref_text)

    def synthesize(self, voice_id: str, text: str, seed: int) -> tuple[bytes, float]:
        raise GPUOOMError("simulated OOM")

    def synthesize_batch(
        self, voice_id: str, texts: list[str], seed: int
    ) -> tuple[list[bytes], list[float]]:
        raise GPUOOMError("simulated OOM")


def _build_request_payload(
    *,
    text: str = "hello world",
    timeout_s: float | None = None,
    retain_s: float | None = None,
    options: dict[str, Any] | None = None,
) -> EventPayload:
    """Build a SynthesisRequest EventPayload through the validated model.

    We round-trip through SynthesisRequestPayload so the test exercises
    the same wire shape Pepper would publish.
    """
    req = SynthesisRequestPayload(
        text=text,
        timeout_s=timeout_s,
        retain_s=retain_s,
        options=options,
    )
    return EventPayload(type="SynthesisRequest", data=req.model_dump())


async def _wait_for_reply(stub: StubEndpoint, *, timeout: float = 5.0) -> Envelope:
    """Spin until the stub has at least one envelope, or fail the test."""
    deadline = asyncio.get_event_loop().time() + timeout
    while not stub.inbox:
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(
                f"no reply envelope received within {timeout}s; "
                f"stub inbox is empty"
            )
        await asyncio.sleep(0.01)
    return stub.inbox[-1]


@pytest.fixture
async def wired_bus(
    tmp_path: Path,
    ref_wav: Path,
) -> tuple[Bus, VoiceEndpoint, StubEndpoint]:
    """Bus + VoiceEndpoint(name='voice') + StubEndpoint(name='caller').

    The caller is registered as agent 'caller' with voice_id 'caller_voice'.
    Returns (bus, voice_endpoint, caller_stub).
    """
    backend = FakeTTSBackend()
    voice_ep = VoiceEndpoint.for_test(
        name="voice",
        backend=backend,
        voices={
            "caller_voice": VoiceInfo(
                voice_id="caller_voice",
                ref_wav=ref_wav,
                ref_text="reference",
            ),
        },
        output_dir=tmp_path / "wav_out",
        audit_path=tmp_path / "audit.jsonl",
    )
    voice_ep.register_agent("caller", "caller_voice")
    caller = StubEndpoint(name="caller")

    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    bus.register(EndpointSpec(endpoint=voice_ep))
    bus.register(EndpointSpec(endpoint=caller))
    await bus.start()
    try:
        yield bus, voice_ep, caller
    finally:
        await bus.stop()


@pytest.fixture
async def wired_bus_slow_backend(
    tmp_path: Path,
    ref_wav: Path,
) -> tuple[Bus, VoiceEndpoint, StubEndpoint, _SlowBackend]:
    """Same as wired_bus but with a _SlowBackend for timeout-path tests."""
    backend = _SlowBackend(delay_s=0.5)
    voice_ep = VoiceEndpoint.for_test(
        name="voice",
        backend=backend,
        voices={
            "caller_voice": VoiceInfo(
                voice_id="caller_voice",
                ref_wav=ref_wav,
                ref_text="reference",
            ),
        },
        output_dir=tmp_path / "wav_out",
        audit_path=tmp_path / "audit.jsonl",
    )
    voice_ep.register_agent("caller", "caller_voice")
    caller = StubEndpoint(name="caller")

    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    bus.register(EndpointSpec(endpoint=voice_ep))
    bus.register(EndpointSpec(endpoint=caller))
    await bus.start()
    try:
        yield bus, voice_ep, caller, backend
    finally:
        await bus.stop()


@pytest.fixture
async def wired_bus_unregistered(
    tmp_path: Path,
    ref_wav: Path,
) -> tuple[Bus, VoiceEndpoint, StubEndpoint]:
    """Bus + VoiceEndpoint without register_agent called for 'caller'."""
    voice_ep = VoiceEndpoint.for_test(
        name="voice",
        backend=FakeTTSBackend(),
        voices={
            "some_voice": VoiceInfo(
                voice_id="some_voice",
                ref_wav=ref_wav,
                ref_text="reference",
            ),
        },
        output_dir=tmp_path / "wav_out",
        audit_path=tmp_path / "audit.jsonl",
    )
    # Note: NO register_agent for 'caller' — that's the point of this fixture.
    caller = StubEndpoint(name="caller")

    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    bus.register(EndpointSpec(endpoint=voice_ep))
    bus.register(EndpointSpec(endpoint=caller))
    await bus.start()
    try:
        yield bus, voice_ep, caller
    finally:
        await bus.stop()


async def test_synthesis_request_yields_ready(
    wired_bus: tuple[Bus, VoiceEndpoint, StubEndpoint],
) -> None:
    """Happy path: SynthesisRequest produces SynthesisReady on the caller's inbox."""
    _bus, _voice_ep, caller = wired_bus

    await caller.send(
        to="voice",
        kind="Event",
        payload=_build_request_payload(text="hello world"),
    )

    reply = await _wait_for_reply(caller)
    assert reply.kind == "Event"
    assert reply.payload.type == "SynthesisReady"
    assert reply.from_ == "voice"  # bus-stamped on publish
    assert reply.to == "caller"
    # The reply must be correlated with the request: in_reply_to references
    # the original request envelope id, and correlation_id is preserved.
    assert reply.in_reply_to is not None
    # Parse the data dict through the typed model to confirm shape.
    ready = SynthesisReadyPayload.model_validate(reply.payload.data)
    assert Path(ready.wav_path).exists()
    assert ready.file_size_bytes > 0
    assert ready.duration_s > 0
    assert ready.sample_rate_hz == FakeTTSBackend.SAMPLE_RATE_HZ
    assert ready.cache_hit is False
    assert ready.chunks >= 1
    assert ready.retain_until  # ISO 8601 string


async def test_synthesis_request_timeout_yields_failed(
    wired_bus_slow_backend: tuple[Bus, VoiceEndpoint, StubEndpoint, _SlowBackend],
) -> None:
    """When synthesis exceeds timeout_s, voice emits SynthesisFailed(TIMEOUT)."""
    _bus, _voice_ep, caller, _backend = wired_bus_slow_backend

    # timeout_s is much shorter than the backend's 0.5s sleep.
    await caller.send(
        to="voice",
        kind="Event",
        payload=_build_request_payload(text="this will time out", timeout_s=0.05),
    )

    reply = await _wait_for_reply(caller)
    assert reply.payload.type == "SynthesisFailed"
    failed = SynthesisFailedPayload.model_validate(reply.payload.data)
    assert failed.reason == "TIMEOUT"
    assert failed.retryable is True
    assert "0.05" in failed.message or "timeout" in failed.message.lower()


async def test_unregistered_agent_yields_voice_not_prepared(
    wired_bus_unregistered: tuple[Bus, VoiceEndpoint, StubEndpoint],
) -> None:
    """An unregistered caller gets SynthesisFailed(VOICE_NOT_PREPARED)."""
    _bus, _voice_ep, caller = wired_bus_unregistered

    await caller.send(
        to="voice",
        kind="Event",
        payload=_build_request_payload(text="hi"),
    )

    reply = await _wait_for_reply(caller)
    assert reply.payload.type == "SynthesisFailed"
    failed = SynthesisFailedPayload.model_validate(reply.payload.data)
    assert failed.reason == "VOICE_NOT_PREPARED"
    assert failed.retryable is False
    assert "caller" in failed.message


async def test_gpu_oom_maps_to_failed_gpu_oom(
    tmp_path: Path,
    ref_wav: Path,
) -> None:
    """A GPUOOMError from the backend maps to SynthesisFailed(GPU_OOM, retryable=True)."""
    backend = _OOMBackend()
    voice_ep = VoiceEndpoint.for_test(
        name="voice",
        backend=backend,
        voices={
            "caller_voice": VoiceInfo(
                voice_id="caller_voice",
                ref_wav=ref_wav,
                ref_text="reference",
            ),
        },
        output_dir=tmp_path / "wav_out",
        audit_path=tmp_path / "audit.jsonl",
    )
    voice_ep.register_agent("caller", "caller_voice")
    caller = StubEndpoint(name="caller")
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    bus.register(EndpointSpec(endpoint=voice_ep))
    bus.register(EndpointSpec(endpoint=caller))
    await bus.start()
    try:
        await caller.send(
            to="voice",
            kind="Event",
            payload=_build_request_payload(text="hi"),
        )
        reply = await _wait_for_reply(caller)
    finally:
        await bus.stop()

    assert reply.payload.type == "SynthesisFailed"
    failed = SynthesisFailedPayload.model_validate(reply.payload.data)
    assert failed.reason == "GPU_OOM"
    assert failed.retryable is True


async def test_non_synthesis_event_is_ignored(
    wired_bus: tuple[Bus, VoiceEndpoint, StubEndpoint],
) -> None:
    """An Event envelope of a different type is acked silently — no reply emitted."""
    _bus, _voice_ep, caller = wired_bus

    await caller.send(
        to="voice",
        kind="Event",
        payload=EventPayload(type="SomeUnrelatedEvent", data={"foo": "bar"}),
    )

    # Give the bus a tick to dispatch + the voice endpoint to ack.
    await asyncio.sleep(0.05)

    # No reply should land on the caller's inbox.
    reply_types = {
        env.payload.type if hasattr(env.payload, "type") else None
        for env in caller.inbox
    }
    assert "SynthesisReady" not in reply_types
    assert "SynthesisFailed" not in reply_types


def test_register_agent_rejects_unknown_voice_id(
    tmp_path: Path,
    ref_wav: Path,
) -> None:
    """register_agent must refuse a voice_id that isn't configured."""
    voice_ep = VoiceEndpoint.for_test(
        name="voice",
        backend=FakeTTSBackend(),
        voices={
            "real_voice": VoiceInfo(
                voice_id="real_voice", ref_wav=ref_wav, ref_text="r"
            ),
        },
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    with pytest.raises(ValueError, match="not configured"):
        voice_ep.register_agent("caller", "nonexistent_voice")
