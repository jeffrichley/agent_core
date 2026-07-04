"""Pydantic payload models for voice endpoint envelopes.

Three event types ride on top of agent_core's Event kind:

- SynthesisRequest (caller → voice)
- SynthesisReady (voice → caller, success)
- SynthesisFailed (voice → caller, failure)

Validation runs at envelope publish/parse time so malformed requests
surface as ValidationError to the publisher rather than as opaque
runtime failures downstream.

See spec §4 for the wire-level contract.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_TIMEOUT_S_CAP = 600.0
_RETAIN_S_CAP = 86400.0

AudioFormat = Literal["wav", "mp3", "ogg"]


class SynthesisRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    timeout_s: float | None = Field(default=None, gt=0)
    retain_s: float | None = Field(default=None, gt=0)
    options: dict[str, Any] | None = None
    format: AudioFormat = "wav"

    @field_validator("timeout_s")
    @classmethod
    def _cap_timeout(cls, v: float | None) -> float | None:
        if v is not None and v > _TIMEOUT_S_CAP:
            raise ValueError(f"timeout_s exceeds 600s cap (got {v})")
        return v

    @field_validator("retain_s")
    @classmethod
    def _cap_retain(cls, v: float | None) -> float | None:
        if v is not None and v > _RETAIN_S_CAP:
            raise ValueError(f"retain_s exceeds 86400s cap (got {v})")
        return v


class SynthesisReadyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wav_path: str = Field(min_length=1)
    file_size_bytes: int = Field(ge=0)
    duration_s: float = Field(ge=0)
    elapsed_s: float = Field(ge=0)
    sample_rate_hz: int = Field(gt=0)
    cache_hit: bool
    chunks: int = Field(ge=1)
    retain_until: str = Field(min_length=1)


FailureReason = Literal[
    "GPU_OOM",
    "TEXT_TOO_LONG",
    "VOICE_NOT_PREPARED",
    "INTERNAL_ERROR",
    "TIMEOUT",
]


class SynthesisFailedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: FailureReason
    message: str = Field(min_length=1)
    retryable: bool


__all__ = [
    "AudioFormat",
    "FailureReason",
    "SynthesisFailedPayload",
    "SynthesisReadyPayload",
    "SynthesisRequestPayload",
]
