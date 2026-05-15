"""TTSBackend protocol + error taxonomy.

The ``TTSBackend`` Protocol is the seam that lets ``VoiceEndpoint`` work
against either ``QwenTTSBackend`` (real) or ``FakeTTSBackend`` (tests).
All failure modes the endpoint maps to agent-readable error messages
descend from ``VoiceError`` so the endpoint's exception handling stays
simple.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


class VoiceError(Exception):
    """Base for every error a TTSBackend may raise."""


class EmptyTextError(VoiceError):
    """Caller passed an empty or whitespace-only text."""


class TextTooLongError(VoiceError):
    """Text exceeds the model's token budget."""


class GPUOOMError(VoiceError):
    """The GPU ran out of memory during synthesis. Usually retryable."""


class VoiceNotPreparedError(VoiceError):
    """synthesize() called for a voice_id that was never prepare_voice()'d."""


@dataclass(frozen=True)
class VoiceInfo:
    """One configured voice in the registry."""

    voice_id: str
    ref_wav: Path
    ref_text: str
    blend: str | None = None


@runtime_checkable
class TTSBackend(Protocol):
    """The seam between VoiceEndpoint and the actual TTS model."""

    def prepare_voice(self, voice_id: str, ref_wav: Path, ref_text: str) -> None:
        """Build + cache the ICL prompt for ``voice_id``. Called once per voice at startup."""

    def synthesize(self, voice_id: str, text: str, seed: int) -> tuple[bytes, float]:
        """Generate audio for an already-prepared voice.

        Returns ``(wav_bytes, generation_s)``. Raises a ``VoiceError`` subclass on
        any failure the endpoint should turn into a SynthesisError.
        """


__all__ = [
    "EmptyTextError",
    "GPUOOMError",
    "TextTooLongError",
    "TTSBackend",
    "VoiceError",
    "VoiceInfo",
    "VoiceNotPreparedError",
]
