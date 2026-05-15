"""Unit tests for `agent_core.daemon.install`."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.daemon.install import WorkspaceNotFoundError, find_workspace_root


def test_find_workspace_root_walks_up_to_pyproject(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(
        "[tool.uv.workspace]\nmembers = [\"packages/*\"]\n",
        encoding="utf-8",
    )
    deep = workspace / "packages" / "core" / "src" / "agent_core"
    deep.mkdir(parents=True)

    assert find_workspace_root(deep) == workspace


def test_find_workspace_root_ignores_non_workspace_pyproject(tmp_path: Path) -> None:
    """A pyproject.toml without [tool.uv.workspace] is not a workspace root."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "pyproject.toml").write_text("[project]\nname = \"x\"\n", encoding="utf-8")

    outer = tmp_path
    (outer / "pyproject.toml").write_text(
        "[tool.uv.workspace]\nmembers = [\"inner\"]\n",
        encoding="utf-8",
    )

    assert find_workspace_root(inner) == outer


def test_find_workspace_root_raises_when_absent(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceNotFoundError, match="workspace"):
        find_workspace_root(tmp_path)


def test_find_workspace_root_skips_malformed_toml(tmp_path: Path) -> None:
    """A pyproject.toml with invalid TOML syntax is skipped, not an error."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "pyproject.toml").write_text("not = [ valid toml ???", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv.workspace]\nmembers = [\"inner\"]\n", encoding="utf-8"
    )
    assert find_workspace_root(inner) == tmp_path
