"""Characterization tests for the shared JsonlAuditLog base.

These tests exercise the append/serialize/swallow machinery via a minimal
concrete subclass so the shared infrastructure can be verified in isolation
from any domain's AuditEvent schema.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_core.audit import JsonlAuditLog


@dataclass(frozen=True)
class _Evt:
    msg: str


class _Log(JsonlAuditLog[_Evt]):
    def _serialize(self, event: _Evt) -> str:
        return json.dumps({"msg": event.msg}, ensure_ascii=False)


def test_append_line_creates_parent_dirs(tmp_path: Path) -> None:
    """_append_line creates parent directories that don't yet exist."""
    nested = tmp_path / "a" / "b" / "c" / "out.jsonl"
    _Log._append_line(nested, '{"msg": "hello"}')
    assert nested.exists()


def test_append_line_appends_not_truncates(tmp_path: Path) -> None:
    """Two _append_line calls produce two lines."""
    target = tmp_path / "out.jsonl"
    _Log._append_line(target, "line1")
    _Log._append_line(target, "line2")
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] == "line1"
    assert lines[1] == "line2"


@pytest.mark.asyncio
async def test_write_produces_readable_jsonl(tmp_path: Path) -> None:
    """A single write produces a valid JSON line readable from the file."""
    path = tmp_path / "audit.jsonl"
    log = _Log(path)
    await log.write(_Evt(msg="hello"))
    line = path.read_text(encoding="utf-8").strip()
    assert json.loads(line)["msg"] == "hello"


@pytest.mark.asyncio
async def test_write_swallows_disk_failure(tmp_path: Path) -> None:
    """write() does not raise when the path cannot be written.

    Uses the blocker-file technique: a regular file sits where the parent
    directory should be, so mkdir() raises NotADirectoryError.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a directory.")
    bad_path = blocker / "nested" / "audit.jsonl"
    log = _Log(bad_path)
    # Must not raise.
    await log.write(_Evt(msg="ignored"))
    assert not bad_path.exists()


@pytest.mark.asyncio
async def test_write_logs_warning_on_failure(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """write() emits a WARNING record when the append fails."""
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a directory.")
    bad_path = blocker / "nested" / "audit.jsonl"
    log = _Log(bad_path)
    import logging
    with caplog.at_level(logging.WARNING):
        await log.write(_Evt(msg="ignored"))
    assert any(r.levelname == "WARNING" for r in caplog.records), (
        f"expected a WARNING record; got: {[(r.name, r.levelname, r.getMessage()) for r in caplog.records]}"
    )


def test_path_property_round_trips_constructor_arg(tmp_path: Path) -> None:
    """_Log(p).path == p — the path property round-trips the constructor arg."""
    p = tmp_path / "audit.jsonl"
    assert _Log(p).path == p
