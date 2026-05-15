"""VoiceEndpoint wiring against FakeTTSBackend."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core_voice.endpoint import VoiceEndpoint
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
