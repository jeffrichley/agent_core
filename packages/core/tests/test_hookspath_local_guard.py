"""Local guard: in a developer worktree, `core.hooksPath` must be set so the
pre-push hook fires before `git push`. Skipped in CI (the runner clones
fresh, never runs `just install-hooks`, and doesn't need a pre-push hook —
the workflow IS the gate there).

This catches the very-common "I `git worktree add`-ed a fresh worktree and
forgot to re-run `just install-hooks`" footgun on the first `just check`
rather than letting the bad push reach origin.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agent_core.daemon.install import find_workspace_root
from agent_core.githooks import HOOKS_DIR_NAME


def _is_ci() -> bool:
    # GitHub Actions, Travis, CircleCI, GitLab CI, etc. all set CI=true.
    return os.environ.get("CI", "").lower() in {"true", "1", "yes"}


@pytest.mark.skipif(_is_ci(), reason="CI runner — pre-push hook not used")
def test_core_hookspath_points_at_versioned_hooks() -> None:
    root = find_workspace_root(Path(__file__))
    result = subprocess.run(
        ["git", "-C", str(root), "config", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=False,
    )
    actual = result.stdout.strip()
    # The recipe sets it to the relative string ".githooks"; some setups may
    # resolve to an absolute path ending in `.githooks`. Accept either, but
    # reject the default `.git/hooks` (no trailing `.githooks`).
    ok = actual.endswith(HOOKS_DIR_NAME) and "/.git/hooks" not in actual.replace(
        "\\", "/"
    ).rstrip("/").lower()
    assert ok, (
        f"core.hooksPath = {actual!r} — the version-controlled pre-push hook "
        f"will NOT fire from this worktree. Run: just install-hooks"
    )
