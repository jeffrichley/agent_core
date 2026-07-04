"""Tests for content-addressed WAV write + TTL cleanup."""

from __future__ import annotations

import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from agent_core_voice.lifecycle import (
    cleanup_expired,
    retain_until_iso,
    transcode_audio,
    write_addressed,
)


def test_write_addressed_creates_sha_named_file(tmp_path: Path) -> None:
    audio = b"RIFF" + b"\x00" * 100
    path, sha = write_addressed(audio, root=tmp_path, retain_s=60.0)
    assert path.exists()
    assert path.name == f"{sha}.wav"
    assert path.read_bytes() == audio


def test_write_addressed_writes_meta_sidecar(tmp_path: Path) -> None:
    audio = b"RIFF" + b"\x00" * 100
    path, sha = write_addressed(audio, root=tmp_path, retain_s=120.0)
    meta_path = path.with_suffix(".meta.json")
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["retain_s"] == 120.0
    assert meta["sha256"] == sha


def test_write_addressed_dedupes_identical_audio(tmp_path: Path) -> None:
    audio = b"RIFF" + b"\x00" * 100
    path1, sha1 = write_addressed(audio, root=tmp_path, retain_s=60.0)
    path2, sha2 = write_addressed(audio, root=tmp_path, retain_s=60.0)
    assert path1 == path2
    assert sha1 == sha2


def test_retain_until_iso_returns_correct_offset() -> None:
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    out = retain_until_iso(retain_s=3600.0, now=now)
    assert out == "2026-05-26T13:00:00+00:00"


def test_cleanup_expired_removes_old_files(tmp_path: Path) -> None:
    audio = b"RIFF" + b"\x00" * 100
    path, sha = write_addressed(audio, root=tmp_path, retain_s=0.1)
    assert path.exists()
    time.sleep(0.2)
    n_removed = cleanup_expired(root=tmp_path)
    assert n_removed == 1
    assert not path.exists()
    assert not path.with_suffix(".meta.json").exists()


def test_cleanup_expired_keeps_live_files(tmp_path: Path) -> None:
    audio = b"RIFF" + b"\x00" * 100
    path, _ = write_addressed(audio, root=tmp_path, retain_s=3600.0)
    n_removed = cleanup_expired(root=tmp_path)
    assert n_removed == 0
    assert path.exists()


@pytest.mark.asyncio
async def test_endpoint_cleanup_tick_removes_expired(tmp_path):
    """VoiceEndpoint exposes a cleanup_tick coroutine that drives the lifecycle sweep."""
    import asyncio

    from madrigal.engine import FakeTTSBackend

    from agent_core_voice.endpoint import VoiceEndpoint
    from agent_core_voice.lifecycle import write_addressed
    from agent_core_voice.protocol import VoiceInfo

    output_dir = tmp_path / "voice-out"
    output_dir.mkdir()
    audio = b"RIFF" + b"\x00" * 100
    path, _ = write_addressed(audio, root=output_dir, retain_s=0.05)
    assert path.exists()

    ref_wav = tmp_path / "ref.wav"
    ref_wav.write_bytes(b"RIFF" + b"\x00" * 64)

    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v1": VoiceInfo(voice_id="v1", ref_wav=ref_wav, ref_text="hi")},
        output_dir=output_dir,
        audit_path=tmp_path / "audit.jsonl",
    )

    await asyncio.sleep(0.1)
    n = await ep.cleanup_tick()
    assert n == 1
    assert not path.exists()


@pytest.mark.asyncio
async def test_endpoint_cleanup_tick_no_op_on_clean_dir(tmp_path):
    """cleanup_tick returns 0 when no expired files exist."""
    from madrigal.engine import FakeTTSBackend

    from agent_core_voice.endpoint import VoiceEndpoint
    from agent_core_voice.protocol import VoiceInfo

    ref_wav = tmp_path / "ref.wav"
    ref_wav.write_bytes(b"RIFF" + b"\x00" * 64)

    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v1": VoiceInfo(voice_id="v1", ref_wav=ref_wav, ref_text="hi")},
        output_dir=tmp_path / "voice-out",
        audit_path=tmp_path / "audit.jsonl",
    )

    n = await ep.cleanup_tick()
    assert n == 0


# ── transcode_audio ────────────────────────────────────────────────────────


def _make_wav_bytes() -> bytes:
    """Return minimal valid WAV bytes (0.1s, 16 kHz mono float32)."""
    buf = io.BytesIO()
    sf.write(buf, np.zeros(1600, dtype="float32"), 16000, format="WAV")
    return buf.getvalue()


def test_transcode_audio_wav_returns_unchanged() -> None:
    audio = _make_wav_bytes()
    result = transcode_audio(audio, target_format="wav")
    assert result == audio


def test_transcode_audio_ogg_returns_ogg_bytes() -> None:
    audio = _make_wav_bytes()
    result = transcode_audio(audio, target_format="ogg")
    # OGG files start with "OggS" magic bytes.
    assert result[:4] == b"OggS"
    assert len(result) > 0


def test_transcode_audio_mp3_calls_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """MP3 path shells out to ffmpeg; mock subprocess.run to capture the call."""
    import subprocess

    fake_mp3 = b"\xff\xfb\x00\x00" + b"\x00" * 100  # fake mp3 header

    captured: list[dict] = []

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        captured.append({"args": args, "kwargs": kwargs})
        import subprocess as sp

        result = sp.CompletedProcess(args=args, returncode=0, stdout=fake_mp3, stderr=b"")
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    audio = _make_wav_bytes()
    result = transcode_audio(audio, target_format="mp3")
    assert result == fake_mp3
    assert len(captured) == 1
    assert "ffmpeg" in captured[0]["args"][0]
    # Must pipe wav in and mp3 out.
    assert "mp3" in captured[0]["args"]


def test_transcode_audio_unknown_format_raises_value_error() -> None:
    audio = _make_wav_bytes()
    with pytest.raises(ValueError, match="unknown"):
        transcode_audio(audio, target_format="flac")


def test_transcode_audio_ffmpeg_not_found_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FileNotFoundError from missing ffmpeg becomes RuntimeError."""

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr("subprocess.run", fake_run)
    audio = _make_wav_bytes()
    with pytest.raises(RuntimeError, match="ffmpeg"):
        transcode_audio(audio, target_format="mp3")


def test_transcode_audio_ffmpeg_failure_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-zero ffmpeg exit becomes RuntimeError carrying stderr fragment."""
    import subprocess as sp

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        return sp.CompletedProcess(
            args=args, returncode=1, stdout=b"", stderr=b"some ffmpeg error"
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    audio = _make_wav_bytes()
    with pytest.raises(RuntimeError, match="ffmpeg"):
        transcode_audio(audio, target_format="mp3")


# ── write_addressed with ext ───────────────────────────────────────────────


def test_write_addressed_default_ext_is_wav(tmp_path: Path) -> None:
    audio = b"RIFF" + b"\x00" * 100
    path, sha = write_addressed(audio, root=tmp_path, retain_s=60.0)
    assert path.suffix == ".wav"
    assert path.name == f"{sha}.wav"


def test_write_addressed_ext_changes_filename(tmp_path: Path) -> None:
    audio = b"RIFF" + b"\x00" * 100
    path, sha = write_addressed(audio, root=tmp_path, retain_s=60.0, ext="mp3")
    assert path.suffix == ".mp3"
    assert path.name == f"{sha}.mp3"


def test_write_addressed_ext_stored_in_meta(tmp_path: Path) -> None:
    audio = b"RIFF" + b"\x00" * 100
    path, sha = write_addressed(audio, root=tmp_path, retain_s=60.0, ext="ogg")
    meta = json.loads(path.with_suffix(".meta.json").read_text())
    assert meta["ext"] == "ogg"


def test_write_addressed_wav_ext_in_meta(tmp_path: Path) -> None:
    """Default ext=wav is also stored in meta."""
    audio = b"RIFF" + b"\x00" * 100
    path, sha = write_addressed(audio, root=tmp_path, retain_s=60.0)
    meta = json.loads(path.with_suffix(".meta.json").read_text())
    assert meta["ext"] == "wav"


# ── cleanup_expired with ext field ────────────────────────────────────────


def test_cleanup_expired_removes_mp3_file(tmp_path: Path) -> None:
    """cleanup_expired reads meta["ext"] to locate .mp3 companion files."""
    audio = b"\xff\xfb" + b"\x00" * 100
    path, sha = write_addressed(audio, root=tmp_path, retain_s=0.1, ext="mp3")
    assert path.exists()
    assert path.suffix == ".mp3"
    time.sleep(0.2)
    n = cleanup_expired(root=tmp_path)
    assert n == 1
    assert not path.exists()


def test_cleanup_expired_legacy_meta_without_ext_still_removes_wav(tmp_path: Path) -> None:
    """Meta sidecars written before this change (no "ext" key) default to .wav."""
    audio = b"RIFF" + b"\x00" * 100
    import hashlib

    sha = hashlib.sha256(audio).hexdigest()
    wav_path = tmp_path / f"{sha}.wav"
    wav_path.write_bytes(audio)
    # Write a legacy meta sidecar without "ext".
    meta_path = tmp_path / f"{sha}.meta.json"
    meta_path.write_text(
        json.dumps({"sha256": sha, "retain_s": 0.1, "written_at_utc": datetime.now(UTC).isoformat()})
    )
    time.sleep(0.2)
    n = cleanup_expired(root=tmp_path)
    assert n == 1
    assert not wav_path.exists()
