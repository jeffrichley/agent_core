"""Tests for the pure presence->guidance policy (no camera, no I/O)."""

from __future__ import annotations

from agent_core_webcam.presence.levels import classify
from agent_core_webcam.presence.state import PresenceState


def _state(*, at_desk: bool, known: list[str], unknown_count: int) -> PresenceState:
    return PresenceState(
        updated_at=1000.0, at_desk=at_desk, known=known, unknown_count=unknown_count
    )


def test_none_state_is_maximally_uncertain() -> None:
    """A missing/stale reading (None) => no reading, principal absent, unknown present."""
    r = classify(None, principal="jeff")
    assert r.have_reading is False
    assert r.principal_present is False
    assert r.unknown_present is True  # cautious side: shoulder-surf still fires


def test_principal_present_when_at_desk_and_enrolled() -> None:
    r = classify(_state(at_desk=True, known=["jeff"], unknown_count=0), principal="jeff")
    assert r.have_reading is True
    assert r.principal_present is True
    assert r.unknown_present is False


def test_principal_absent_when_not_at_desk() -> None:
    r = classify(_state(at_desk=False, known=["jeff"], unknown_count=0), principal="jeff")
    assert r.principal_present is False


def test_principal_absent_when_not_in_known() -> None:
    r = classify(_state(at_desk=True, known=[], unknown_count=0), principal="jeff")
    assert r.principal_present is False


def test_unknown_present_tracks_count() -> None:
    r = classify(_state(at_desk=True, known=["jeff"], unknown_count=2), principal="jeff")
    assert r.principal_present is True  # Jeff present AND a stranger present
    assert r.unknown_present is True


def test_principal_name_is_configurable() -> None:
    r = classify(_state(at_desk=True, known=["pepper"], unknown_count=0), principal="pepper")
    assert r.principal_present is True
