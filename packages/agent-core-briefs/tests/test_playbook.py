"""Playbook parser: extracts metadata, destinations, colors, sections,
conditional sections, extension refs from a YAML-in-MD playbook file.
Resolves dynamic colors via simpleeval; resolves file-ref guidance."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_core_briefs.playbook import (
    PlaybookParseError,
    parse_playbook,
    resolve_colors_for_sections,
    resolve_conditional_sections,
)

FIXTURE = Path(__file__).parent / "fixtures" / "playbooks" / "morning-test.md"


def test_parses_metadata():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    assert playbook.brief_type == "morning_brief"
    assert playbook.voice == "test"
    assert playbook.schedule_cron == "0 7 * * *"


def test_parses_destinations_with_var_substitution():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    assert len(playbook.destinations) == 2
    discord = next(d for d in playbook.destinations if d["type"] == "discord_embed")
    assert discord["config"]["channel_id"] == "12345"
    md = next(d for d in playbook.destinations if d["type"] == "markdown_file")
    assert "/test/root/" in md["config"]["path"]


def test_parses_color_palette():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    assert playbook.colors["TEST_RED"] == 15548997
    assert playbook.colors["TEST_GREEN"] == 5763719


def test_parses_sections_in_order():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    section_ids = [s.section_id for s in playbook.sections]
    assert section_ids == ["greeting", "calendar_today", "priorities_today"]


def test_section_with_static_color_resolves_to_decimal():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    static_sections = [s for s in playbook.sections if isinstance(s.color, str)]
    resolved = resolve_colors_for_sections(static_sections, playbook.colors, context={})
    greeting = next(s for s in resolved if s.section_id == "greeting")
    assert greeting.color == 15548997  # TEST_RED


def test_dynamic_color_resolves_against_context():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    # priorities_today has dynamic color: red if any project blocker, else green
    ctx_blocked = {"projects": {"active": [{"blockers": ["x"]}]}}
    resolved = resolve_colors_for_sections(
        playbook.sections,
        playbook.colors,
        context=ctx_blocked,
    )
    p = next(s for s in resolved if s.section_id == "priorities_today")
    assert p.color == 15548997  # TEST_RED

    ctx_clear = {"projects": {"active": [{"blockers": []}]}}
    resolved = resolve_colors_for_sections(
        playbook.sections,
        playbook.colors,
        context=ctx_clear,
    )
    p = next(s for s in resolved if s.section_id == "priorities_today")
    assert p.color == 5763719  # TEST_GREEN


def test_conditional_section_active_when_expr_true():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    ctx = {"now": {"day_of_week": "Monday"}}
    active_ids = resolve_conditional_sections(playbook.conditional_sections, ctx)
    assert active_ids == ["weekly_digest"]


def test_conditional_section_inactive_when_expr_false():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    ctx = {"now": {"day_of_week": "Tuesday"}}
    active_ids = resolve_conditional_sections(playbook.conditional_sections, ctx)
    assert active_ids == []


def test_missing_brief_type_raises(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("# bad\n```yaml\nvoice: test\n```\n", encoding="utf-8")
    with pytest.raises(PlaybookParseError, match="brief_type"):
        parse_playbook(bad, vars_map={})


def test_undefined_color_in_section_raises(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text(
        "# bad\n"
        "```yaml\nbrief_type: x\nvoice: y\n```\n"
        "```yaml\ncolors:\n  RED: 1\n```\n"
        "```yaml\nsection_id: s\ntitle: t\ncolor: PURPLE\nfields: []\n```\n",
        encoding="utf-8",
    )
    pb = parse_playbook(bad, vars_map={})
    with pytest.raises(PlaybookParseError, match="undefined color"):
        resolve_colors_for_sections(pb.sections, pb.colors, context={})


def test_dynamic_color_undefined_name_raises_loud(tmp_path):
    """A typo or missing context binding in a dynamic-color expression
    must raise — silent fallback to if_false would hide authoring bugs."""
    bad = tmp_path / "bad.md"
    bad.write_text(
        "# bad\n"
        "```yaml\nbrief_type: x\nvoice: y\n```\n"
        "```yaml\ncolors:\n  RED: 1\n  GREEN: 2\n```\n"
        "```yaml\n"
        "section_id: s\ntitle: t\n"
        "color:\n"
        "  dynamic: true\n"
        '  expr: "projeccts.active"\n'  # typo: projeccts
        "  if_true: RED\n"
        "  if_false: GREEN\n"
        "fields: []\n"
        "```\n",
        encoding="utf-8",
    )
    pb = parse_playbook(bad, vars_map={})
    with pytest.raises(PlaybookParseError, match="expression"):
        resolve_colors_for_sections(
            pb.sections,
            pb.colors,
            context={"projects": {"active": []}},  # right name; expr has wrong name
        )


def test_unrecognized_block_raises(tmp_path):
    """A block whose top-level keys don't match any classifier (e.g., a
    misspelled ``section-id``) must surface as a parse error rather than
    silently dropping content."""
    bad = tmp_path / "bad.md"
    bad.write_text(
        "# bad\n"
        "```yaml\nbrief_type: x\nvoice: y\n```\n"
        "```yaml\nrandom_key: nonsense\nother: data\n```\n",
        encoding="utf-8",
    )
    with pytest.raises(PlaybookParseError, match="unrecognized"):
        parse_playbook(bad, vars_map={})
