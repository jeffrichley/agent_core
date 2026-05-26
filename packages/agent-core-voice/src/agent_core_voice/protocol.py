"""TTSBackend protocol + error taxonomy.

Single-source-of-truth for the voice-backend error taxonomy is now
``madrigal.engine.protocol`` (the upstream voice library). This module
re-exports those symbols so existing call sites continue to work
unchanged after the Phase 1 backend swap.

The ``TTSBackend`` Protocol and the ``VoiceError`` hierarchy come from
madrigal; ``VoiceInfo`` (the agent-core-voice registry record) stays
local because it's our wiring layer's concern, not madrigal's.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from madrigal.engine.protocol import (
    EmptyTextError,
    GPUOOMError,
    TextTooLongError,
    TTSBackend,
    VoiceError,
    VoiceNotPreparedError,
)


@dataclass(frozen=True)
class VoiceInfo:
    """One configured voice in the registry."""

    voice_id: str
    ref_wav: Path
    ref_text: str
    blend: str | None = None


__all__ = [
    "EmptyTextError",
    "GPUOOMError",
    "TextTooLongError",
    "TTSBackend",
    "VoiceError",
    "VoiceInfo",
    "VoiceNotPreparedError",
]
