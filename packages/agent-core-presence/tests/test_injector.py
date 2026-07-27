"""Tests for the ``PresenceInjector`` hook — rendering and the staleness guard."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agent_core_presence.injector import PresenceInjector
from agent_core_presence.state import PresenceState, write_state


def _write(tmp_path: Path, **kwargs: Any) -> Path:
    """Write a ``PresenceState`` from kwargs and return its path."""
    path = tmp_path / "state.json"
    write_state(PresenceState(**kwargs), path)
    return path


def test_fresh_reading_renders_tag(tmp_path: Path) -> None:
    """A fresh reading renders who is at the desk under the Presence heading."""
    path = _write(tmp_path, updated_at=time.time(), at_desk=True, known=["jeff"], unknown_count=0)
    result = PresenceInjector().execute("UserPromptSubmit", {}, {"state_path": str(path)})
    assert result.heading == "Presence"
    assert "At desk: yes" in result.content
    assert "jeff" in result.content
    assert "Unknown faces: 0" in result.content


def test_unknown_present_is_reported(tmp_path: Path) -> None:
    """An unrecognized person is reported as a count, with no identity."""
    path = _write(tmp_path, updated_at=time.time(), at_desk=True, known=[], unknown_count=1)
    result = PresenceInjector().execute("UserPromptSubmit", {}, {"state_path": str(path)})
    assert "Unknown faces: 1" in result.content
    assert "nobody enrolled-recognized" in result.content


def test_stale_reading_degrades_to_unknown(tmp_path: Path) -> None:
    """A reading older than max_age degrades to unknown and never asserts an identity."""
    path = _write(tmp_path, updated_at=time.time() - 3600, at_desk=True, known=["jeff"])
    result = PresenceInjector().execute(
        "UserPromptSubmit", {}, {"state_path": str(path), "max_age_seconds": 30}
    )
    assert "unknown" in result.content.lower()
    assert "stale" in result.content.lower()
    assert "jeff" not in result.content


def test_missing_state_degrades_to_unknown(tmp_path: Path) -> None:
    """A missing state file degrades to unknown rather than raising."""
    result = PresenceInjector().execute(
        "UserPromptSubmit", {}, {"state_path": str(tmp_path / "nope.json")}
    )
    assert "unknown" in result.content.lower()
