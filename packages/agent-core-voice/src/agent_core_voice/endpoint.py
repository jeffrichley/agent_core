"""VoiceEndpoint — bus endpoint exposing per-agent synthesis via MCP.

Implements the standard Endpoint protocol but ``deliver`` is a no-op:
voice is tool-only — no inbox, no agent-to-agent envelopes. The endpoint
holds the warm TTS backend, the registry of configured voices, the
output directory, and the audit log.

Construction wiring:

* Production: bus runner calls ``VoiceEndpoint(name=..., **yaml_params)``
  which constructs ``QwenTTSBackend`` internally (added in Task 11).
* Tests: ``VoiceEndpoint.for_test(backend=fake, voices=..., ...)`` skips
  the real backend and injects a fake.
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
from agent_core_voice.protocol import TTSBackend, VoiceInfo

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


@dataclass(frozen=True)
class SynthesisError:
    """Failed synthesis — message is the agent-readable string."""

    message: str


class VoiceEndpoint:
    """Tool-only bus endpoint backing the voice MCP tool surface."""

    def __init__(
        self,
        *,
        name: str,
        backend: TTSBackend,
        voices: dict[str, VoiceInfo],
        output_dir: Path | str,
        audit_path: Path | str,
    ) -> None:
        self._name = name
        self._backend = backend
        self._voices: dict[str, VoiceInfo] = dict(voices)
        self._output_dir = Path(output_dir)
        self._audit = AuditLog(Path(audit_path))
        self._handle: BusHandle | None = None

        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Pre-build ICL prompts for every configured voice. After this returns,
        # every agent is warm from call 1.
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
    ) -> "VoiceEndpoint":
        """Test seam — same constructor, explicit name default."""
        return cls(
            name=name,
            backend=backend,
            voices=voices,
            output_dir=output_dir,
            audit_path=audit_path,
        )

    @property
    def name(self) -> str:
        return self._name

    def voice_ids(self) -> set[str]:
        return set(self._voices.keys())

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

    async def synthesize_safe(
        self,
        *,
        agent_name: str,
        voice_id: str,
        text: str,
        seed: int,
    ) -> SynthesisSuccess | SynthesisError:
        """Synthesize text in ``voice_id``, write the wav, append audit, return envelope.

        Never raises. All failures land as ``SynthesisError(message=...)``.
        (Error mapping is added in Task 7; this method body currently assumes the
        happy path.)
        """
        now = datetime.now(UTC)
        wav_bytes, generation_s = await asyncio.to_thread(
            self._backend.synthesize, voice_id, text, seed
        )
        duration_s, _ = self._wav_duration(wav_bytes)
        path = self._next_output_path(agent_name, seed, text, now)
        await asyncio.to_thread(path.write_bytes, wav_bytes)

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
        )

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

    # Endpoint protocol stubs (voice is tool-only — no envelope traffic).
    async def start(self, bus: BusHandle) -> None:
        self._handle = bus
        log.info("VoiceEndpoint(name=%s) started; output_dir=%s", self._name, self._output_dir)

    async def deliver(self, envelope: Envelope) -> None:
        # Voice is tool-only; envelopes addressed to us are unexpected.
        # Log at debug, then ack so the bus doesn't redeliver or dead-letter.
        log.debug(
            "VoiceEndpoint(name=%s) ignoring delivered envelope %s", self._name, envelope.id
        )
        if self._handle is not None:
            await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        self._handle = None
        log.info("VoiceEndpoint(name=%s) stopped", self._name)


__all__ = [
    "SynthesisError",
    "SynthesisSuccess",
    "VoiceEndpoint",
]
