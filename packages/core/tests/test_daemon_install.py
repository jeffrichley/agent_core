"""Unit tests for `agent_core.daemon.install`."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_core.daemon.install import (
    STAMP_FILENAME,
    InstallStamp,
    UvNotFoundError,
    WorkspaceNotFoundError,
    build_uv_sync_command,
    find_workspace_root,
    read_stamp,
    run_install,
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
        installed_version="0.1.0",
        python_version="3.12.5",
        extra="cu130",
        release_tag="v0.1.0",
    )
    write_stamp(tmp_path, stamp)

    assert (tmp_path / STAMP_FILENAME).exists()
    assert read_stamp(tmp_path) == stamp


def test_read_stamp_old_phase2_schema_back_compat(tmp_path: Path) -> None:
    """Stamps written by Phase 2 lacked installed_version/release_tag and
    carried uv_lock_hash. read_stamp must accept them: drop the obsolete
    field, default the new ones."""
    import json as _json
    (tmp_path / STAMP_FILENAME).write_text(
        _json.dumps({
            "installed_at": "2026-05-20T10:00:00Z",
            "installed_sha": "abc1234",
            "python_version": "3.12",
            "extra": "cu130",
            "uv_lock_hash": "sha256:legacy",
        }) + "\n",
        encoding="utf-8",
    )

    stamp = read_stamp(tmp_path)
    assert stamp is not None
    assert stamp.installed_at == "2026-05-20T10:00:00Z"
    assert stamp.installed_sha == "abc1234"
    assert stamp.installed_version == "unknown"   # new field defaulted
    assert stamp.release_tag is None              # new field defaulted
    # uv_lock_hash silently dropped — no attribute on the dataclass


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


# ---------------------------------------------------------------------------
# Task 5: run_install() tests
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(
        "[tool.uv.workspace]\nmembers = [\"packages/*\"]\n", encoding="utf-8"
    )
    (workspace / "uv.lock").write_text("# fake lock\n", encoding="utf-8")
    return workspace


def test_run_install_invokes_uv_venv_then_uv_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr("agent_core.daemon.install.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent_core.daemon.install._git_head_sha", lambda _ws: "abc1234"
    )

    run_install(home=home, workspace=workspace, extra="cu130", python_version="3.12")

    # First call: uv venv ... --python 3.12 --clear
    assert calls[0][:2] == ["uv", "venv"]
    assert "--python" in calls[0]
    assert "3.12" in calls[0]
    assert "--clear" in calls[0]
    # Second call: uv sync --frozen --no-editable --no-dev --extra cu130
    assert calls[1] == [
        "uv",
        "sync",
        "--frozen",
        "--no-editable",
        "--no-dev",
        "--extra",
        "cu130",
    ]


def test_run_install_writes_stamp_with_lock_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setattr(
        "agent_core.daemon.install.subprocess.run",
        lambda cmd, **kwargs: MagicMock(returncode=0),
    )
    monkeypatch.setattr(
        "agent_core.daemon.install._git_head_sha", lambda _ws: "abc1234"
    )

    run_install(home=home, workspace=workspace, extra=None, python_version="3.12")

    stamp = read_stamp(home)
    assert stamp is not None
    assert stamp.installed_sha == "abc1234"
    assert stamp.extra is None
    # New (Phase 2.5) stamp fields — source installs use stub values
    assert stamp.installed_version == "unknown"
    assert stamp.release_tag is None


def test_run_install_raises_uv_not_found_on_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "uv")

    monkeypatch.setattr("agent_core.daemon.install.subprocess.run", fake_run)

    with pytest.raises(UvNotFoundError, match="uv not found on PATH"):
        run_install(home=home, workspace=workspace, extra=None, python_version="3.12")


def test_run_install_raises_on_uv_venv_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uv venv non-zero exit (e.g., requested Python version unavailable) raises."""
    workspace = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["uv", "venv"]:
            return MagicMock(returncode=1, stderr="python 99.99 not found")
        return MagicMock(returncode=0)

    monkeypatch.setattr("agent_core.daemon.install.subprocess.run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        run_install(home=home, workspace=workspace, extra=None, python_version="99.99")


def test_run_install_raises_on_uv_sync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    def fake_run(cmd, **kwargs):
        # `uv venv` succeeds, `uv sync` fails.
        if cmd[:2] == ["uv", "sync"]:
            return MagicMock(returncode=1, stderr="resolution error")
        return MagicMock(returncode=0)

    monkeypatch.setattr("agent_core.daemon.install.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent_core.daemon.install._git_head_sha", lambda _ws: "abc1234"
    )

    with pytest.raises(subprocess.CalledProcessError):
        run_install(home=home, workspace=workspace, extra=None, python_version="3.12")
