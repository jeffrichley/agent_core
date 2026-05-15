"""Unit tests for `agent_core.daemon.install`."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.daemon.install import (
    STAMP_FILENAME,
    InstallStamp,
    WorkspaceNotFoundError,
    build_uv_sync_command,
    find_workspace_root,
    read_stamp,
    write_stamp,
)


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


def test_write_then_read_stamp_round_trips(tmp_path: Path) -> None:
    stamp = InstallStamp(
        installed_at="2026-05-15T19:31:04Z",
        installed_sha="42713d7",
        python_version="3.12.5",
        extra="cu130",
        uv_lock_hash="sha256:abc",
    )
    write_stamp(tmp_path, stamp)

    assert (tmp_path / STAMP_FILENAME).exists()
    assert read_stamp(tmp_path) == stamp


def test_read_stamp_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_stamp(tmp_path) is None


def test_read_stamp_returns_none_when_corrupt(tmp_path: Path) -> None:
    (tmp_path / STAMP_FILENAME).write_text("not json", encoding="utf-8")
    assert read_stamp(tmp_path) is None


def test_read_stamp_returns_none_when_missing_fields(tmp_path: Path) -> None:
    (tmp_path / STAMP_FILENAME).write_text(
        '{"installed_at": "2026-05-15T19:31:04Z"}', encoding="utf-8"
    )
    assert read_stamp(tmp_path) is None


def test_build_uv_sync_command_basic(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    cmd, env_overrides = build_uv_sync_command(venv=venv, extra=None)
    assert cmd == ["uv", "sync", "--frozen", "--no-editable", "--no-dev"]
    assert env_overrides == {"UV_PROJECT_ENVIRONMENT": str(venv)}


def test_build_uv_sync_command_with_extra(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    cmd, env_overrides = build_uv_sync_command(venv=venv, extra="cu130")
    assert cmd == [
        "uv",
        "sync",
        "--frozen",
        "--no-editable",
        "--no-dev",
        "--extra",
        "cu130",
    ]
    assert env_overrides == {"UV_PROJECT_ENVIRONMENT": str(venv)}
