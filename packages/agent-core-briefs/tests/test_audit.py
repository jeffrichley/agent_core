"""Tests for the briefs audit log (JSONL append).

The audit log is observability for the submit handler — these tests
pin the on-disk format so log-aggregation tooling and human readers
can rely on the shape, and confirm error handling never breaks the
submit flow.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_core_briefs.audit import AuditEvent, AuditLog


def _event(**overrides) -> AuditEvent:
    """Build an AuditEvent with sensible defaults for tests."""
    base = {
        "timestamp": datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC),
        "event_type": "submit.complete",
        "session_token": "deadbeef",
        "brief_type": "morning_brief",
        "target_agent": "pepper",
        "data": {"total_destinations": 2, "successful": 1, "failed": 1},
    }
    base.update(overrides)
    return AuditEvent(**base)


@pytest.mark.asyncio
async def test_write_appends_jsonl_line(tmp_path: Path) -> None:
    """Two writes produce a 2-line file, each line a parseable JSON object."""
    log_path = tmp_path / "audit.jsonl"
    audit = AuditLog(log_path)

    await audit.write(_event(event_type="submit.validate.fail"))
    await audit.write(_event(event_type="submit.complete", data={"overall_success": True}))

    content = log_path.read_text(encoding="utf-8")
    lines = [line for line in content.split("\n") if line]
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["event_type"] == "submit.validate.fail"
    assert second["event_type"] == "submit.complete"
    assert second["data"]["overall_success"] is True


def test_default_path_is_under_agent_core_briefs() -> None:
    """default_path() lives under ~/.agent-core/briefs/audit.jsonl."""
    expected = Path.home() / ".agent-core" / "briefs" / "audit.jsonl"
    assert AuditLog.default_path() == expected


@pytest.mark.asyncio
async def test_write_creates_parent_directories(tmp_path: Path) -> None:
    """Writing to a non-existent nested path creates all needed parents."""
    log_path = tmp_path / "deep" / "nested" / "tree" / "audit.jsonl"
    audit = AuditLog(log_path)
    assert not log_path.parent.exists()

    await audit.write(_event())

    assert log_path.exists()
    assert log_path.parent.is_dir()


@pytest.mark.asyncio
async def test_event_serializes_datetime_as_iso(tmp_path: Path) -> None:
    """The timestamp must be an ISO-8601 string in the JSON, not an epoch number."""
    log_path = tmp_path / "audit.jsonl"
    audit = AuditLog(log_path)

    ts = datetime(2026, 5, 4, 14, 30, 15, tzinfo=UTC)
    await audit.write(_event(timestamp=ts))

    line = log_path.read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["timestamp"] == "2026-05-04T14:30:15+00:00"


@pytest.mark.asyncio
async def test_session_token_serialized_verbatim(tmp_path: Path) -> None:
    """The audit log writes session_token verbatim — truncation is the caller's job.

    The submit handler is expected to pass an 8-char prefix; the audit
    log doesn't re-truncate. This test pins the contract so a future
    "audit log truncates for me" change has to be made deliberately.
    """
    log_path = tmp_path / "audit.jsonl"
    audit = AuditLog(log_path)

    await audit.write(_event(session_token="abc12345"))
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["session_token"] == "abc12345"
    assert len(record["session_token"]) == 8


@pytest.mark.asyncio
async def test_write_swallows_disk_failure(tmp_path: Path, caplog) -> None:
    """A write that raises (here: bad path under a file-not-dir) does not propagate.

    Audit is observability, not the critical path — submit_brief must
    never crash because the audit log can't be written. We require the
    warning to actually land in the logger so a future regression that
    silently swallows the error AND skips logging would fail this test.
    """
    # Create a file where we'll try to put a directory underneath it.
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a directory.")
    bad_path = blocker / "nested" / "audit.jsonl"
    audit = AuditLog(bad_path)

    with caplog.at_level("WARNING", logger="agent_core_briefs.audit"):
        # Should NOT raise.
        await audit.write(_event())

    # Hard requirement (I3): the failure must have been logged at WARNING
    # level. No OR-escape hatch — silent swallow is a real regression.
    matching = [r for r in caplog.records if "audit" in r.getMessage().lower()]
    assert matching, (
        f"expected a WARNING from agent_core_briefs.audit; "
        f"got records: {[(r.name, r.levelname, r.getMessage()) for r in caplog.records]}"
    )
    # Sanity: blocker is still the file we left behind.
    assert blocker.is_file()


@pytest.mark.asyncio
async def test_path_property_returns_constructor_arg(tmp_path: Path) -> None:
    """AuditLog.path exposes the stored Path for tests / introspection."""
    log_path = tmp_path / "out.jsonl"
    audit = AuditLog(log_path)
    assert audit.path == log_path
