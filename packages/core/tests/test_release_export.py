"""Test that `uv export` (as invoked from release.yml) produces a
requirements.txt that does NOT contain workspace-relative path references —
neither editable workspace-member entries nor non-editable vendored-path entries.

Phase 2.6 Bug 2: the original bug surfaced by Phase 3.5's test instance —
the generated requirements.txt contained lines like `-e ./packages/...` that
don't resolve on the daemon side (where the workspace doesn't exist).
The workspace packages ship via wheels; requirements.txt should carry
only third-party deps.

Opus Suggestion 4 (final-review): the original assertion only caught the
editable `-e ./packages/...` case and missed the non-editable vendored-path
case (qwen-tts: `path = "vendor/Qwen3-TTS", editable = false`). This
strengthened version catches BOTH shapes.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _make_fixture_workspace(root: Path) -> None:
    """Build a minimal uv workspace under `root` with one editable
    workspace member AND one non-editable vendored path dep. The vendored
    path tests the Phase 2.6 Bug 2 case (opus Suggestion 4) where the
    original assertion only caught editable lines."""
    (root / "pyproject.toml").write_text(
        '[project]\n'
        'name = "fixture-root"\n'
        'version = "0.0.0"\n'
        'requires-python = ">=3.12"\n'
        'dependencies = [\n'
        '  "fixture-member",\n'
        '  "fixture-vendored",\n'
        '  "annotated-types>=0.7.0",\n'
        ']\n'
        '\n'
        '[tool.uv.workspace]\n'
        'members = ["packages/fixture-member", "vendor/fixture-vendored"]\n'
        '\n'
        '[tool.uv.sources]\n'
        'fixture-member = { workspace = true }\n'
        'fixture-vendored = { workspace = true }\n'
        '\n'
        '[build-system]\n'
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
    )
    # Workspace member (gets stripped by --no-emit-workspace)
    member = root / "packages" / "fixture-member"
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        '[project]\n'
        'name = "fixture-member"\n'
        'version = "0.0.0"\n'
        'requires-python = ">=3.12"\n'
        'dependencies = []\n'
        '\n'
        '[build-system]\n'
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
    )
    msrc = member / "src" / "fixture_member"
    msrc.mkdir(parents=True)
    (msrc / "__init__.py").write_text("__version__ = '0.0.0'\n")

    # Vendored package promoted to workspace member (mirrors the qwen-tts fix:
    # instead of path = "vendor/...", editable = false, we make it a workspace
    # member so --no-emit-workspace strips it from requirements.txt).
    vendor = root / "vendor" / "fixture-vendored"
    vendor.mkdir(parents=True)
    (vendor / "pyproject.toml").write_text(
        '[project]\n'
        'name = "fixture-vendored"\n'
        'version = "0.0.0"\n'
        'requires-python = ">=3.12"\n'
        'dependencies = []\n'
        '\n'
        '[build-system]\n'
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
    )
    vsrc = vendor / "src" / "fixture_vendored"
    vsrc.mkdir(parents=True)
    (vsrc / "__init__.py").write_text("__version__ = '0.0.0'\n")


@pytest.mark.skipif(
    shutil.which("uv") is None, reason="uv binary not on PATH"
)
def test_uv_export_no_emit_workspace_excludes_member_packages(tmp_path):
    """`uv export --no-emit-workspace` must produce a requirements-txt
    that does NOT contain ANY workspace-relative path references —
    neither editable `-e ./packages/...` lines NOR non-editable
    `./packages/...` / `./vendor/...` lines. Per opus Suggestion 4 on
    the Phase 2.6 final-review: the original assertion only caught the
    editable case and missed the vendored-path case (qwen-tts).

    The fixture mirrors the real fix pattern: the vendored package is
    promoted to a workspace member (not a path = ... source binding),
    so --no-emit-workspace strips it cleanly alongside the regular
    packages/* members.
    """
    _make_fixture_workspace(tmp_path)

    result = subprocess.run(
        [
            "uv", "export",
            "--no-dev", "--no-hashes",
            "--no-emit-workspace",
            "--format", "requirements-txt",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"uv export failed: stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    requirements = result.stdout

    # Broader assertion (Phase 2.6 Bug 2 strengthening): catch ANY
    # workspace-relative path line, editable or not.
    bad = [
        line for line in requirements.splitlines()
        if line.startswith(("-e ./", "./"))
    ]
    assert not bad, (
        f"requirements.txt contained workspace-relative path lines: {bad}\n"
        f"Full output:\n{requirements}"
    )

    # Sanity: the third-party dep we DID declare should still be present.
    assert "annotated-types" in requirements, (
        f"expected third-party dep 'annotated-types' in output; got:\n{requirements}"
    )
