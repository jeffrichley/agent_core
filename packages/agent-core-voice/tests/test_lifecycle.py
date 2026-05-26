"""Tests for content-addressed WAV write + TTL cleanup."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core_voice.lifecycle import (
    cleanup_expired,
    retain_until_iso,
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
