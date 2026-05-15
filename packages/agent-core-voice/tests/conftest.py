"""Shared fixtures for agent-core-voice tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from agent_core_voice.fake import FakeTTSBackend


@pytest.fixture
def ref_wav(tmp_path: Path) -> Path:
    """A 1-second 24 kHz mono wav file usable as a reference clip."""
    path = tmp_path / "ref.wav"
    sf.write(str(path), np.zeros(24000, dtype="float32"), 24000)
    return path


@pytest.fixture
def fake_backend() -> FakeTTSBackend:
    """A fresh FakeTTSBackend with no voices prepared."""
    return FakeTTSBackend()
