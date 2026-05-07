"""MCPAuditWriter and AuditLine: dated JSONL with locking and swallowed errors."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core.mcp_audit.writer import AuditLine, MCPAuditWriter, daily_path

# ---------------------------------------------------------------------------
# AuditLine.to_dict
# ---------------------------------------------------------------------------


def test_audit_line_to_dict_ok_result_omits_error_field():
    line = AuditLine(
        timestamp=datetime(2026, 5, 7, 14, 23, 7, tzinfo=UTC),
        endpoint="pepper",
        session_id="abc",
        request_id="42",
        tool="send",
        args_summary={"arg_keys": ["text"], "arg_count": 1},
        duration_ms=87,
        result="ok",
        error=None,
    )
    d = line.to_dict()
    assert d["endpoint"] == "pepper"
    assert d["tool"] == "send"
    assert d["result"] == "ok"
    assert d["duration_ms"] == 87
    assert d["args_summary"] == {"arg_keys": ["text"], "arg_count": 1}
    assert "error" not in d


def test_audit_line_to_dict_error_result_includes_error_type_and_message():
    line = AuditLine(
        timestamp=datetime(2026, 5, 7, tzinfo=UTC),
        endpoint="pepper",
        session_id=None,
        request_id="9",
        tool="capture_webcam_frame",
        args_summary={"arg_keys": [], "arg_count": 0},
        duration_ms=12,
        result="error",
        error={"type": "CameraBusyError", "message": "camera busy"},
    )
    d = line.to_dict()
    assert d["result"] == "error"
    assert d["error"] == {"type": "CameraBusyError", "message": "camera busy"}


def test_audit_line_to_dict_includes_session_id_null_when_none():
    line = AuditLine(
        timestamp=datetime(2026, 5, 7, tzinfo=UTC),
        endpoint="pepper",
        session_id=None,
        request_id="1",
        tool="send",
        args_summary={"arg_keys": [], "arg_count": 0},
        duration_ms=1,
        result="ok",
        error=None,
    )
    d = line.to_dict()
    assert "session_id" in d
    assert d["session_id"] is None


def test_audit_line_to_dict_serializes_timestamp_as_iso_with_offset():
    from zoneinfo import ZoneInfo
    ts = datetime(2026, 5, 7, 14, 23, 7, tzinfo=ZoneInfo("US/Eastern"))
    line = AuditLine(
        timestamp=ts, endpoint="x", session_id=None, request_id="1",
        tool="t", args_summary={"arg_keys": [], "arg_count": 0},
        duration_ms=1, result="ok", error=None,
    )
    d = line.to_dict()
    assert d["timestamp"].endswith("-04:00") or d["timestamp"].endswith("-05:00")


# ---------------------------------------------------------------------------
# daily_path
# ---------------------------------------------------------------------------


def test_daily_path_rolls_at_local_midnight_for_configured_timezone(tmp_path: Path):
    # 04:30 UTC = 00:30 ET → tomorrow's file in UTC, today's in ET
    when = datetime(2026, 5, 7, 4, 30, tzinfo=UTC)
    p_utc = daily_path(tmp_path, timezone="UTC", when=when)
    p_et = daily_path(tmp_path, timezone="US/Eastern", when=when)
    assert p_utc.name == "2026-05-07.jsonl"
    assert p_et.name == "2026-05-07.jsonl"  # 00:30 ET → still 2026-05-07

    # 03:30 UTC = 23:30 prior day in ET (DST: -04:00 in May, so 23:30 ET = 03:30 UTC).
    when2 = datetime(2026, 5, 7, 3, 30, tzinfo=UTC)
    p_et2 = daily_path(tmp_path, timezone="US/Eastern", when=when2)
    assert p_et2.name == "2026-05-06.jsonl"


# ---------------------------------------------------------------------------
# MCPAuditWriter
# ---------------------------------------------------------------------------


def _ok_line(tool: str = "send") -> AuditLine:
    return AuditLine(
        timestamp=datetime.now(UTC),
        endpoint="pepper",
        session_id="s1",
        request_id="r1",
        tool=tool,
        args_summary={"arg_keys": [], "arg_count": 0},
        duration_ms=5,
        result="ok",
        error=None,
    )


@pytest.mark.asyncio
async def test_writer_appends_one_jsonl_line_per_write(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    await writer.write(_ok_line("send"))
    await writer.write(_ok_line("handle"))

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert [p["tool"] for p in parsed] == ["send", "handle"]


@pytest.mark.asyncio
async def test_writer_creates_log_root_directory_on_first_write(tmp_path: Path):
    nested = tmp_path / "nested" / "audit"
    assert not nested.exists()
    writer = MCPAuditWriter(log_root=nested, timezone="UTC")
    await writer.write(_ok_line())
    assert nested.exists()
    assert any(nested.glob("*.jsonl"))


@pytest.mark.asyncio
async def test_writer_swallows_oserror_and_logs_warning(tmp_path: Path, caplog):
    # Simulate write failure by pointing log_root at a path whose parent
    # is itself a regular file (mkdir will fail).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    bad_root = blocker / "audit"
    writer = MCPAuditWriter(log_root=bad_root, timezone="UTC")

    import logging as _logging
    with caplog.at_level(_logging.WARNING, logger="agent_core.mcp_audit.writer"):
        # Must not raise.
        await writer.write(_ok_line())
    assert any("audit" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_writer_concurrent_writes_produce_intact_lines(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    # Each line carries a long marker so any byte-interleaving would corrupt it.
    async def _w(i: int) -> None:
        await writer.write(
            AuditLine(
                timestamp=datetime.now(UTC),
                endpoint=f"e{i}",
                session_id=None,
                request_id=str(i),
                tool="t",
                args_summary={"arg_keys": ["x" * 200], "arg_count": 1, "i": i},
                duration_ms=i,
                result="ok",
                error=None,
            )
        )

    await asyncio.gather(*[_w(i) for i in range(50)])

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 50
    # Each line must be valid JSON — corruption would raise here.
    parsed = [json.loads(line) for line in lines]
    assert {p["request_id"] for p in parsed} == {str(i) for i in range(50)}


@pytest.mark.asyncio
async def test_writer_uses_configured_timezone_for_path_rotation(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="US/Eastern")
    line = AuditLine(
        timestamp=datetime(2026, 5, 7, 3, 30, tzinfo=UTC),  # 23:30 ET prior day
        endpoint="pepper",
        session_id=None,
        request_id="1",
        tool="t",
        args_summary={"arg_keys": [], "arg_count": 0},
        duration_ms=1,
        result="ok",
        error=None,
    )
    await writer.write(line)
    files = list(tmp_path.glob("*.jsonl"))
    assert [f.name for f in files] == ["2026-05-06.jsonl"]
