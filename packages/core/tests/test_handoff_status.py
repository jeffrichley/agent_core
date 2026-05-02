"""Tests for handoff_status sidecar helpers."""

import json
from pathlib import Path

from agent_core.hooks.handoff_status import (
    path_for_handoff,
    read_status,
    sha256_text,
    write_failed,
    write_pending,
    write_ready,
)


def test_path_for_handoff_sibling(tmp_path: Path):
    h = tmp_path / "a" / "handoff.md"
    assert path_for_handoff(h) == tmp_path / "a" / "handoff-status.json"


def test_write_pending_read_roundtrip(tmp_path: Path):
    p = tmp_path / "handoff-status.json"
    write_pending(p, "sess-1", correlation_id="corr-1")
    data = read_status(p)
    assert data is not None
    assert data["state"] == "pending"
    assert data["session_id"] == "sess-1"
    assert data["correlation_id"] == "corr-1"
    assert data["schema_version"] == 1


def test_write_ready_preserves_correlation(tmp_path: Path):
    p = tmp_path / "s.json"
    write_pending(p, "s1")
    prev = read_status(p)
    write_ready(p, "s1", "hello handoff")
    data = read_status(p)
    assert data["state"] == "ready"
    assert data["content_sha256"] == sha256_text("hello handoff")
    assert data["correlation_id"] == prev["correlation_id"]


def test_write_failed(tmp_path: Path):
    p = tmp_path / "f.json"
    write_pending(p, "s2")
    write_failed(p, "s2", "disk full")
    data = read_status(p)
    assert data["state"] == "failed"
    assert "disk" in data["error"]


def test_read_status_invalid_json(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_status(p) is None


def test_read_status_bom(tmp_path: Path):
    p = tmp_path / "bom.json"
    body = json.dumps({"state": "ready", "session_id": "x", "correlation_id": "c", "updated_at": "t", "error": None, "content_sha256": None, "schema_version": 1})
    p.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    assert read_status(p) is not None
