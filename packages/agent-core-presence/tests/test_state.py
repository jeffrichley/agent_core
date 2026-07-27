"""Tests for the presence-state file contract (write/read round-trips)."""

from __future__ import annotations

import time
from pathlib import Path

from agent_core_presence.state import PresenceState, read_state, write_state


def test_write_then_read_roundtrips(tmp_path: Path) -> None:
    """A written state reads back with every field intact."""
    path = tmp_path / "presence" / "state.json"
    state = PresenceState(updated_at=time.time(), at_desk=True, known=["jeff"], unknown_count=2)
    write_state(state, path)

    loaded = read_state(path)
    assert loaded is not None
    assert loaded.at_desk is True
    assert loaded.known == ["jeff"]
    assert loaded.unknown_count == 2


def test_read_missing_returns_none(tmp_path: Path) -> None:
    """A missing file reads as ``None`` rather than raising."""
    assert read_state(tmp_path / "nope.json") is None


def test_read_malformed_returns_none(tmp_path: Path) -> None:
    """Unparseable content reads as ``None`` rather than raising."""
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert read_state(path) is None


def test_write_leaves_no_temp_file(tmp_path: Path) -> None:
    """The atomic write cleans up after itself — no ``.tmp`` sibling lingers."""
    path = tmp_path / "state.json"
    write_state(PresenceState(updated_at=time.time()), path)
    assert list(tmp_path.glob("*.tmp")) == []
