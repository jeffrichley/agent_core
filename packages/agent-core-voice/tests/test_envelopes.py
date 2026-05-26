"""Tests for SynthesisRequest/Ready/Failed payload models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_core_voice.envelopes import (
    SynthesisFailedPayload,
    SynthesisReadyPayload,
    SynthesisRequestPayload,
)


class TestSynthesisRequestPayload:
    def test_minimal_request_validates(self) -> None:
        p = SynthesisRequestPayload(text="hello")
        assert p.text == "hello"
        assert p.timeout_s is None  # default = use endpoint default
        assert p.retain_s is None
        assert p.options is None

    def test_timeout_cap_rejected_at_parse_time(self) -> None:
        with pytest.raises(ValidationError, match="timeout_s exceeds 600s cap"):
            SynthesisRequestPayload(text="hello", timeout_s=601.0)

    def test_retain_cap_rejected_at_parse_time(self) -> None:
        with pytest.raises(ValidationError, match="retain_s exceeds 86400s cap"):
            SynthesisRequestPayload(text="hello", retain_s=86401.0)

    def test_negative_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisRequestPayload(text="hello", timeout_s=-1.0)

    def test_empty_text_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisRequestPayload(text="")

    def test_options_dict_accepted(self) -> None:
        p = SynthesisRequestPayload(
            text="hi",
            options={"chunk_strategy": "sentence", "parallel": True, "seed": 7},
        )
        assert p.options == {"chunk_strategy": "sentence", "parallel": True, "seed": 7}


class TestSynthesisReadyPayload:
    def test_minimal_ready_validates(self) -> None:
        p = SynthesisReadyPayload(
            wav_path="/tmp/x.wav",
            file_size_bytes=1024,
            duration_s=1.5,
            elapsed_s=0.8,
            sample_rate_hz=24000,
            cache_hit=False,
            chunks=1,
            retain_until="2026-05-26T15:00:00+00:00",
        )
        assert p.wav_path == "/tmp/x.wav"


class TestSynthesisFailedPayload:
    def test_valid_reason_accepted(self) -> None:
        p = SynthesisFailedPayload(
            reason="GPU_OOM", message="GPU is OOM", retryable=True
        )
        assert p.reason == "GPU_OOM"
        assert p.retryable is True

    def test_invalid_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisFailedPayload(
                reason="MYSTERY", message="x", retryable=False
            )
