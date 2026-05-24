"""Test that `uv export` (as invoked from release.yml) produces a
requirements.txt that does NOT contain editable workspace-member entries.

Phase 2.6 Bug 2: the bug Phase 3.5's test instance surfaced — the
generated requirements.txt contained lines like `-e ./packages/...` that
don't resolve on the daemon side (where the workspace doesn't exist).
The workspace packages ship via wheels; requirements.txt should carry
only third-party deps.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _make_fixture_workspace(root: Path) -> None:
    """Build a minimal uv workspace under `root` with one member package."""
    (root / "pyproject.toml").write_text(
        '[project]\n'
        'name = "fixture-root"\n'
        'version = "0.0.0"\n'
        'requires-python = ">=3.12"\n'
        'dependencies = [\n'
        '  "fixture-member",\n'
        '  "annotated-types>=0.7.0",\n'  # one real third-party dep for control
        ']\n'
        '\n'
        '[tool.uv.workspace]\n'
        'members = ["packages/fixture-member"]\n'
        '\n'
        '[tool.uv.sources]\n'
        'fixture-member = { workspace = true }\n'
        '\n'
        '[build-system]\n'
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
    )
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
    src = member / "src" / "fixture_member"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("__version__ = '0.0.0'\n")


@pytest.mark.skipif(
    shutil.which("uv") is None, reason="uv binary not on PATH"
)
def test_uv_export_no_emit_workspace_excludes_member_packages(tmp_path):
    """`uv export --no-emit-workspace` against a fixture workspace must
    produce a requirements-txt that does NOT contain `-e ./packages/...`
    (or any other editable reference to workspace members)."""
    _make_fixture_workspace(tmp_path)

    result = subprocess.run(
        [
            "uv", "export",
            "--frozen", "--no-dev", "--no-hashes",
            "--no-emit-workspace",
            "--format", "requirements-txt",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    # If --frozen complains about a missing lockfile in a fresh fixture,
    # drop --frozen and re-run; this fixture doesn't ship with a lockfile.
    if result.returncode != 0 and "lock" in (result.stderr or "").lower():
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

    # Core assertion: no editable lines pointing at workspace members.
    bad = [line for line in requirements.splitlines() if line.startswith("-e ./packages/")]
    assert not bad, (
        f"requirements.txt contained workspace-member editable lines: {bad}\n"
        f"Full output:\n{requirements}"
    )

    # Sanity: the third-party dep we DID declare should still be present.
    assert "annotated-types" in requirements, (
        f"expected third-party dep 'annotated-types' in output; got:\n{requirements}"
    )
