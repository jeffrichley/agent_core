"""AuditLog writes one jsonl line per event with the documented schema."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_core_voice.audit import AuditEvent, AuditLog


@pytest.mark.asyncio
async def test_writes_success_line(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)

    await log.write(
        AuditEvent(
            timestamp=datetime(2026, 5, 15, 14, 23, 1, tzinfo=UTC),
            agent="alice",
            voice_id="alice",
            text_len=42,
            seed=42,
            duration_s=3.42,
            generation_s=9.1,
            wav_path="/tmp/x.wav",
            error=None,
        )
    )

    line = path.read_text("utf-8").strip()
    payload = json.loads(line)
    assert payload == {
        "ts": "2026-05-15T14:23:01+00:00",
        "agent": "alice",
        "voice_id": "alice",
        "text_len": 42,
        "seed": 42,
        "duration_s": 3.42,
        "generation_s": 9.1,
        "wav_path": "/tmp/x.wav",
        "error": None,
    }


@pytest.mark.asyncio
async def test_writes_error_line(tmp_path: Path) -> None:
    """On failure, duration_s + wav_path null, error populated."""
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)

    await log.write(
        AuditEvent(
            timestamp=datetime(2026, 5, 15, 14, 23, 1, tzinfo=UTC),
            agent="alice",
            voice_id="alice",
            text_len=0,
            seed=42,
            duration_s=None,
            generation_s=None,
            wav_path=None,
            error="text is empty",
        )
    )

    payload = json.loads(path.read_text("utf-8").strip())
    assert payload["error"] == "text is empty"
    assert payload["duration_s"] is None
    assert payload["wav_path"] is None


@pytest.mark.asyncio
async def test_appends_multiple_lines(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)

    for i in range(3):
        await log.write(
            AuditEvent(
                timestamp=datetime(2026, 5, 15, 14, 23, i, tzinfo=UTC),
                agent="alice",
                voice_id="alice",
                text_len=i,
                seed=42,
                duration_s=1.0,
                generation_s=1.0,
                wav_path=f"/tmp/{i}.wav",
                error=None,
            )
        )

    lines = path.read_text("utf-8").splitlines()
    assert len(lines) == 3


@pytest.mark.asyncio
async def test_write_swallows_disk_failure(tmp_path: Path) -> None:
    """A path that cannot be written must not raise — audit is observability,
    not the critical path of synthesis."""
    # Point at a directory we can't write to: place a regular file where the
    # parent dir should be, so mkdir() raises.
    blocking_file = tmp_path / "blocker"
    blocking_file.write_text("blocks the parent dir", encoding="utf-8")
    impossible = blocking_file / "audit.jsonl"
    log = AuditLog(impossible)

    result = await log.write(
        AuditEvent(
            timestamp=datetime(2026, 5, 15, 14, 23, 1, tzinfo=UTC),
            agent="alice",
            voice_id="alice",
            text_len=5,
            seed=42,
            duration_s=1.0,
            generation_s=1.0,
            wav_path="/tmp/x.wav",
            error=None,
        )
    )

    # Must not raise, must return None, and must not have created the file.
    assert result is None
    assert not impossible.exists()
