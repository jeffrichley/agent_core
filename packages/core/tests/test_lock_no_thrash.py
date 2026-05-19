"""Guard: dynamic-version members must not carry a pinned version in uv.lock,
so source-only commits don't thrash the lockfile (the daemon's --frozen path).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_core.daemon.install import find_workspace_root


def _workspace_member_names(root: Path) -> set[str]:
    """Read each `packages/*/pyproject.toml`'s [project].name (runtime-derived
    so a future member can't silently slip past this guard).
    """
    names: set[str] = set()
    for pp in sorted((root / "packages").glob("*/pyproject.toml")):
        data = tomllib.loads(pp.read_text(encoding="utf-8"))
        name = data.get("project", {}).get("name")
        assert name, f"{pp} has no [project].name"
        names.add(name)
    assert names, f"no workspace members found under {root / 'packages'}"
    return names


def test_workspace_members_have_no_pinned_version_in_lock() -> None:
    root = find_workspace_root(Path(__file__))
    members = _workspace_member_names(root)
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    offenders = [
        p["name"]
        for p in lock["package"]
        if p["name"] in members and "version" in p
    ]
    assert not offenders, (
        "dynamic-version members must omit `version` in uv.lock "
        f"(lock thrash risk): {offenders}"
    )
