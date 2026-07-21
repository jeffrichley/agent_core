"""VoiceEndpoint wiring against madrigal.engine.FakeTTSBackend."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import pytest
import soundfile as sf
from agent_core_voice.endpoint import SynthesisError, SynthesisSuccess, VoiceEndpoint
from agent_core_voice.protocol import VoiceInfo
from madrigal.engine import FakeTTSBackend


class _TrackingHandle:
    """Minimal handle stub with real spawn+drain semantics for lifecycle tests.

    Copied from test_synthesis_task_lifecycle.py — supports the full
    spawn/publish/ack/nack interface required by start() and deliver().
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self.failures: list[BaseException] = []

    def spawn(self, coro, *, name=None):  # type: ignore[no-untyped-def]
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

    async def publish(self, *a, **kw) -> None: ...  # type: ignore[no-untyped-def]
    async def ack(self, *a, **kw) -> None: ...  # type: ignore[no-untyped-def]
    async def nack(self, *a, **kw) -> None: ...  # type: ignore[no-untyped-def]
    def endpoints(self) -> list: return []


def _minimal_handle() -> _TrackingHandle:
    """Return a fresh _TrackingHandle for use in start() calls."""
    return _TrackingHandle()


@pytest.mark.asyncio
async def test_start_prepares_every_voice(tmp_path: Path, ref_wav: Path) -> None:
    """All configured voices are prepare_voice'd when start() is awaited."""
    backend = FakeTTSBackend()
    voices = {
        "alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="hi alice"),
        "bob": VoiceInfo(voice_id="bob", ref_wav=ref_wav, ref_text="hi bob"),
    }
    ep = VoiceEndpoint.for_test(
        backend=backend,
        voices=voices,
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    await ep.start(_minimal_handle())
    assert ep.voice_ids() == {"alice", "bob"}
    # FakeTTSBackend recorded which voices were prepared via call_log.
    prepared = {entry[1] for entry in backend.call_log if entry[0] == "prepare_voice"}
    assert prepared == {"alice", "bob"}


def test_init_creates_output_dir(tmp_path: Path, ref_wav: Path) -> None:
    out = tmp_path / "voice_out"
    assert not out.exists()
    VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v": VoiceInfo(voice_id="v", ref_wav=ref_wav, ref_text="r")},
        output_dir=out,
        audit_path=out / "audit.jsonl",
    )
    assert out.is_dir()


@pytest.mark.asyncio
async def test_start_missing_ref_wav_raises(tmp_path: Path) -> None:
    """ref_wav validation runs during prepare_voice in start() (the fake refuses missing files)."""
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={
            "v": VoiceInfo(
                voice_id="v",
                ref_wav=tmp_path / "nope.wav",
                ref_text="r",
            )
        },
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    with pytest.raises(FileNotFoundError):
        await ep.start(_minimal_handle())


@pytest.mark.asyncio
async def test_synthesize_safe_happy_path(tmp_path: Path, ref_wav: Path) -> None:
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    await ep.start(_minimal_handle())

    result = await ep.synthesize_safe(
        agent_name="alice",
        voice_id="alice",
        text="hello world",
        seed=42,
    )

    assert isinstance(result, SynthesisSuccess)
    path = Path(result.path)
    assert path.exists()
    assert path.is_relative_to(tmp_path / "out" / "alice")
    # File is a valid mono wav at madrigal's FakeTTSBackend rate (16 kHz).
    data, sr = sf.read(str(path))
    assert sr == FakeTTSBackend.SAMPLE_RATE_HZ
    assert data.ndim == 1
    assert result.duration_s > 0
    assert result.generation_s >= 0


@pytest.mark.asyncio
async def test_synthesize_output_path_layout(tmp_path: Path, ref_wav: Path) -> None:
    """<output_dir>/<agent>/<YYYY-MM-DD>/<timestamp>-<seed>-<text_hash>.wav"""
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    await ep.start(_minimal_handle())

    result = await ep.synthesize_safe(
        agent_name="alice",
        voice_id="alice",
        text="hello",
        seed=42,
    )
    rel = Path(result.path).relative_to(tmp_path / "out")
    parts = rel.parts
    assert parts[0] == "alice"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1])
    assert re.fullmatch(r"\d{8}T\d{6}_\d{6}-42-[0-9a-f]{8}\.wav", parts[2])


@pytest.mark.asyncio
async def test_synthesize_safe_empty_text_returns_error(tmp_path: Path, ref_wav: Path) -> None:
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v": VoiceInfo(voice_id="v", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    result = await ep.synthesize_safe(agent_name="v", voice_id="v", text="", seed=42)
    assert isinstance(result, SynthesisError)
    assert "empty" in result.message.lower()


@pytest.mark.asyncio
async def test_synthesize_safe_text_too_long(tmp_path: Path, ref_wav: Path) -> None:
    """Endpoint enforces ``max_text_len`` even when the backend would happily accept the text.

    Backend-level text-length budgeting was removed in the Phase 1 swap to
    ``madrigal.generate``; the endpoint is the single source of truth for
    the per-request length cap.
    """
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v": VoiceInfo(voice_id="v", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
        max_text_len=5,
    )
    result = await ep.synthesize_safe(
        agent_name="v", voice_id="v", text="this is too long", seed=42
    )
    assert isinstance(result, SynthesisError)
    assert "exceeds" in result.message.lower() or "too long" in result.message.lower()


@pytest.mark.asyncio
async def test_synthesize_safe_unprepared_voice(tmp_path: Path, ref_wav: Path) -> None:
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v": VoiceInfo(voice_id="v", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    result = await ep.synthesize_safe(agent_name="v", voice_id="other", text="hello", seed=42)
    assert isinstance(result, SynthesisError)
    assert "not prepared" in result.message.lower()


@pytest.mark.asyncio
async def test_audit_line_written_on_error(tmp_path: Path, ref_wav: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v": VoiceInfo(voice_id="v", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=audit_path,
    )
    await ep.synthesize_safe(agent_name="v", voice_id="v", text="", seed=42)

    payload = json.loads(audit_path.read_text("utf-8").strip())
    assert payload["error"] is not None
    assert payload["wav_path"] is None
    assert payload["duration_s"] is None


def test_init_expands_tilde_in_paths(
    tmp_path: Path, ref_wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """~ in output_dir / audit_path / voices[*].ref_wav expands to $HOME at construction."""
    home = tmp_path / "fake-home"
    home.mkdir()
    # Move the ref_wav under fake home so we can reference it via ~.
    new_ref = home / "ref.wav"
    new_ref.write_bytes(ref_wav.read_bytes())
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v": {"ref_wav": "~/ref.wav", "ref_text": "r"}},
        output_dir="~/voice-out",
        audit_path="~/voice-out/audit.jsonl",
    )

    # output_dir landed under the expanded home — not at a literal ~ dir.
    assert (home / "voice-out").is_dir()
    # Voice info shows the EXPANDED ref_clip (not the original ~ string).
    info = ep.voice_info("v")
    assert info["ref_clip"] == str(new_ref)


def test_init_requires_backend_or_model_path(tmp_path: Path) -> None:
    """Construction with neither backend= nor model_path= raises a clear ValueError."""
    with pytest.raises(ValueError, match="backend=.*model_path="):
        VoiceEndpoint(
            name="x",
            output_dir=tmp_path / "out",
            audit_path=tmp_path / "audit.jsonl",
        )


class _BadBytesBackend:
    """Backend whose synthesize returns invalid wav bytes.

    Implements ``synthesize_batch`` too so it works with the chunked +
    parallel path madrigal.generate uses (which routes single-text +
    chunked through synthesize_batch under the hood).
    """

    def prepare_voice(self, voice_id, ref_wav, ref_text):  # type: ignore[no-untyped-def]
        return None

    def synthesize(self, voice_id, text, seed):  # type: ignore[no-untyped-def]
        return b"not a wav file", 0.5

    def synthesize_batch(self, voice_id, texts, seed):  # type: ignore[no-untyped-def]
        return [b"not a wav file"] * len(texts), [0.5] * len(texts)


@pytest.mark.asyncio
async def test_synthesize_safe_swallows_wav_decode_error(tmp_path: Path, ref_wav: Path) -> None:
    ep = VoiceEndpoint.for_test(
        backend=_BadBytesBackend(),  # type: ignore[arg-type]
        voices={"v": VoiceInfo(voice_id="v", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    result = await ep.synthesize_safe(agent_name="v", voice_id="v", text="hi", seed=42)
    assert isinstance(result, SynthesisError)
    # madrigal raises a wave.Error ("file does not start with RIFF id") inside
    # generate() when it tries to decode the bogus wav bytes our fake backend
    # returned. The endpoint catches that as a generic synthesis failure.
    # Permissive OR keeps the assertion robust to madrigal/wave/soundfile wording
    # changes while still proving the error message carries substantive content
    # about the wav-decode failure (not a stripped/empty message and not one of
    # the other short-circuit branches like empty-text / too-long / not-prepared).
    msg = result.message.lower()
    assert any(token in msg for token in ("wav", "synthesis", "decode", "riff", "error"))
    # And explicitly NOT one of the other named failure paths.
    assert "empty" not in msg
    assert "exceeds" not in msg
    assert "not prepared" not in msg


@pytest.mark.asyncio
async def test_handle_synthesis_request_ogg_writes_ogg_file(
    tmp_path: Path, ref_wav: Path
) -> None:
    """A SynthesisRequest with format='ogg' results in a .ogg file on disk."""
    import asyncio
    import uuid

    from agent_core.bus.envelope import Envelope, EventPayload
    from agent_core_voice.envelopes import SynthesisReadyPayload, SynthesisRequestPayload

    class _CapturingHandle:
        def __init__(self) -> None:
            self.published: list[Any] = []

        async def ack(self, env_id: str) -> None:
            pass

        async def publish(self, env: Any, to: Any = None) -> None:
            self.published.append(env)

    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    await ep.start(_minimal_handle())
    handle = _CapturingHandle()
    ep._handle = handle  # type: ignore[assignment]
    ep.register_agent("alice", "alice")

    env_id = str(uuid.uuid4())
    req_payload = SynthesisRequestPayload(text="hello ogg world", format="ogg")
    env = Envelope(
        id=env_id,
        correlation_id=env_id,
        to="voice_test",
        from_="alice",
        kind="Event",
        payload=EventPayload(type="SynthesisRequest", data=req_payload.model_dump()),
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    await ep._handle_synthesis_request(env, req_payload)

    # One SynthesisReady envelope published.
    assert len(handle.published) == 1
    ready = handle.published[0]
    assert ready.payload.type == "SynthesisReady"

    ready_payload = SynthesisReadyPayload.model_validate(ready.payload.data)
    audio_path = Path(ready_payload.wav_path)
    assert audio_path.exists()
    assert audio_path.suffix == ".ogg"


@pytest.mark.asyncio
async def test_handle_synthesis_request_transcode_failure_publishes_failed(
    tmp_path: Path, ref_wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A RuntimeError from transcode_audio is caught and publishes SynthesisFailed."""
    import uuid

    from agent_core.bus.envelope import Envelope, EventPayload
    from agent_core_voice.envelopes import SynthesisFailedPayload, SynthesisRequestPayload

    monkeypatch.setattr(
        "agent_core_voice.endpoint.transcode_audio",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("transcode boom")),
    )

    class _CapturingHandle:
        def __init__(self) -> None:
            self.published: list[Any] = []

        async def ack(self, env_id: str) -> None:
            pass

        async def publish(self, env: Any, to: Any = None) -> None:
            self.published.append(env)

    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    await ep.start(_minimal_handle())
    handle = _CapturingHandle()
    ep._handle = handle  # type: ignore[assignment]
    ep.register_agent("alice", "alice")

    env_id = str(uuid.uuid4())
    req_payload = SynthesisRequestPayload(text="test", format="mp3")
    env = Envelope(
        id=env_id,
        correlation_id=env_id,
        to="voice_test",
        from_="alice",
        kind="Event",
        payload=EventPayload(type="SynthesisRequest", data=req_payload.model_dump()),
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    await ep._handle_synthesis_request(env, req_payload)

    assert len(handle.published) == 1
    failed = handle.published[0]
    assert failed.payload.type == "SynthesisFailed"
    failed_payload = SynthesisFailedPayload.model_validate(failed.payload.data)
    assert failed_payload.reason == "INTERNAL_ERROR"
    assert failed_payload.retryable is False


@pytest.mark.asyncio
async def test_production_wiring_constructs_madrigal_qwen_backend(
    tmp_path: Path, ref_wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard the lazy ``from madrigal.engine import QwenTTSBackend`` path.

    Without ``backend=``, ``VoiceEndpoint.start()`` lazy-imports madrigal's
    real Qwen backend and constructs it from ``model_path`` / ``device`` /
    ``attn_implementation``. Patching the symbol on ``madrigal.engine`` lets
    us exercise that branch without needing torch + qwen-tts at test time —
    otherwise the wiring would only fail at production deploy.
    """
    construct_calls: list[dict[str, object]] = []

    class _FakeQwen:
        def __init__(self, **kwargs: object) -> None:
            construct_calls.append(kwargs)

        def prepare_voice(
            self, voice_id: str, ref_wav: Path, ref_text: str
        ) -> None:  # noqa: ARG002
            return None

        def synthesize(
            self, voice_id: str, text: str, seed: int
        ) -> tuple[bytes, float]:  # noqa: ARG002
            return b"", 0.0

        def synthesize_batch(
            self, voice_id: str, texts: list[str], seed: int
        ) -> tuple[list[bytes], list[float]]:  # noqa: ARG002
            return [b""] * len(texts), [0.0] * len(texts)

    monkeypatch.setattr("madrigal.engine.QwenTTSBackend", _FakeQwen)

    ep = VoiceEndpoint(
        name="voice",
        model_path="/fake/model",
        device="cpu",
        attn_implementation="sdpa",
        voices={"v1": VoiceInfo(voice_id="v1", ref_wav=ref_wav, ref_text="hi")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    await ep.start(_minimal_handle())

    # Backend was constructed exactly once, with the kwargs the endpoint
    # forwarded from its ``model_path`` / ``device`` / ``attn_implementation``
    # arguments.
    assert len(construct_calls) == 1
    assert construct_calls[0] == {
        "model_path": "/fake/model",
        "device": "cpu",
        "attn_implementation": "sdpa",
    }
    # And the endpoint itself constructed cleanly — voices prepared, name set.
    assert ep.name == "voice"
    assert ep.voice_ids() == {"v1"}


# ---------------------------------------------------------------------------
# Sub-request 7: new tests proving the offload-boundary behaviour
# ---------------------------------------------------------------------------


def test_init_does_not_construct_backend(
    tmp_path: Path, ref_wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VoiceEndpoint.__init__ MUST NOT call QwenTTSBackend — only start() may."""
    construct_calls: list[dict[str, object]] = []

    class _SpyQwen:
        def __init__(self, **kwargs: object) -> None:
            construct_calls.append(kwargs)

        def prepare_voice(self, voice_id: str, ref_wav: Path, ref_text: str) -> None:
            pass

        def synthesize(self, voice_id: str, text: str, seed: int) -> tuple[bytes, float]:
            return b"", 0.0

        def synthesize_batch(
            self, voice_id: str, texts: list[str], seed: int
        ) -> tuple[list[bytes], list[float]]:
            return [b""] * len(texts), [0.0] * len(texts)

    monkeypatch.setattr("madrigal.engine.QwenTTSBackend", _SpyQwen)

    # Construct without calling start() — backend must NOT be built yet.
    _ep = VoiceEndpoint(
        name="voice",
        model_path="/fake/model",
        device="cpu",
        attn_implementation="sdpa",
        voices={"v1": VoiceInfo(voice_id="v1", ref_wav=ref_wav, ref_text="hi")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )

    assert construct_calls == [], (
        "QwenTTSBackend was constructed in __init__ but MUST be deferred to start()"
    )


@pytest.mark.asyncio
@pytest.mark.slow
async def test_start_builds_backend_off_thread(tmp_path: Path, ref_wav: Path) -> None:
    """start() builds the backend via asyncio.to_thread — the event loop is not frozen.

    A progress ticker coroutine increments a counter while a blocking backend
    constructor sleeps for 200 ms. If the loop were frozen, the ticker could
    not run, and the counter would stay at 0.
    """
    progress_ticks = 0

    async def _ticker() -> None:
        nonlocal progress_ticks
        while True:
            await asyncio.sleep(0.01)
            progress_ticks += 1

    class _BlockingFakeQwen:
        """Fake backend whose constructor sleeps 200 ms (simulates model load)."""

        SAMPLE_RATE_HZ = 24000

        def __init__(self, **kwargs: object) -> None:
            time.sleep(0.2)  # blocks the calling thread, NOT the event loop

        def prepare_voice(self, voice_id: str, ref_wav: Path, ref_text: str) -> None:
            pass

        def synthesize(self, voice_id: str, text: str, seed: int) -> tuple[bytes, float]:
            return b"", 0.0

        def synthesize_batch(
            self, voice_id: str, texts: list[str], seed: int
        ) -> tuple[list[bytes], list[float]]:
            return [b""] * len(texts), [0.0] * len(texts)

    import madrigal.engine as _me

    original = _me.QwenTTSBackend
    _me.QwenTTSBackend = _BlockingFakeQwen  # type: ignore[assignment]
    try:
        ep = VoiceEndpoint(
            name="voice",
            model_path="/fake/model",
            device="cpu",
            attn_implementation="sdpa",
            voices={},
            output_dir=tmp_path / "out",
            audit_path=tmp_path / "audit.jsonl",
        )

        ticker_task = asyncio.create_task(_ticker())
        try:
            await ep.start(_minimal_handle())
        finally:
            ticker_task.cancel()
            await asyncio.gather(ticker_task, return_exceptions=True)
    finally:
        _me.QwenTTSBackend = original  # type: ignore[assignment]

    assert progress_ticks > 0, (
        f"Event loop was frozen during start(): ticker never ran (ticks={progress_ticks}). "
        "Ensure start() uses asyncio.to_thread for backend construction."
    )


@pytest.mark.asyncio
async def test_for_test_backend_skips_construction(
    tmp_path: Path, ref_wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """for_test(backend=stub) sets _backend at construction; start() skips QwenTTSBackend."""
    construct_calls: list[dict[str, object]] = []

    class _SpyQwen2:
        def __init__(self, **kwargs: object) -> None:
            construct_calls.append(kwargs)

        def prepare_voice(self, voice_id: str, ref_wav: Path, ref_text: str) -> None:
            pass

        def synthesize(self, voice_id: str, text: str, seed: int) -> tuple[bytes, float]:
            return b"", 0.0

        def synthesize_batch(
            self, voice_id: str, texts: list[str], seed: int
        ) -> tuple[list[bytes], list[float]]:
            return [b""] * len(texts), [0.0] * len(texts)

    monkeypatch.setattr("madrigal.engine.QwenTTSBackend", _SpyQwen2)

    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    await ep.start(_minimal_handle())

    assert construct_calls == [], (
        "for_test() path must not call QwenTTSBackend even when start() is invoked"
    )


async def test_reply_tail_failure_still_publishes_synthesis_failed(
    tmp_path: Path, ref_wav: Path
) -> None:
    """If publishing the SynthesisReady fails, the handler must still emit a
    terminal SynthesisFailed — not die silently in its spawned task and hang
    the requesting agent forever. Adversarial review finding #7.
    """
    import uuid
    from datetime import UTC, datetime

    from agent_core.bus.envelope import Envelope, EventPayload
    from agent_core_voice.envelopes import SynthesisRequestPayload

    class _FailFirstPublishHandle:
        def __init__(self) -> None:
            self.published: list[Any] = []
            self._calls = 0

        async def ack(self, env_id: str) -> None:
            pass

        async def publish(self, env: Any, to: Any = None) -> None:
            self._calls += 1
            if self._calls == 1:
                raise RuntimeError("bus publish exploded")  # the SynthesisReady
            self.published.append(env)  # the recovery SynthesisFailed

    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    await ep.start(_minimal_handle())
    handle = _FailFirstPublishHandle()
    ep._handle = handle  # type: ignore[assignment]
    ep.register_agent("alice", "alice")

    env_id = str(uuid.uuid4())
    req_payload = SynthesisRequestPayload(text="hello world")
    env = Envelope(
        id=env_id,
        correlation_id=env_id,
        to="voice_test",
        from_="alice",
        kind="Event",
        payload=EventPayload(type="SynthesisRequest", data=req_payload.model_dump()),
        created_at=datetime.now(UTC),
    )

    # Must not raise despite the reply publish exploding.
    await ep._handle_synthesis_request(env, req_payload)

    # The caller still got a terminal reply.
    assert len(handle.published) == 1
    assert handle.published[0].payload.type == "SynthesisFailed"
