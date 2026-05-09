"""Issue #70: WakeAuditWriter — append-only JSONL with relay-side wake events."""

from __future__ import annotations

import json
from pathlib import Path

from agent_core_channel.wake_audit import WakeAuditWriter


def test_writer_creates_file_lazily(tmp_path: Path) -> None:
    target = tmp_path / "pepper.jsonl"
    writer = WakeAuditWriter(target)
    assert not target.exists()
    writer.write_inlined(
        wake_id="w-1",
        agent="pepper",
        mode="full",
        envelopes_inlined=[
            {"id": "e-1", "kind": "TextMessage", "from": "discord-pepper",
             "urgency": "green", "bytes": 87}
        ],
        queue_total_count=1,
    )
    assert target.exists()


def test_writer_appends_one_line_per_event(tmp_path: Path) -> None:
    target = tmp_path / "pepper.jsonl"
    writer = WakeAuditWriter(target)
    writer.write_inlined(
        wake_id="w-1", agent="pepper", mode="full",
        envelopes_inlined=[], queue_total_count=0,
    )
    writer.write_inlined(
        wake_id="w-2", agent="pepper", mode="full",
        envelopes_inlined=[], queue_total_count=0,
    )
    lines = target.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    e1 = json.loads(lines[0])
    e2 = json.loads(lines[1])
    assert e1["wake_id"] == "w-1"
    assert e2["wake_id"] == "w-2"


def test_writer_records_fallback(tmp_path: Path) -> None:
    target = tmp_path / "pepper.jsonl"
    writer = WakeAuditWriter(target)
    writer.write_fallback(
        wake_id="w-3", agent="pepper", mode="full",
        reason="envelope_count",
    )
    line = json.loads(target.read_text(encoding="utf-8").strip())
    assert line["wake_id"] == "w-3"
    assert line["fallback"] == "envelope_count"
    assert line["envelopes_inlined"] == []


def test_writer_records_render_error(tmp_path: Path) -> None:
    target = tmp_path / "pepper.jsonl"
    writer = WakeAuditWriter(target)
    writer.write_fallback(
        wake_id="w-4", agent="pepper", mode="full",
        reason="render_error", error_message="boom",
    )
    line = json.loads(target.read_text(encoding="utf-8").strip())
    assert line["fallback"] == "render_error"
    assert line["error"] == "boom"


def test_writer_includes_iso_timestamp(tmp_path: Path) -> None:
    target = tmp_path / "pepper.jsonl"
    writer = WakeAuditWriter(target)
    writer.write_inlined(
        wake_id="w-ts", agent="pepper", mode="full",
        envelopes_inlined=[], queue_total_count=0,
    )
    line = json.loads(target.read_text(encoding="utf-8").strip())
    assert "ts" in line
    # ISO 8601 format with 'T' and timezone marker.
    assert "T" in line["ts"]
    assert line["ts"].endswith("+00:00") or line["ts"].endswith("Z")
