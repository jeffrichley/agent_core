"""VoiceEndpoint — bus endpoint exposing per-agent synthesis.

Implements the standard Endpoint protocol. ``deliver`` accepts
``Event`` envelopes whose ``payload.type == "SynthesisRequest"``,
spawns a background task to run synthesis via ``madrigal.generate``,
and publishes a correlated ``SynthesisReady`` or ``SynthesisFailed``
envelope back to the caller. Non-synthesis envelopes are acked and
ignored.

The endpoint holds the warm TTS backend, the registry of configured
voices, an agent → voice_id mapping (populated by plugin.py at wire
time so callers cannot spoof voice ids on the wire), the output
directory, and the audit log.

Construction wiring:

* Production: bus runner calls ``VoiceEndpoint(name=..., **yaml_params)``
  which constructs ``madrigal.engine.QwenTTSBackend`` internally.
* Tests: ``VoiceEndpoint.for_test(backend=fake, voices=..., ...)`` skips
  the real backend and injects a fake (typically
  ``madrigal.engine.FakeTTSBackend``).

Synthesis routes through ``madrigal.generate()`` with chunked + parallel
batching enabled. The endpoint does not call ``backend.synthesize``
directly; the voice library owns chunking, batching, and wav
concatenation.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import soundfile as sf

from agent_core_voice.audit import AuditEvent, AuditLog
from agent_core_voice.lifecycle import transcode_audio
from agent_core_voice.protocol import (
    EmptyTextError,
    GPUOOMError,
    TextTooLongError,
    TTSBackend,
    VoiceError,
    VoiceInfo,
    VoiceNotPreparedError,
)

if TYPE_CHECKING:
    from agent_core.bus.envelope import Envelope
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SynthesisSuccess:
    """Successful synthesis result."""

    path: str
    duration_s: float
    generation_s: float
    sample_rate: int


@dataclass(frozen=True)
class SynthesisError:
    """Failed synthesis — message is the agent-readable string."""

    message: str


class _WavPhaseError(Exception):
    """Internal marker for failures in the wav decode/write phase.

    Wraps the underlying OSError/RuntimeError so ``_error_message`` can
    distinguish wav-phase failures from arbitrary backend RuntimeErrors.
    """

    def __init__(self, wrapped: BaseException) -> None:
        super().__init__(str(wrapped))
        self.wrapped = wrapped


class VoiceEndpoint:
    """Tool-only bus endpoint backing the voice MCP tool surface."""

    def __init__(
        self,
        *,
        name: str,
        backend: TTSBackend | None = None,
        voices: dict[str, VoiceInfo] | dict[str, dict] | None = None,
        output_dir: Path | str,
        audit_path: Path | str,
        max_text_len: int = 2000,
        # Real-backend params, only used when backend is None:
        model_path: str | None = None,
        device: str = "cuda:0",
        attn_implementation: str = "sdpa",
    ) -> None:
        self._name = name
        self._handle: BusHandle | None = None
        self._max_text_len = max_text_len
        # Plugin.py populates this at wire time; envelope handler uses it to
        # resolve envelope.from_ → voice_id without trusting caller-supplied
        # voice ids on the wire.
        self._agent_to_voice: dict[str, str] = {}

        if backend is None:
            if model_path is None:
                raise ValueError(
                    "VoiceEndpoint requires either backend=... (tests) or "
                    "model_path=... (production with QwenTTSBackend)"
                )
            from madrigal.engine import QwenTTSBackend

            backend = QwenTTSBackend(
                model_path=model_path,
                device=device,
                attn_implementation=attn_implementation,
            )
        self._backend = backend

        # Normalize voices: yaml gives dict[str, dict]; tests give dict[str, VoiceInfo].
        # Expand ~ in all paths so yaml configs can use "~/.agent-core/..." cleanly.
        normalized: dict[str, VoiceInfo] = {}
        for vid, raw in (voices or {}).items():
            if isinstance(raw, VoiceInfo):
                normalized[vid] = raw
            else:
                normalized[vid] = VoiceInfo(
                    voice_id=vid,
                    ref_wav=Path(raw["ref_wav"]).expanduser(),
                    ref_text=raw["ref_text"],
                    blend=raw.get("blend"),
                )
        self._voices = normalized

        self._output_dir = Path(output_dir).expanduser()
        self._audit = AuditLog(Path(audit_path).expanduser())
        self._output_dir.mkdir(parents=True, exist_ok=True)

        for voice_id, info in self._voices.items():
            self._backend.prepare_voice(voice_id, Path(info.ref_wav), info.ref_text)
            log.info("voice %r prepared (ref_wav=%s)", voice_id, info.ref_wav)

    @classmethod
    def for_test(
        cls,
        *,
        backend: TTSBackend,
        voices: dict[str, VoiceInfo],
        output_dir: Path | str,
        audit_path: Path | str,
        name: str = "voice_test",
        max_text_len: int = 2000,
    ) -> VoiceEndpoint:
        """Test seam — same constructor, explicit name default."""
        return cls(
            name=name,
            backend=backend,
            voices=voices,
            output_dir=output_dir,
            audit_path=audit_path,
            max_text_len=max_text_len,
        )

    @property
    def name(self) -> str:
        return self._name

    def voice_ids(self) -> set[str]:
        return set(self._voices.keys())

    def register_agent(self, agent_name: str, voice_id: str) -> None:
        """Bind an agent name → voice_id mapping.

        Plugin.py calls this at wire time (before bus.start). The envelope
        handler uses the mapping to resolve which voice an inbound
        ``envelope.from_`` is allowed to use — voice_id is endpoint-closed
        and never trusted from the wire.
        """
        if voice_id not in self._voices:
            raise ValueError(
                f"voice_id={voice_id!r} not configured; "
                f"available: {sorted(self._voices.keys())}"
            )
        self._agent_to_voice[agent_name] = voice_id

    def voice_info(self, voice_id: str) -> dict[str, Any]:
        info = self._voices[voice_id]
        return {
            "voice_id": info.voice_id,
            "ref_clip": str(info.ref_wav),
            "ref_text": info.ref_text,
            "blend": info.blend,
            "sample_rate": 24000,
            "mode": "1.7B Base + ICL voice clone",
        }

    async def cleanup_tick(self) -> int:
        """Sweep expired WAVs from the output directory. Returns count removed.

        Called periodically (every 5 min by the scheduler endpoint) to enforce
        the file lifecycle policy. Safe to call manually for tests or ops.

        NOTE: Wire this in production by adding a scheduler job to
        ``~/.agent-core/jobs.yaml`` that targets the voice endpoint and calls
        ``cleanup_tick()`` every 5 minutes. Exact yaml shape depends on the
        scheduler endpoint's job format — check
        ``agent_core/endpoints/scheduler.py`` for the current schema.
        """
        from agent_core_voice.lifecycle import cleanup_expired

        return await asyncio.to_thread(cleanup_expired, root=self._output_dir)

    async def synthesize_safe(
        self,
        *,
        agent_name: str,
        voice_id: str,
        text: str,
        seed: int,
    ) -> SynthesisSuccess | SynthesisError:
        """Synthesize via ``madrigal.generate()`` (chunked + parallel-batched).

        Never raises. All failures land as ``SynthesisError(message=...)``.
        """
        # Lazy-import to keep module import cheap and to honor the
        # backend-swap boundary: madrigal is the runtime dep, not a hard
        # import-time dep for tests that only touch protocol/audit code.
        from madrigal import Spec, generate

        now = datetime.now(UTC)

        # Endpoint-level text validation. The chunker would also raise on
        # empty input, but doing the pre-check here keeps the error path
        # uniform (single error class, audited identically) regardless of
        # whether chunking is enabled.
        if not text or not text.strip():
            return await self._record_error(
                now, agent_name, voice_id, text, seed, EmptyTextError("text is empty")
            )
        if len(text) > self._max_text_len:
            return await self._record_error(
                now,
                agent_name,
                voice_id,
                text,
                seed,
                TextTooLongError(
                    f"text length {len(text)} exceeds endpoint budget {self._max_text_len}"
                ),
            )

        try:
            result = await asyncio.to_thread(
                generate,
                text,
                Spec(
                    voice_id=voice_id,
                    seed=seed,
                    chunk_strategy="sentence",
                    parallel=True,
                ),
                backend=self._backend,
            )
        except VoiceError as exc:
            return await self._record_error(now, agent_name, voice_id, text, seed, exc)
        except Exception as exc:  # defensive — unknown backend/library failure
            log.exception("madrigal.generate raised unexpected exception")
            return await self._record_error(now, agent_name, voice_id, text, seed, exc)

        wav_bytes = result.audio
        if wav_bytes is None:
            return await self._record_error(
                now,
                agent_name,
                voice_id,
                text,
                seed,
                _WavPhaseError(RuntimeError("madrigal returned no audio bytes")),
            )

        try:
            duration_s, sample_rate = self._wav_duration(wav_bytes)
            path = self._next_output_path(agent_name, seed, text, now)
            await asyncio.to_thread(path.write_bytes, wav_bytes)
        except (OSError, RuntimeError) as exc:
            return await self._record_error(
                now, agent_name, voice_id, text, seed, _WavPhaseError(exc)
            )

        # generation_s = sum of per-chunk wall times reported by the
        # backend (madrigal exposes these in result.timings). When timings
        # are absent (cache hit, sequential single-call path on some
        # backends) we fall back to 0.0; callers should treat
        # generation_s as a hint, not a guarantee.
        generation_s = float(sum(result.timings)) if result.timings else 0.0

        await self._audit.write(
            AuditEvent(
                timestamp=now,
                agent=agent_name,
                voice_id=voice_id,
                text_len=len(text),
                seed=seed,
                duration_s=duration_s,
                generation_s=generation_s,
                wav_path=str(path),
                error=None,
            )
        )
        return SynthesisSuccess(
            path=str(path),
            duration_s=duration_s,
            generation_s=generation_s,
            sample_rate=sample_rate,
        )

    async def _record_error(
        self,
        now: datetime,
        agent_name: str,
        voice_id: str,
        text: str,
        seed: int,
        exc: BaseException,
    ) -> SynthesisError:
        message = self._error_message(exc)
        await self._audit.write(
            AuditEvent(
                timestamp=now,
                agent=agent_name,
                voice_id=voice_id,
                text_len=len(text),
                seed=seed,
                duration_s=None,
                generation_s=None,
                wav_path=None,
                error=message,
            )
        )
        return SynthesisError(message=message)

    @staticmethod
    def _error_message(exc: BaseException) -> str:
        if isinstance(exc, EmptyTextError):
            return "text is empty"
        if isinstance(exc, TextTooLongError):
            return f"text exceeds model budget ({exc})"
        if isinstance(exc, GPUOOMError):
            return "GPU is out of memory; try again in a moment"
        if isinstance(exc, VoiceNotPreparedError):
            return str(exc)
        if isinstance(exc, _WavPhaseError):
            wrapped = exc.wrapped
            if isinstance(wrapped, OSError):
                return f"output directory is not writable: {wrapped}"
            return f"wav decode/write failed: {wrapped}"
        if isinstance(exc, OSError):
            return f"output directory is not writable: {exc}"
        return f"{type(exc).__name__}: {exc}"

    def _next_output_path(self, agent_name: str, seed: int, text: str, now: datetime) -> Path:
        day = now.strftime("%Y-%m-%d")
        ts = now.strftime("%Y%m%dT%H%M%S_%f")
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        dir_ = self._output_dir / agent_name / day
        dir_.mkdir(parents=True, exist_ok=True)
        return dir_ / f"{ts}-{seed}-{text_hash}.wav"

    @staticmethod
    def _wav_duration(wav_bytes: bytes) -> tuple[float, int]:
        with sf.SoundFile(io.BytesIO(wav_bytes)) as f:
            return f.frames / float(f.samplerate), int(f.samplerate)

    # Endpoint protocol implementation.
    async def start(self, bus: BusHandle) -> None:
        self._handle = bus
        log.info("VoiceEndpoint(name=%s) started; output_dir=%s", self._name, self._output_dir)

    async def deliver(self, envelope: Envelope) -> None:
        """Route SynthesisRequest envelopes to a background task; ack others.

        We return from ``deliver`` promptly (per Endpoint protocol contract)
        and let the synthesis run in an asyncio task. The bus is then free
        to dispatch to other endpoints while the GPU is busy.
        """
        if (
            envelope.kind != "Event"
            or not hasattr(envelope.payload, "type")
            or envelope.payload.type != "SynthesisRequest"
        ):
            log.debug(
                "VoiceEndpoint(name=%s) ignoring envelope %s (kind=%s, payload_type=%s)",
                self._name,
                envelope.id,
                envelope.kind,
                getattr(envelope.payload, "type", None),
            )
            if self._handle is not None:
                await self._handle.ack(envelope.id)
            return

        from agent_core_voice.envelopes import SynthesisRequestPayload

        try:
            req = SynthesisRequestPayload.model_validate(envelope.payload.data)
        except Exception as exc:
            log.warning(
                "VoiceEndpoint(name=%s) rejected malformed SynthesisRequest %s: %s",
                self._name,
                envelope.id,
                exc,
            )
            await self._publish_failed(
                envelope,
                reason="INTERNAL_ERROR",
                message=f"invalid SynthesisRequest payload: {exc}",
                retryable=False,
            )
            if self._handle is not None:
                await self._handle.ack(envelope.id)
            return

        # Ack the request envelope NOW (before spawning the worker) so the
        # bus doesn't treat synthesis-wall-time as an in-flight timeout.
        # The Ready/Failed envelopes we emit later are separate envelopes
        # correlated via in_reply_to=<request.id>, not the same envelope.
        if self._handle is not None:
            await self._handle.ack(envelope.id)

        # Spawn-and-forget — synthesis can take ~60s; the bus must not block.
        self._handle.spawn(
            self._handle_synthesis_request(envelope, req),
            name=f"voice-synthesis-{envelope.id}",
        )

    async def stop(self) -> None:
        self._handle = None
        log.info("VoiceEndpoint(name=%s) stopped", self._name)

    async def _handle_synthesis_request(
        self,
        envelope: Envelope,
        req: Any,  # SynthesisRequestPayload — typed via local import to avoid module-level cycle
    ) -> None:
        """Run synthesis in a thread, publish Ready/Failed, then ack."""
        from madrigal import Spec, generate

        from agent_core_voice.envelopes import SynthesisReadyPayload
        from agent_core_voice.lifecycle import retain_until_iso, write_addressed

        agent_name = envelope.from_
        voice_id = self._agent_to_voice.get(agent_name)
        if voice_id is None:
            await self._publish_failed(
                envelope,
                reason="VOICE_NOT_PREPARED",
                message=(
                    f"no voice configured for agent {agent_name!r}; "
                    f"call VoiceEndpoint.register_agent at wire time"
                ),
                retryable=False,
            )
            return

        timeout_s = req.timeout_s if req.timeout_s is not None else 300.0
        retain_s = req.retain_s if req.retain_s is not None else 3600.0
        options = req.options or {}
        spec = Spec(
            voice_id=voice_id,
            seed=int(options.get("seed", 42)),
            chunk_strategy=options.get("chunk_strategy", "sentence"),
            parallel=bool(options.get("parallel", True)),
        )

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(generate, req.text, spec, backend=self._backend),
                timeout=timeout_s,
            )
        except TimeoutError:
            await self._publish_failed(
                envelope,
                reason="TIMEOUT",
                message=f"synthesis exceeded timeout_s={timeout_s}",
                retryable=True,
            )
            return
        except VoiceError as exc:
            await self._publish_failed_from_voiceerror(envelope, exc)
            return
        except Exception as exc:
            log.exception("madrigal.generate raised unexpected exception")
            await self._publish_failed(
                envelope,
                reason="INTERNAL_ERROR",
                message=f"{type(exc).__name__}: {exc}",
                retryable=False,
            )
            return

        wav_bytes = result.audio
        if wav_bytes is None:
            await self._publish_failed(
                envelope,
                reason="INTERNAL_ERROR",
                message="madrigal.generate returned no audio bytes",
                retryable=False,
            )
            return

        audio_format = getattr(req, "format", "wav") or "wav"

        try:
            # Measure duration on the original WAV bytes (soundfile reads WAV reliably).
            duration_s, _sr_from_wav = self._wav_duration(wav_bytes)
            # Transcode to the requested format (no-op for wav).
            audio_bytes = await asyncio.to_thread(
                transcode_audio, wav_bytes, target_format=audio_format
            )
            wav_path, _sha = await asyncio.to_thread(
                write_addressed,
                audio_bytes,
                root=self._output_dir,
                retain_s=retain_s,
                ext=audio_format,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            await self._publish_failed(
                envelope,
                reason="INTERNAL_ERROR",
                message=f"audio write/transcode failed: {exc}",
                retryable=False,
            )
            return

        # Derive metadata from the madrigal Result. Timings list gives us
        # per-chunk wall times — sum for elapsed_s, count for chunks.
        timings = result.timings or []
        elapsed_s = float(sum(timings))
        sample_rate = int(result.sample_rate_hz)
        chunks = max(1, len(timings))  # at least one chunk even on cache hit

        ready_payload = SynthesisReadyPayload(
            wav_path=str(wav_path),
            file_size_bytes=wav_path.stat().st_size,
            duration_s=duration_s,
            elapsed_s=elapsed_s,
            sample_rate_hz=sample_rate,
            cache_hit=bool(result.cache_hit),
            chunks=chunks,
            retain_until=retain_until_iso(retain_s=retain_s),
        )

        import uuid as _uuid

        from agent_core.bus.envelope import Envelope as BusEnvelope
        from agent_core.bus.envelope import EventPayload

        ready = BusEnvelope(
            id=_uuid.uuid4().hex,
            correlation_id=envelope.correlation_id,
            in_reply_to=envelope.id,
            to=envelope.from_,
            kind="Event",
            payload=EventPayload(
                type="SynthesisReady",
                data=ready_payload.model_dump(),
            ),
            created_at=datetime.now(UTC),
        )

        assert self._handle is not None
        await self._handle.publish(ready)

    async def _publish_failed(
        self,
        request_env: Envelope,
        *,
        reason: str,
        message: str,
        retryable: bool,
    ) -> None:
        """Build + publish a SynthesisFailed envelope correlated with the request."""
        from agent_core.bus.envelope import Envelope as BusEnvelope
        from agent_core.bus.envelope import EventPayload
        from agent_core_voice.envelopes import SynthesisFailedPayload

        if self._handle is None:
            log.error(
                "VoiceEndpoint(name=%s) cannot publish failure (handle unset) "
                "for request %s: %s",
                self._name,
                request_env.id,
                message,
            )
            return

        import uuid as _uuid

        failed = BusEnvelope(
            id=_uuid.uuid4().hex,
            correlation_id=request_env.correlation_id,
            in_reply_to=request_env.id,
            to=request_env.from_,
            kind="Event",
            payload=EventPayload(
                type="SynthesisFailed",
                data=SynthesisFailedPayload(
                    reason=reason,  # type: ignore[arg-type]
                    message=message,
                    retryable=retryable,
                ).model_dump(),
            ),
            created_at=datetime.now(UTC),
        )
        await self._handle.publish(failed)

    async def _publish_failed_from_voiceerror(
        self,
        request_env: Envelope,
        exc: VoiceError,
    ) -> None:
        """Map a VoiceError subclass onto the failure-reason taxonomy."""
        reason = "INTERNAL_ERROR"
        retryable = False
        if isinstance(exc, GPUOOMError):
            reason, retryable = "GPU_OOM", True
        elif isinstance(exc, TextTooLongError):
            reason = "TEXT_TOO_LONG"
        elif isinstance(exc, VoiceNotPreparedError):
            reason = "VOICE_NOT_PREPARED"
        await self._publish_failed(
            request_env,
            reason=reason,
            message=str(exc),
            retryable=retryable,
        )


__all__ = [
    "SynthesisError",
    "SynthesisSuccess",
    "VoiceEndpoint",
]
