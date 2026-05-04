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

_DEFAULT_DESTINATIONS = [{"type": "markdown_file", "config": {"path": "out.md"}}]


def _make_session(
    *,
    sections: list[SectionSpec] | None = None,
    sections_required: list[str] | None = None,
    sections_optional: list[str] | None = None,
    sections_conditional_active: list[str] | None = None,
    extension_sections: list[SectionSpec] | None = None,
    conditional_sections: list[SectionSpec] | None = None,
    destinations: list[dict] | None = None,
) -> ComposeSession:
    """Build a minimal ComposeSession for validator tests.

    Defaults ``destinations`` to a single markdown_file entry so unrelated
    tests don't trip the no_destinations_configured rule. Tests
    exercising that rule pass ``destinations=[]`` explicitly.
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
        destinations=destinations if destinations is not None else list(_DEFAULT_DESTINATIONS),
        conditional_sections=conditional_sections or [],
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


def test_validate_accepts_more_than_10_sections():
    """The Discord 10-embed limit is destination-specific, NOT a generic
    submission rule (I4). A markdown-only brief with 12 sections is fine."""
    specs = [_section(f"s{i}") for i in range(12)]
    session = _make_session(sections=specs, sections_optional=[s.section_id for s in specs])
    submission = [_submitted(f"s{i}") for i in range(12)]
    issues = validate_submission(session=session, sections=submission)
    # No code about embed limits should appear.
    assert not any("discord" in i.code or "embed" in i.code for i in issues)


def test_validate_flags_no_destinations_configured():
    """I1: a session with no destinations is undeliverable → no_destinations_configured.

    Surfaced as a validation failure so submit_brief short-circuits BEFORE
    consuming the session token; the agent / operator can fix the playbook
    and retry with the same token.
    """
    session = _make_session(destinations=[])
    issues = validate_submission(session=session, sections=[])
    no_dest = [i for i in issues if i.code == "no_destinations_configured"]
    assert len(no_dest) == 1
    assert no_dest[0].section_id is None


def test_validate_flags_missing_required_field_in_conditional_active_section():
    """C1 regression: conditional sections that are active still need their
    required fields validated. Without ``session.conditional_sections``,
    submit-time validation silently skips per-section field checks for
    conditional sections — Pepper's morning_brief weekly_digest depends on
    this enforcement.
    """
    spec = SectionSpec(
        section_id="weekly_digest",
        title="Week ahead",
        color=5763719,
        required=False,  # conditional, not required-by-default
        fields=[FieldSpec(name="This week", required=True, max_chars=200)],
    )
    session = _make_session(
        sections=[],
        sections_required=[],
        sections_optional=[],
        sections_conditional_active=["weekly_digest"],
        conditional_sections=[spec],
    )
    submitted = [
        {
            "section_id": "weekly_digest",
            "fields": [{"name": "This week", "value": ""}],
        }
    ]
    issues = validate_submission(session=session, sections=submitted)
    assert any(
        i.code == "missing_required_field" and i.section_id == "weekly_digest" for i in issues
    )


def test_validate_flags_over_max_chars_in_conditional_active_section():
    """C1: max_chars on conditional section fields is also enforced."""
    spec = SectionSpec(
        section_id="weekly_digest",
        title="Week ahead",
        color=5763719,
        fields=[FieldSpec(name="This week", required=True, max_chars=10)],
    )
    session = _make_session(
        sections_conditional_active=["weekly_digest"],
        conditional_sections=[spec],
    )
    submitted = [
        {
            "section_id": "weekly_digest",
            "fields": [{"name": "This week", "value": "x" * 50}],
        }
    ]
    issues = validate_submission(session=session, sections=submitted)
    assert any(i.code == "field_over_max_chars" and i.section_id == "weekly_digest" for i in issues)


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
