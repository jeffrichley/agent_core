"""Real Qwen3-TTS backend with in-context-learning voice cloning.

Torch and qwen_tts are lazy-imported inside ``__init__`` so the rest of
the package (protocol, fake, endpoint, mcp, plugin) is importable on
hosts where torch hasn't been installed (e.g., CI runners using the cpu
extra without GPU, or any host running only the unit tests).

Never used by tests. Production wiring constructs this class with the
yaml params; the bus runner is responsible for instantiating it via
``register_endpoint_types``.
"""

from __future__ import annotations

import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import soundfile as sf

from agent_core_voice.protocol import (
    EmptyTextError,
    GPUOOMError,
    VoiceNotPreparedError,
)

log = logging.getLogger(__name__)


class QwenTTSBackend:
    """Real backend: loads Qwen3-TTS once, holds ICL prompts per voice."""

    def __init__(
        self,
        *,
        model_path: str,
        device: str = "cuda:0",
        attn_implementation: str = "sdpa",
    ) -> None:
        # Lazy imports — torch and qwen_tts only required for production.
        import torch
        from qwen_tts import Qwen3TTSModel

        self._torch = torch
        self._device = device
        log.info(
            "loading Qwen3-TTS: model_path=%s device=%s attn=%s",
            model_path,
            device,
            attn_implementation,
        )
        self._model = Qwen3TTSModel.from_pretrained(
            model_path,
            device_map=device,
            torch_dtype=torch.bfloat16,
            attn_implementation=attn_implementation,
        )
        self._prompts: dict[str, Any] = {}

    def prepare_voice(self, voice_id: str, ref_wav: Path, ref_text: str) -> None:
        ref_wav = Path(ref_wav)
        if not ref_wav.exists():
            raise FileNotFoundError(f"ref_wav not found: {ref_wav}")
        prompt = self._model.create_voice_clone_prompt(
            ref_audio=str(ref_wav),
            ref_text=ref_text,
        )
        self._prompts[voice_id] = prompt
        log.info("voice %r prepared", voice_id)

    def synthesize(self, voice_id: str, text: str, seed: int) -> tuple[bytes, float]:
        if voice_id not in self._prompts:
            raise VoiceNotPreparedError(f"voice {voice_id!r} not prepared")
        if not text or not text.strip():
            raise EmptyTextError("text is empty")

        self._torch.manual_seed(seed)
        start = time.monotonic()
        try:
            wavs, sr = self._model.generate_voice_clone(
                text=text,
                language="english",
                voice_clone_prompt=self._prompts[voice_id],
            )
        except RuntimeError as exc:
            if "CUDA out of memory" in str(exc) or "OutOfMemoryError" in type(exc).__name__:
                raise GPUOOMError(str(exc)) from exc
            raise

        generation_s = time.monotonic() - start

        buf = BytesIO()
        sf.write(buf, wavs[0], int(sr), format="WAV", subtype="PCM_16")
        return buf.getvalue(), generation_s


__all__ = ["QwenTTSBackend"]
