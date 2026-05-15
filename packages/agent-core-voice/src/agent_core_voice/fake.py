"""FakeTTSBackend -- test-only stand-in for QwenTTSBackend.

Returns deterministic sine-wave wav bytes keyed by ``(voice_id, text, seed)``.
Distinct inputs always produce byte-distinct outputs so tests that confuse
voices or seeds fail loudly. Refuses the same argument shapes the real
backend refuses (per the test-fakes-mirror-real-strictly principle).

NEVER referenced from plugin.py or yaml. Tests construct VoiceEndpoint
directly with ``backend=FakeTTSBackend(...)``.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
import soundfile as sf

from agent_core_voice.protocol import (
    EmptyTextError,
    TextTooLongError,
    VoiceNotPreparedError,
)

_SAMPLE_RATE = 24000


class FakeTTSBackend:
    """Deterministic dummy backend for tests. Never used in production wiring."""

    def __init__(self, *, max_text_len: int = 10_000) -> None:
        self._prepared: set[str] = set()
        self._max_text_len = max_text_len

    def prepare_voice(self, voice_id: str, ref_wav: Path, ref_text: str) -> None:
        if not Path(ref_wav).exists():
            raise FileNotFoundError(f"ref_wav not found: {ref_wav}")
        self._prepared.add(voice_id)

    def synthesize(self, voice_id: str, text: str, seed: int) -> tuple[bytes, float]:
        if voice_id not in self._prepared:
            raise VoiceNotPreparedError(f"voice {voice_id!r} not prepared")
        if not text or not text.strip():
            raise EmptyTextError("text is empty")
        if len(text) > self._max_text_len:
            raise TextTooLongError(f"text length {len(text)} exceeds budget {self._max_text_len}")

        # Hash (voice_id, text, seed) -> sine-wave frequency in 200-800 Hz so
        # distinct inputs produce distinct audio. The '|' separator prevents
        # boundary-collision aliasing (e.g. ("v1", "23", 4) vs ("v1", "2", "34")).
        # Duration is proportional to text length so callers can assert on it.
        key = f"{voice_id}|{text}|{seed}".encode()
        digest = hashlib.sha256(key).digest()
        freq_hz = 200 + (int.from_bytes(digest[:4], "big") % 600)
        duration_s = max(0.25, min(5.0, 0.05 * len(text)))

        n_samples = int(duration_s * _SAMPLE_RATE)
        t = np.arange(n_samples, dtype=np.float32) / _SAMPLE_RATE
        signal = 0.3 * np.sin(2 * np.pi * freq_hz * t).astype(np.float32)

        buf = io.BytesIO()
        sf.write(buf, signal, _SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return buf.getvalue(), 0.001


__all__ = ["FakeTTSBackend"]
