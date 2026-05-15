"""Error taxonomy + TTSBackend Protocol runtime-checkable verification."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from agent_core_voice.protocol import (
    EmptyTextError,
    GPUOOMError,
    TextTooLongError,
    TTSBackend,
    VoiceError,
    VoiceInfo,
    VoiceNotPreparedError,
)


def test_error_hierarchy() -> None:
    """All errors descend from VoiceError so handlers can catch one base."""
    for cls in (EmptyTextError, TextTooLongError, GPUOOMError, VoiceNotPreparedError):
        assert issubclass(cls, VoiceError)
        assert issubclass(cls, Exception)


def test_voice_info_is_frozen_dataclass() -> None:
    """VoiceInfo is immutable so it can be safely shared between threads."""
    info = VoiceInfo(
        voice_id="x",
        ref_wav=Path("/tmp/r.wav"),
        ref_text="hi",
        blend="test",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.voice_id = "y"  # type: ignore[misc]


def test_tts_backend_is_runtime_checkable() -> None:
    """isinstance(obj, TTSBackend) works at runtime."""

    class _Impl:
        def prepare_voice(self, voice_id, ref_wav, ref_text):  # type: ignore[no-untyped-def]
            return None

        def synthesize(self, voice_id, text, seed):  # type: ignore[no-untyped-def]
            return b"", 0.0

    class _Missing:
        def prepare_voice(self, voice_id, ref_wav, ref_text):  # type: ignore[no-untyped-def]
            return None

    assert isinstance(_Impl(), TTSBackend)
    assert not isinstance(_Missing(), TTSBackend)
