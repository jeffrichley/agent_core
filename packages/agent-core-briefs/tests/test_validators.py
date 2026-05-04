"""Tests for ``validate_submission`` — submit-time submission validator.

These tests verify the cross-section and per-section rules the submit
handler relies on. The per-section validation overlaps deliberately
with :func:`agent_core_briefs.tools.validate_section` (which the agent
calls mid-compose); having both lets the framework refuse a final
submission even if the agent never consulted ``validate_section``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_core_briefs.protocol import FieldSpec, SectionSpec
from agent_core_briefs.session import ComposeSession
from agent_core_briefs.validators import ValidationIssue, validate_submission


def _make_session(
    *,
    sections: list[SectionSpec] | None = None,
    sections_required: list[str] | None = None,
    sections_optional: list[str] | None = None,
    sections_conditional_active: list[str] | None = None,
    extension_sections: list[SectionSpec] | None = None,
) -> ComposeSession:
    """Build a minimal ComposeSession for validator tests.

    The validator only reads the section specs and id lists; the rest
    of the session fields are filler.
    """
    return ComposeSession(
        brief_type="morning_brief",
        playbook_path=Path("/nonexistent.md"),
        voice="test",
        scope="today",
        when=datetime.now(UTC),
        context={},
        sections_required=sections_required or [],
        sections_optional=sections_optional or [],
        sections_conditional_active=sections_conditional_active or [],
        target_agent="agent",
        correlation_id="c1",
        metadata={},
        sections=sections or [],
        colors_palette={},
        created_at=datetime.now(UTC),
        extension_sections=extension_sections or [],
    )


def _section(
    section_id: str,
    *,
    fields: list[FieldSpec] | None = None,
) -> SectionSpec:
    return SectionSpec(
        section_id=section_id,
        title=section_id.title(),
        color=15548997,
        required=False,
        fields=fields or [],
    )


def _submitted(section_id: str, *, fields: list[dict] | None = None) -> dict:
    return {
        "section_id": section_id,
        "title": section_id.title(),
        "color": 15548997,
        "fields": fields or [],
    }


def test_validate_passes_well_formed_submission():
    """Happy path: every required section present with valid fields → no issues."""
    spec = _section(
        "greeting",
        fields=[FieldSpec(name="Today", required=True, max_chars=200)],
    )
    session = _make_session(sections=[spec], sections_required=["greeting"])
    submission = [_submitted("greeting", fields=[{"name": "Today", "value": "All good."}])]
    assert validate_submission(session=session, sections=submission) == []


def test_validate_flags_missing_required_section():
    """Required section absent from submission → missing_required_section issue."""
    spec = _section("greeting", fields=[FieldSpec(name="Today")])
    session = _make_session(sections=[spec], sections_required=["greeting"])
    issues = validate_submission(session=session, sections=[])
    assert any(i.code == "missing_required_section" and i.section_id == "greeting" for i in issues)


def test_validate_flags_missing_conditional_active_section():
    """Conditional-active sections are required at submit time too."""
    digest_spec = _section("weekly_digest", fields=[FieldSpec(name="This week")])
    session = _make_session(
        sections=[digest_spec],
        sections_conditional_active=["weekly_digest"],
    )
    issues = validate_submission(session=session, sections=[])
    assert any(
        i.code == "missing_required_section" and i.section_id == "weekly_digest" for i in issues
    )


def test_validate_flags_unknown_section():
    """Section in submission but not in playbook + extensions → unknown_section."""
    spec = _section("greeting", fields=[FieldSpec(name="Today")])
    session = _make_session(sections=[spec], sections_optional=["greeting"])
    submission = [
        _submitted("greeting", fields=[{"name": "Today", "value": "Hi"}]),
        _submitted("rogue_section", fields=[]),
    ]
    issues = validate_submission(session=session, sections=submission)
    assert any(i.code == "unknown_section" and i.section_id == "rogue_section" for i in issues)


def test_validate_flags_missing_required_field():
    """Required field absent (key missing) from submitted fields → missing_required_field."""
    spec = _section(
        "greeting",
        fields=[FieldSpec(name="Today", required=True), FieldSpec(name="Optional")],
    )
    session = _make_session(sections=[spec], sections_required=["greeting"])
    submission = [_submitted("greeting", fields=[{"name": "Optional", "value": "..."}])]
    issues = validate_submission(session=session, sections=submission)
    assert any(i.code == "missing_required_field" and i.section_id == "greeting" for i in issues)


def test_validate_flags_empty_required_field():
    """Required field present but value is whitespace → missing_required_field."""
    spec = _section("greeting", fields=[FieldSpec(name="Today", required=True)])
    session = _make_session(sections=[spec], sections_required=["greeting"])
    submission = [_submitted("greeting", fields=[{"name": "Today", "value": "   "}])]
    issues = validate_submission(session=session, sections=submission)
    assert any(i.code == "missing_required_field" and i.section_id == "greeting" for i in issues)


def test_validate_flags_over_max_chars():
    """A field longer than its spec's max_chars → field_over_max_chars."""
    spec = _section(
        "greeting",
        fields=[FieldSpec(name="Today", required=True, max_chars=10)],
    )
    session = _make_session(sections=[spec], sections_required=["greeting"])
    submission = [_submitted("greeting", fields=[{"name": "Today", "value": "x" * 50}])]
    issues = validate_submission(session=session, sections=submission)
    over = [i for i in issues if i.code == "field_over_max_chars"]
    assert len(over) == 1
    assert "max_chars 10" in over[0].message
    assert "50" in over[0].message


def test_validate_flags_unknown_field_in_section():
    """A submitted field name not in the spec → unknown_field."""
    spec = _section("greeting", fields=[FieldSpec(name="Today", required=True)])
    session = _make_session(sections=[spec], sections_required=["greeting"])
    submission = [
        _submitted(
            "greeting",
            fields=[
                {"name": "Today", "value": "Hi"},
                {"name": "Bogus", "value": "x"},
            ],
        )
    ]
    issues = validate_submission(session=session, sections=submission)
    assert any(
        i.code == "unknown_field" and "Bogus" in i.message and i.section_id == "greeting"
        for i in issues
    )


def test_validate_flags_too_many_sections_for_discord():
    """More than 10 submitted sections → too_many_sections_for_discord."""
    specs = [_section(f"s{i}") for i in range(12)]
    session = _make_session(sections=specs, sections_optional=[s.section_id for s in specs])
    submission = [_submitted(f"s{i}") for i in range(12)]
    issues = validate_submission(session=session, sections=submission)
    too_many = [i for i in issues if i.code == "too_many_sections_for_discord"]
    assert len(too_many) == 1
    assert too_many[0].section_id is None
    assert "12" in too_many[0].message
    assert "10" in too_many[0].message


def test_validate_accepts_extension_section_with_known_id():
    """Extension sections registered on the session count as known IDs."""
    ext = _section(
        "ad_hoc_blockers",
        fields=[FieldSpec(name="Note", required=True)],
    )
    session = _make_session(extension_sections=[ext])
    submission = [
        _submitted(
            "ad_hoc_blockers",
            fields=[{"name": "Note", "value": "Pipeline broken."}],
        )
    ]
    assert validate_submission(session=session, sections=submission) == []


def test_validation_issue_is_frozen_dataclass():
    """ValidationIssue should be frozen so callers can't mutate post-return."""
    issue = ValidationIssue(section_id="foo", code="x", message="y")
    import dataclasses

    assert dataclasses.is_dataclass(issue)
    # Frozen dataclasses raise on attribute assignment.
    try:
        issue.section_id = "bar"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("ValidationIssue should be frozen")
