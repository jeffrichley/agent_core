"""Unit tests for `agent_core.daemon.install` — stamp read/write + workspace helper.

Phase 2.5 removed source-install code (run_install, build_uv_sync_command,
compute_lock_hash, _git_head_sha) and their tests. find_workspace_root remains
as a utility used by guard tests, so its tests stay.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import pytest

from agent_core.daemon.install import (
    STAMP_FILENAME,
    InstallStamp,
    WorkspaceNotFoundError,
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


def test_read_stamp_returns_none_when_missing_required_fields(tmp_path: Path) -> None:
    (tmp_path / STAMP_FILENAME).write_text(
        '{"installed_at": "2026-05-15T19:31:04Z"}', encoding="utf-8"
    )
    assert read_stamp(tmp_path) is None
