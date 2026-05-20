"""Slow: real `uv build` proves git-derived versions. Skipped by default."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


def _uv() -> bool:
    try:
        subprocess.run(["uv", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _git(repo: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _make_member(repo: Path) -> Path:
    """A minimal hatchling+uv-dynamic-versioning package inside a git repo."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    pkg = repo / "src" / "demo"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[build-system]\n'
        'requires = ["hatchling", "uv-dynamic-versioning"]\n'
        'build-backend = "hatchling.build"\n\n'
        '[project]\n'
        'name = "demo"\n'
        'dynamic = ["version"]\n'
        'requires-python = ">=3.12"\n\n'
        '[tool.hatch.version]\n'
        'source = "uv-dynamic-versioning"\n\n'
        '[tool.uv-dynamic-versioning]\n'
        'fallback-version = "0.0.0"\n\n'
        '[tool.hatch.build.targets.wheel]\n'
        'packages = ["src/demo"]\n',
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _built_version(repo: Path, tmp: Path) -> str:
    out = tmp / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out)],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    wheel = next(out.glob("demo-*.whl"))
    return wheel.name.split("-")[1]


@pytest.mark.skipif(not _uv(), reason="uv not on PATH")
def test_tagged_commit_yields_exact_version(tmp_path: Path) -> None:
    repo = _make_member(tmp_path / "r")
    _git(repo, "tag", "-a", "v1.2.3", "-m", "r")
    assert _built_version(repo, tmp_path) == "1.2.3"


@pytest.mark.skipif(not _uv(), reason="uv not on PATH")
def test_untagged_commit_has_dev_version_with_sha(tmp_path: Path) -> None:
    repo = _make_member(tmp_path / "r")
    _git(repo, "tag", "-a", "v1.2.3", "-m", "r")
    (repo / "src" / "demo" / "x.py").write_text("y = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "next")
    v = _built_version(repo, tmp_path)
    # uv-dynamic-versioning default (no bump): 1.2.3.post<n>.dev0+<sha>
    assert v != "1.2.3" and "1.2.3" in v and "dev" in v


@pytest.mark.skipif(not _uv(), reason="uv not on PATH")
def test_pepper_cutover_tags_are_ignored(tmp_path: Path) -> None:
    repo = _make_member(tmp_path / "r")
    _git(repo, "tag", "-a", "pepper-cutover-ready-2026-05-06", "-m", "p")
    v = _built_version(repo, tmp_path)
    assert "pepper" not in v and "2026" not in v


@pytest.mark.skipif(not _uv(), reason="uv not on PATH")
def test_no_git_uses_fallback(tmp_path: Path) -> None:
    repo = _make_member(tmp_path / "r")
    import shutil
    import stat

    def _force_remove(func, path, exc_info):  # type: ignore[no-untyped-def]
        # On Windows, .git objects are read-only; make writable before retry.
        os.chmod(path, stat.S_IWRITE)
        func(path)

    shutil.rmtree(repo / ".git", onerror=_force_remove)
    assert _built_version(repo, tmp_path) == "0.0.0"
