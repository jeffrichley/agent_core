"""Guard: every workspace member must carry the Defect-A cache key.

Fast unit test (no subprocess). If this fails, a member's
`pyproject.toml` is missing the git-commit cache key and
`daemon refresh` could silently ship stale code for that package.
See docs/superpowers/specs/2026-05-18-agent-core-maturity-design.md.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_core.daemon.install import find_workspace_root

EXPECTED_CACHE_KEYS = [
    {"file": "pyproject.toml"},
    {"git": {"commit": True}},
]


def _member_pyprojects() -> list[Path]:
    root = find_workspace_root(Path(__file__))
    members = sorted((root / "packages").glob("*/pyproject.toml"))
    assert members, f"no member pyproject.toml found under {root / 'packages'}"
    return members


def test_every_member_has_the_defect_a_cache_key() -> None:
    offenders: list[str] = []
    for pp in _member_pyprojects():
        data = tomllib.loads(pp.read_text(encoding="utf-8"))
        cache_keys = data.get("tool", {}).get("uv", {}).get("cache-keys")
        if cache_keys != EXPECTED_CACHE_KEYS:
            offenders.append(f"{pp}: cache-keys={cache_keys!r}")
    assert not offenders, (
        "members missing/incorrect [tool.uv] cache-keys "
        f"(expected {EXPECTED_CACHE_KEYS!r}):\n" + "\n".join(offenders)
    )
