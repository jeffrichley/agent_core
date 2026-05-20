"""Guard: towncrier is configured and every workspace member has a
changelog.d/<pkg>/ section, so a new member can't silently drop out of
the aggregated CHANGELOG.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_core.daemon.install import find_workspace_root


def test_every_member_has_a_changelog_section_and_dir() -> None:
    root = find_workspace_root(Path(__file__))
    cfg = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    tc = cfg["tool"]["towncrier"]
    assert tc["directory"] == "changelog.d"
    assert tc["filename"] == "CHANGELOG.md"
    section_paths = {s["path"] for s in tc["section"]}
    members = sorted(p.name for p in (root / "packages").iterdir() if p.is_dir())
    missing_section = [m for m in members if m not in section_paths]
    missing_dir = [
        m for m in members if not (root / "changelog.d" / m).is_dir()
    ]
    assert not missing_section, f"members with no towncrier section: {missing_section}"
    assert not missing_dir, f"members with no changelog.d/<pkg>/ dir: {missing_dir}"
