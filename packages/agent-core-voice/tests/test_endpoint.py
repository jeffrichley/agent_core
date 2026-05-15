"""VoiceEndpoint wiring against FakeTTSBackend."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import soundfile as sf

from agent_core_voice.endpoint import SynthesisSuccess, VoiceEndpoint
from agent_core_voice.fake import FakeTTSBackend
from agent_core_voice.protocol import VoiceInfo


def test_init_prepares_every_voice(tmp_path: Path, ref_wav: Path) -> None:
    """All configured voices are prepare_voice'd before __init__ returns."""
    backend = FakeTTSBackend()
    voices = {
        "alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="hi alice"),
        "bob":   VoiceInfo(voice_id="bob",   ref_wav=ref_wav, ref_text="hi bob"),
    }
    ep = VoiceEndpoint.for_test(
        backend=backend,
        voices=voices,
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    assert ep.voice_ids() == {"alice", "bob"}
    # FakeTTSBackend recorded which voices were prepared.
    assert backend._prepared == {"alice", "bob"}


def test_init_creates_output_dir(tmp_path: Path, ref_wav: Path) -> None:
    out = tmp_path / "voice_out"
    assert not out.exists()
    VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v": VoiceInfo(voice_id="v", ref_wav=ref_wav, ref_text="r")},
        output_dir=out,
        audit_path=out / "audit.jsonl",
    )
    assert out.is_dir()


def test_init_missing_ref_wav_raises(tmp_path: Path) -> None:
    """ref_wav validation runs during prepare_voice (the fake refuses missing files)."""
    with pytest.raises(FileNotFoundError):
        VoiceEndpoint.for_test(
            backend=FakeTTSBackend(),
            voices={
                "v": VoiceInfo(
                    voice_id="v",
                    ref_wav=tmp_path / "nope.wav",
                    ref_text="r",
                )
            },
            output_dir=tmp_path / "out",
            audit_path=tmp_path / "audit.jsonl",
        )


@pytest.mark.asyncio
async def test_synthesize_safe_happy_path(tmp_path: Path, ref_wav: Path) -> None:
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )

    result = await ep.synthesize_safe(
        agent_name="alice",
        voice_id="alice",
        text="hello world",
        seed=42,
    )

    assert isinstance(result, SynthesisSuccess)
    path = Path(result.path)
    assert path.exists()
    assert path.is_relative_to(tmp_path / "out" / "alice")
    # File is a valid 24 kHz mono wav.
    data, sr = sf.read(str(path))
    assert sr == 24000
    assert data.ndim == 1
    assert result.duration_s > 0
    assert result.generation_s >= 0


@pytest.mark.asyncio
async def test_synthesize_output_path_layout(tmp_path: Path, ref_wav: Path) -> None:
    """<output_dir>/<agent>/<YYYY-MM-DD>/<timestamp>-<seed>-<text_hash>.wav"""
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )

    result = await ep.synthesize_safe(
        agent_name="alice",
        voice_id="alice",
        text="hello",
        seed=42,
    )
    rel = Path(result.path).relative_to(tmp_path / "out")
    parts = rel.parts
    assert parts[0] == "alice"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1])
    assert re.fullmatch(r"\d{8}T\d{6}_\d{6}-42-[0-9a-f]{8}\.wav", parts[2])
