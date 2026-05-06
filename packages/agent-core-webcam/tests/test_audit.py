"""Tests for the webcam audit log (JSONL append).

Pins the on-disk schema so log readers (Pepper, operators, future
log-aggregation tooling) can rely on the field shape, and confirms a
disk failure never breaks the surrounding capture flow.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_core_webcam.audit import AuditEvent, AuditLog


def _event(**overrides) -> AuditEvent:
    base = {
        "timestamp": datetime(2026, 5, 6, 14, 23, 7, 481000, tzinfo=UTC),
        "tool": "capture_webcam_frame",
        "result": "ok",
        "data": {"camera_index": 0, "resolution": [1280, 720]},
    }
    base.update(overrides)
    return AuditEvent(**base)


async def test_write_appends_one_jsonl_line(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.jsonl")
    await log.write(_event())
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert text.endswith("\n")
    parsed = json.loads(text.strip())
    assert parsed["timestamp"] == "2026-05-06T14:23:07.481000+00:00"
    assert parsed["tool"] == "capture_webcam_frame"
    assert parsed["result"] == "ok"
    assert parsed["data"] == {"camera_index": 0, "resolution": [1280, 720]}


async def test_write_appends_multiple_lines(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.jsonl")
    await log.write(_event(tool="capture_webcam_frame"))
    await log.write(_event(tool="list_cameras"))
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["tool"] == "capture_webcam_frame"
    assert json.loads(lines[1])["tool"] == "list_cameras"


async def test_write_creates_parent_directory(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "audit.jsonl"
    log = AuditLog(target)
    await log.write(_event())
    assert target.exists()


async def test_write_swallows_disk_failure(tmp_path: Path):
    """A path that cannot be written must not raise — audit is observability,
    not the critical path of capture."""
    # Point at a directory we can't write to: use a path that has a file
    # where the parent should be a directory.
    blocking_file = tmp_path / "blocker"
    blocking_file.write_text("blocks the parent dir", encoding="utf-8")
    impossible = blocking_file / "audit.jsonl"
    log = AuditLog(impossible)
    # Must not raise.
    await log.write(_event())


def test_default_path_returns_endpoint_scoped_path():
    p = AuditLog.default_path("webcam-pepper")
    assert p.name == "audit.jsonl"
    assert p.parent.name == "webcam-pepper"
    assert p.parent.parent.name == "webcam"
