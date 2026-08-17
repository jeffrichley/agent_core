"""Tests for the ``PresenceInjector`` hook — rendering and the staleness guard."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agent_core_webcam.presence.injector import PresenceInjector
from agent_core_webcam.presence.levels import DEFAULT_TEMPLATES
from agent_core_webcam.presence.state import PresenceState, write_state


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
    assert "jeff" not in result.content  # a stale reading never asserts an identity


def test_missing_state_degrades_to_unknown(tmp_path: Path) -> None:
    """A missing state file degrades to unknown rather than raising."""
    result = PresenceInjector().execute(
        "UserPromptSubmit", {}, {"state_path": str(tmp_path / "nope.json")}
    )
    assert "unknown" in result.content.lower()


def _fresh(path: Path, *, at_desk: bool, known: list[str], unknown_count: int) -> None:
    write_state(
        PresenceState(
            updated_at=time.time(), at_desk=at_desk, known=known, unknown_count=unknown_count
        ),
        path,
    )


def test_level3_injects_trust_gate_when_stranger_only(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    _fresh(p, at_desk=False, known=[], unknown_count=1)
    out = PresenceInjector().execute("SessionStart", {}, {"state_path": str(p), "level": 3})
    assert DEFAULT_TEMPLATES["trust_gate"] in out.content


def test_level1_never_injects_guidance(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    _fresh(p, at_desk=False, known=[], unknown_count=2)
    out = PresenceInjector().execute("SessionStart", {}, {"state_path": str(p), "level": 1})
    assert DEFAULT_TEMPLATES["trust_gate"] not in out.content
    assert DEFAULT_TEMPLATES["shoulder_surf"] not in out.content


def test_stale_reading_degrades_to_cautious_at_level3(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    write_state(
        PresenceState(updated_at=1.0, at_desk=True, known=["jeff"], unknown_count=0), p
    )  # ancient
    out = PresenceInjector().execute("SessionStart", {}, {"state_path": str(p), "level": 3})
    # 2026-08-16: the fixed "unknown_banner" literal was RETIRED here — it is
    # the string that made a dead watcher and a 31-second-old reading identical
    # for 56 hours. The no-reading branch now names the cause and the age.
    # What must hold is the INVARIANT, not the wording: no reading => no facts.
    assert "At desk:" not in out.content
    assert "no current reading" in out.content.lower()
    assert DEFAULT_TEMPLATES["trust_gate"] in out.content


def test_custom_principal_and_templates(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    _fresh(p, at_desk=True, known=["pepper"], unknown_count=0)
    out = PresenceInjector().execute(
        "SessionStart", {}, {"state_path": str(p), "level": 3, "principal": "pepper"}
    )
    # Pepper confirmed present => no trust gate.
    assert DEFAULT_TEMPLATES["trust_gate"] not in out.content


def test_execute_never_raises_on_garbage_params(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    _fresh(p, at_desk=True, known=["jeff"], unknown_count=0)
    # A non-numeric max_age would blow up float() — must be swallowed to "unknown".
    out = PresenceInjector().execute(
        "SessionStart", {}, {"state_path": str(p), "max_age_seconds": "not-a-number"}
    )
    # 2026-08-16: the fixed "unknown_banner" literal was RETIRED here — it is
    # the string that made a dead watcher and a 31-second-old reading identical
    # for 56 hours. The no-reading branch now names the cause and the age.
    # What must hold is the INVARIANT, not the wording: no reading => no facts.
    assert "At desk:" not in out.content
    assert "no current reading" in out.content.lower()


def test_level3_error_path_still_trust_gates(tmp_path: Path) -> None:
    """An internal error at level 3 must NOT drop the trust-gate — the error path
    is level-appropriate, never less cautious than a normal missing reading."""
    p = tmp_path / "state.json"
    _fresh(p, at_desk=True, known=["jeff"], unknown_count=0)  # Jeff confirmed present
    out = PresenceInjector().execute(
        "SessionStart",
        {},
        # garbage max_age forces the except path; level stays 3
        {"state_path": str(p), "level": 3, "max_age_seconds": object()},
    )
    # 2026-08-16: the fixed "unknown_banner" literal was RETIRED here — it is
    # the string that made a dead watcher and a 31-second-old reading identical
    # for 56 hours. The no-reading branch now names the cause and the age.
    # What must hold is the INVARIANT, not the wording: no reading => no facts.
    assert "At desk:" not in out.content
    assert "no current reading" in out.content.lower()
    assert DEFAULT_TEMPLATES["trust_gate"] in out.content  # de-escalation preserved


def test_unparseable_level_defaults_to_max_caution(tmp_path: Path) -> None:
    """If even ``level`` is unusable on the error path, default to level 3."""
    p = tmp_path / "state.json"
    _fresh(p, at_desk=True, known=["jeff"], unknown_count=0)
    out = PresenceInjector().execute(
        "SessionStart",
        {},
        {"state_path": str(p), "level": object(), "max_age_seconds": object()},
    )
    assert DEFAULT_TEMPLATES["trust_gate"] in out.content
