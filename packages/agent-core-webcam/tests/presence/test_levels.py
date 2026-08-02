"""Tests for the pure presence->guidance policy (no camera, no I/O)."""

from __future__ import annotations

from agent_core_webcam.presence.levels import DEFAULT_TEMPLATES, classify, render
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


def _render(state: PresenceState | None, *, level: int, principal: str = "jeff") -> str:
    return render(
        classify(state, principal=principal), state, level=level, templates=DEFAULT_TEMPLATES
    )


def test_level1_is_facts_only_even_with_unknown() -> None:
    """Ambient level never injects guidance, even when a stranger is present."""
    out = _render(_state(at_desk=True, known=["jeff"], unknown_count=3), level=1)
    assert out == "At desk: yes. Recognized: jeff. Unknown faces: 3."
    assert DEFAULT_TEMPLATES["shoulder_surf"] not in out
    assert DEFAULT_TEMPLATES["trust_gate"] not in out


def test_level2_adds_shoulder_surf_only_when_unknown_present() -> None:
    clear = _render(_state(at_desk=True, known=["jeff"], unknown_count=0), level=2)
    assert DEFAULT_TEMPLATES["shoulder_surf"] not in clear
    watched = _render(_state(at_desk=True, known=["jeff"], unknown_count=1), level=2)
    assert DEFAULT_TEMPLATES["shoulder_surf"] in watched
    # Level 2 never trust-gates, even when principal absent.
    assert DEFAULT_TEMPLATES["trust_gate"] not in _render(
        _state(at_desk=False, known=[], unknown_count=1), level=2
    )


def test_level3_trust_gates_when_principal_not_confirmed() -> None:
    absent = _render(_state(at_desk=False, known=[], unknown_count=1), level=3)
    assert DEFAULT_TEMPLATES["trust_gate"] in absent
    assert DEFAULT_TEMPLATES["shoulder_surf"] in absent  # cumulative
    # Principal confirmed and alone => no gating, no shoulder-surf.
    confirmed = _render(_state(at_desk=True, known=["jeff"], unknown_count=0), level=3)
    assert DEFAULT_TEMPLATES["trust_gate"] not in confirmed
    assert DEFAULT_TEMPLATES["shoulder_surf"] not in confirmed


def test_no_reading_uses_unknown_banner_and_gates_at_level3() -> None:
    out = _render(None, level=3)
    assert DEFAULT_TEMPLATES["unknown_banner"] in out
    assert "At desk" not in out  # no facts line when there is no reading
    assert DEFAULT_TEMPLATES["trust_gate"] in out  # uncertainty => cautious


def test_no_reading_never_asserts_someone_is_in_view() -> None:
    """With no reading, level 2 must caution without claiming an observation.

    Caught 2026-08-02 in live validation against an empty desk: the single
    ``shoulder_surf`` fragment fired on the no-reading path and stated "An
    unrecognized person is in view" when nothing had been seen at all. It
    failed safe, but a safety mechanism that asserts a fact it does not have
    is exactly what stops being believed.
    """
    out = _render(None, level=2)
    assert DEFAULT_TEMPLATES["shoulder_surf"] not in out
    assert DEFAULT_TEMPLATES["shoulder_surf_no_reading"] in out
    # Stronger than "doesn't claim a person": it must not open with any
    # assertion about the world that a later clause has to walk back, since
    # compression drops the retraction and keeps the claim.
    assert DEFAULT_TEMPLATES["shoulder_surf_no_reading"].startswith("No camera reading available")
    # ...and the honesty fix must not have cost any caution.
    assert DEFAULT_TEMPLATES["trust_gate"] in _render(None, level=3)
    assert DEFAULT_TEMPLATES["shoulder_surf_no_reading"] in _render(None, level=3)


def test_observed_unknown_still_states_the_observation() -> None:
    """The real-detection wording must survive: a seen stranger is a fact."""
    out = _render(_state(at_desk=True, known=["jeff"], unknown_count=2), level=2)
    assert DEFAULT_TEMPLATES["shoulder_surf"] in out
    assert DEFAULT_TEMPLATES["shoulder_surf_no_reading"] not in out


def test_templates_are_overridable() -> None:
    custom = {**DEFAULT_TEMPLATES, "trust_gate": "STRANGER — LOCK DOWN."}
    out = render(
        classify(_state(at_desk=False, known=[], unknown_count=1), principal="jeff"),
        _state(at_desk=False, known=[], unknown_count=1),
        level=3,
        templates=custom,
    )
    assert "STRANGER — LOCK DOWN." in out
