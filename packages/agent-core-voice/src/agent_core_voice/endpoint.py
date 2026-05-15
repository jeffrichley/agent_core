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

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_core_voice.audit import AuditLog
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

    # Endpoint protocol stubs (voice is tool-only — no envelope traffic).
    async def deliver(self, envelope: "Envelope", bus: "BusHandle") -> None:
        del envelope, bus  # voice publishes nothing

    async def start(self, bus: "BusHandle") -> None:
        del bus

    async def stop(self) -> None:
        return None


__all__ = [
    "SynthesisError",
    "SynthesisSuccess",
    "VoiceEndpoint",
]
