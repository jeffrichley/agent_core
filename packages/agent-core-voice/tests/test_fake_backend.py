"""FakeTTSBackend contract: refuses what real refuses, deterministic distinct outputs."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from agent_core_voice.fake import FakeTTSBackend
from agent_core_voice.protocol import (
    EmptyTextError,
    TextTooLongError,
    VoiceNotPreparedError,
)


@pytest.fixture
def ref_wav(tmp_path: Path) -> Path:
    """A 1-second 24 kHz mono wav file for prepare_voice to read."""
    import numpy as np

    path = tmp_path / "ref.wav"
    sf.write(str(path), np.zeros(24000, dtype="float32"), 24000)
    return path


def test_synthesize_unprepared_raises(ref_wav: Path) -> None:
    backend = FakeTTSBackend()
    with pytest.raises(VoiceNotPreparedError):
        backend.synthesize("never_prepared", "hello", 42)


def test_empty_text_raises(ref_wav: Path) -> None:
    backend = FakeTTSBackend()
    backend.prepare_voice("v", ref_wav, "ref")
    with pytest.raises(EmptyTextError):
        backend.synthesize("v", "", 42)
    with pytest.raises(EmptyTextError):
        backend.synthesize("v", "   ", 42)


def test_text_too_long_raises(ref_wav: Path) -> None:
    backend = FakeTTSBackend(max_text_len=10)
    backend.prepare_voice("v", ref_wav, "ref")
    with pytest.raises(TextTooLongError):
        backend.synthesize("v", "this is way too long", 42)


def test_synthesize_deterministic(ref_wav: Path) -> None:
    """Same (voice_id, text, seed) -> byte-identical output."""
    backend = FakeTTSBackend()
    backend.prepare_voice("v", ref_wav, "ref")
    a, _ = backend.synthesize("v", "hello", 42)
    b, _ = backend.synthesize("v", "hello", 42)
    assert a == b


def test_synthesize_distinct_inputs_distinct_outputs(ref_wav: Path) -> None:
    """Different inputs must produce different audio (catches voice mix-up bugs)."""
    backend = FakeTTSBackend()
    backend.prepare_voice("v1", ref_wav, "ref")
    backend.prepare_voice("v2", ref_wav, "ref")

    by_voice_v1, _ = backend.synthesize("v1", "hello", 42)
    by_voice_v2, _ = backend.synthesize("v2", "hello", 42)
    assert by_voice_v1 != by_voice_v2, "Different voice_id must change audio"

    by_text_a, _ = backend.synthesize("v1", "hello", 42)
    by_text_b, _ = backend.synthesize("v1", "world", 42)
    assert by_text_a != by_text_b, "Different text must change audio"

    by_seed_a, _ = backend.synthesize("v1", "hello", 1)
    by_seed_b, _ = backend.synthesize("v1", "hello", 2)
    assert by_seed_a != by_seed_b, "Different seed must change audio"


def test_synthesize_returns_valid_wav(ref_wav: Path) -> None:
    """The returned bytes are a real 24 kHz mono wav soundfile can decode."""
    backend = FakeTTSBackend()
    backend.prepare_voice("v", ref_wav, "ref")
    wav_bytes, generation_s = backend.synthesize("v", "hello world", 42)

    data, sr = sf.read(io.BytesIO(wav_bytes))
    assert sr == 24000
    assert data.ndim == 1
    assert len(data) > 0
    assert generation_s >= 0.0
    assert np.any(data != 0), "fake should produce non-silent audio"
