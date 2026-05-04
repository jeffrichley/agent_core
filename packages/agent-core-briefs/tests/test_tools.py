"""Agent-tool surface tests — the 5 tools shipped in T10.

Each test builds a minimal in-memory ``ComposeSession``, registers it,
and exercises one tool against the registry. The tools are async so
each test wraps the call with ``pytest.mark.asyncio``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_core_briefs.protocol import FieldSpec, SectionSpec
from agent_core_briefs.session import (
    ComposeSession,
    SessionNotFoundError,
    SessionRegistry,
)
from agent_core_briefs.tools import (
    add_extension_section,
    compress_sections,
    get_section_spec,
    list_sections,
    validate_section,
)


def _make_section(
    section_id: str,
    *,
    title: str = "Title",
    color: int | str | dict = 15548997,
    required: bool = True,
    fields: list[FieldSpec] | None = None,
) -> SectionSpec:
    return SectionSpec(
        section_id=section_id,
        title=title,
        color=color,
        required=required,
        fields=fields or [],
    )


def _register(
    registry: SessionRegistry,
    *,
    sections: list[SectionSpec] | None = None,
    sections_required: list[str] | None = None,
    sections_optional: list[str] | None = None,
    sections_conditional_active: list[str] | None = None,
    colors_palette: dict[str, int] | None = None,
    context: dict | None = None,
    voice: str = "Pepper, warm",
    brief_type: str = "morning_brief",
) -> str:
    session = ComposeSession(
        brief_type=brief_type,
        playbook_path=Path("/nonexistent.md"),
        voice=voice,
        scope="today",
        when=datetime.now(UTC),
        context=context or {},
        sections_required=sections_required or [],
        sections_optional=sections_optional or [],
        sections_conditional_active=sections_conditional_active or [],
        target_agent="agent",
        correlation_id="c1",
        metadata={},
        sections=sections or [],
        colors_palette=colors_palette or {},
        created_at=datetime.now(UTC),
    )
    return registry.create(session)


# ---- list_sections --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sections_returns_required_optional_conditional():
    """Happy path: each list and the brief-level fields come back unchanged."""
    registry = SessionRegistry()
    token = _register(
        registry,
        sections_required=["greeting", "calendar_today"],
        sections_optional=["footer_optional"],
        sections_conditional_active=["weekly_digest"],
        voice="Pepper, warm",
        brief_type="morning_brief",
    )
    result = await list_sections(registry, token)
    assert result == {
        "required": ["greeting", "calendar_today"],
        "optional": ["footer_optional"],
        "conditional_active": ["weekly_digest"],
        "brief_type": "morning_brief",
        "voice": "Pepper, warm",
    }


@pytest.mark.asyncio
async def test_list_sections_raises_on_unknown_token():
    """Unknown token propagates SessionNotFoundError."""
    registry = SessionRegistry()
    with pytest.raises(SessionNotFoundError):
        await list_sections(registry, "deadbeef" * 4)


# ---- get_section_spec -----------------------------------------------------


@pytest.mark.asyncio
async def test_get_section_spec_returns_resolved_color():
    """Color comes back as int decimal even when section.color is a palette name."""
    registry = SessionRegistry()
    token = _register(
        registry,
        sections=[
            _make_section(
                "greeting",
                title="Morning",
                color="RED",
                fields=[
                    FieldSpec(
                        name="Today",
                        required=True,
                        max_chars=200,
                        guidance="One-line frame.",
                    )
                ],
            )
        ],
        colors_palette={"RED": 15548997, "GREEN": 5763719},
    )
    result = await get_section_spec(registry, token, section_id="greeting")
    assert result["section_id"] == "greeting"
    assert result["title"] == "Morning"
    assert result["color"] == 15548997  # resolved from palette
    assert result["required"] is True
    assert result["fields"] == [
        {
            "name": "Today",
            "required": True,
            "max_chars": 200,
            "guidance": "One-line frame.",
        }
    ]


@pytest.mark.asyncio
async def test_get_section_spec_unknown_section_raises():
    """Section id not in playbook OR extensions raises ValueError."""
    registry = SessionRegistry()
    token = _register(
        registry,
        sections=[_make_section("greeting", color=15548997)],
        colors_palette={},
    )
    with pytest.raises(ValueError, match="ghost"):
        await get_section_spec(registry, token, section_id="ghost")


@pytest.mark.asyncio
async def test_get_section_spec_resolves_dynamic_color():
    """Dynamic-color expression evaluates against session.context."""
    registry = SessionRegistry()
    token = _register(
        registry,
        sections=[
            _make_section(
                "calendar_today",
                color={
                    "dynamic": True,
                    "expr": "calendar.has_meeting",
                    "if_true": "RED",
                    "if_false": "GREEN",
                },
            )
        ],
        colors_palette={"RED": 15548997, "GREEN": 5763719},
        context={"calendar": {"has_meeting": True}},
    )
    result = await get_section_spec(registry, token, section_id="calendar_today")
    assert result["color"] == 15548997  # RED branch


# ---- validate_section -----------------------------------------------------


@pytest.mark.asyncio
async def test_validate_section_passes_well_formed_fields():
    """All required fields present, none over budget → empty issues."""
    registry = SessionRegistry()
    token = _register(
        registry,
        sections=[
            _make_section(
                "greeting",
                color=15548997,
                fields=[FieldSpec(name="Today", required=True, max_chars=400)],
            )
        ],
    )
    result = await validate_section(
        registry,
        token,
        section_id="greeting",
        fields=[{"name": "Today", "value": "A short morning frame."}],
    )
    assert result == {"section_id": "greeting", "valid": True, "issues": []}


@pytest.mark.asyncio
async def test_validate_section_flags_missing_required_field():
    """Missing required field surfaces an issue mentioning the field name."""
    registry = SessionRegistry()
    token = _register(
        registry,
        sections=[
            _make_section(
                "greeting",
                color=15548997,
                fields=[
                    FieldSpec(name="Today", required=True),
                    FieldSpec(name="Note"),
                ],
            )
        ],
    )
    result = await validate_section(
        registry,
        token,
        section_id="greeting",
        fields=[{"name": "Note", "value": "side note"}],
    )
    assert result["valid"] is False
    assert any("Today" in issue and "required" in issue for issue in result["issues"])


@pytest.mark.asyncio
async def test_validate_section_flags_over_max_chars():
    """Value over max_chars surfaces an issue mentioning the count."""
    registry = SessionRegistry()
    token = _register(
        registry,
        sections=[
            _make_section(
                "greeting",
                color=15548997,
                fields=[FieldSpec(name="Schedule", max_chars=400)],
            )
        ],
    )
    long_value = "x" * 450
    result = await validate_section(
        registry,
        token,
        section_id="greeting",
        fields=[{"name": "Schedule", "value": long_value}],
    )
    assert result["valid"] is False
    issue = next(i for i in result["issues"] if "Schedule" in i)
    assert "450" in issue
    assert "400" in issue


@pytest.mark.asyncio
async def test_validate_section_flags_unknown_field_name():
    """Field names not in the spec come back as an issue."""
    registry = SessionRegistry()
    token = _register(
        registry,
        sections=[
            _make_section(
                "greeting",
                color=15548997,
                fields=[FieldSpec(name="Today", required=True)],
            )
        ],
    )
    result = await validate_section(
        registry,
        token,
        section_id="greeting",
        fields=[
            {"name": "Today", "value": "ok"},
            {"name": "Bogus", "value": "should be flagged"},
        ],
    )
    assert result["valid"] is False
    assert any("Bogus" in issue for issue in result["issues"])


# ---- compress_sections ----------------------------------------------------


@pytest.mark.asyncio
async def test_compress_sections_returns_proportional_budgets():
    """Budgets are proportional to per-section field max_chars sums and total to target."""
    registry = SessionRegistry()
    # Section A: max_chars sum = 200; B: max_chars sum = 400 → 1:2 split.
    token = _register(
        registry,
        sections=[
            _make_section(
                "a",
                color=15548997,
                fields=[FieldSpec(name="x", max_chars=200)],
            ),
            _make_section(
                "b",
                color=15548997,
                fields=[FieldSpec(name="y", max_chars=400)],
            ),
        ],
    )
    result = await compress_sections(
        registry,
        token,
        section_ids=["a", "b"],
        target_total_chars=1500,
    )
    assert result["target_total_chars"] == 1500
    by_id = {a["section_id"]: a["budget"] for a in result["allocations"]}
    # 1500 * 200/600 = 500 (A); B absorbs the remainder = 1000.
    assert by_id["a"] == 500
    assert by_id["b"] == 1000
    # Sum lands exact.
    assert sum(a["budget"] for a in result["allocations"]) == 1500


@pytest.mark.asyncio
async def test_compress_sections_respects_target_zero():
    """target_total_chars=0 returns 0-budget for every section without crashing."""
    registry = SessionRegistry()
    token = _register(
        registry,
        sections=[
            _make_section("a", color=15548997, fields=[FieldSpec(name="x", max_chars=200)]),
            _make_section("b", color=15548997, fields=[FieldSpec(name="y", max_chars=400)]),
        ],
    )
    result = await compress_sections(registry, token, section_ids=["a", "b"], target_total_chars=0)
    assert result["target_total_chars"] == 0
    assert all(a["budget"] == 0 for a in result["allocations"])


@pytest.mark.asyncio
async def test_compress_sections_default_budget_when_no_max_chars():
    """Sections without max_chars get a default weight, so the split is even."""
    registry = SessionRegistry()
    # Neither section has max_chars; both get the default weight → even split.
    token = _register(
        registry,
        sections=[
            _make_section("a", color=15548997, fields=[FieldSpec(name="x")]),
            _make_section("b", color=15548997, fields=[FieldSpec(name="y")]),
        ],
    )
    result = await compress_sections(
        registry, token, section_ids=["a", "b"], target_total_chars=1000
    )
    by_id = {a["section_id"]: a["budget"] for a in result["allocations"]}
    assert by_id["a"] == 500
    assert by_id["b"] == 500


# ---- add_extension_section -----------------------------------------------


@pytest.mark.asyncio
async def test_add_extension_section_appends():
    """add_extension_section appends to session.extension_sections and returns the spec."""
    registry = SessionRegistry()
    token = _register(
        registry,
        sections=[_make_section("greeting", color=15548997)],
        colors_palette={"RED": 15548997},
    )
    result = await add_extension_section(
        registry,
        token,
        section_id="blockers",
        title="Today's blockers",
        color=15548997,
        fields=[{"name": "Issue", "required": True}],
    )
    assert result["section_id"] == "blockers"
    assert result["title"] == "Today's blockers"
    assert result["color"] == 15548997
    assert result["required"] is False
    assert result["fields"] == [
        {
            "name": "Issue",
            "required": True,
            "max_chars": None,
            "guidance": None,
        }
    ]
    # Session state updated.
    session = registry.get(token)
    assert len(session.extension_sections) == 1
    assert session.extension_sections[0].section_id == "blockers"


@pytest.mark.asyncio
async def test_add_extension_section_collision_raises():
    """Section id colliding with playbook section OR prior extension raises ValueError."""
    registry = SessionRegistry()
    token = _register(
        registry,
        sections=[_make_section("greeting", color=15548997)],
    )
    # Collide with playbook section.
    with pytest.raises(ValueError, match="greeting"):
        await add_extension_section(
            registry,
            token,
            section_id="greeting",
            title="Dup",
            color=15548997,
            fields=[],
        )
    # Add an extension, then collide with it.
    await add_extension_section(
        registry,
        token,
        section_id="blockers",
        title="Blockers",
        color=15548997,
        fields=[],
    )
    with pytest.raises(ValueError, match="blockers"):
        await add_extension_section(
            registry,
            token,
            section_id="blockers",
            title="Dup",
            color=15548997,
            fields=[],
        )


@pytest.mark.asyncio
async def test_get_section_spec_finds_extension_section():
    """get_section_spec resolves extension sections too (added at agent-time)."""
    registry = SessionRegistry()
    token = _register(
        registry,
        sections=[_make_section("greeting", color=15548997)],
    )
    await add_extension_section(
        registry,
        token,
        section_id="blockers",
        title="Blockers",
        color=15548997,
        fields=[{"name": "Issue", "required": True}],
    )
    result = await get_section_spec(registry, token, section_id="blockers")
    assert result["section_id"] == "blockers"
    assert result["color"] == 15548997
