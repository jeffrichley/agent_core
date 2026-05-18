"""End-to-end install against a minimal tmp workspace. Real uv subprocess.

Skipped by default (slow); run locally with:
    pytest packages/core/tests/test_daemon_install_integration.py -v -m slow
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_core.daemon.install import read_stamp, run_install

pytestmark = pytest.mark.slow


def _uv_available() -> bool:
    return shutil.which("uv") is not None


@pytest.mark.skipif(not _uv_available(), reason="uv not on PATH")
def test_run_install_creates_working_venv_against_minimal_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Minimal workspace pyproject — no torch, no agent-core. Just `iniconfig`
    # so uv has something real to install.
    # hatchling needs an explicit packages declaration because the root project
    # has no src/ directory matching "fake_ws_root" by default.
    (workspace / "pyproject.toml").write_text(
        """
[project]
name = "fake-ws-root"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = ["iniconfig"]

[tool.uv.workspace]
members = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/fake_ws_root"]
""",
        encoding="utf-8",
    )
    # Create a minimal package so hatchling can build a wheel.
    pkg_dir = workspace / "src" / "fake_ws_root"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    # Generate a lockfile so --frozen has something to install against.
    lock_result = subprocess.run(
        ["uv", "lock"], cwd=workspace, capture_output=True, text=True, check=False
    )
    assert lock_result.returncode == 0, lock_result.stderr

    home = tmp_path / "agent-core-home"
    home.mkdir()

    stamp = run_install(
        home=home,
        workspace=workspace,
        extra=None,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
    )

    # Venv exists and has a Python interpreter.
    if sys.platform == "win32":
        py = home / ".venv" / "Scripts" / "python.exe"
    else:
        py = home / ".venv" / "bin" / "python"
    assert py.exists(), f"expected interpreter at {py}"

    # iniconfig was installed.
    show = subprocess.run(
        [str(py), "-c", "import iniconfig; print(iniconfig.__name__)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert show.returncode == 0, show.stderr
    assert show.stdout.strip() == "iniconfig"

    # Stamp file exists and matches what run_install returned.
    on_disk = read_stamp(home)
    assert on_disk == stamp

    # Re-running run_install is idempotent (uv sync no-op).
    stamp2 = run_install(
        home=home,
        workspace=workspace,
        extra=None,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
    )
    # Stamp's installed_at will differ (re-stamped); installed_sha/lock_hash
    # should match the first call.
    assert stamp2.uv_lock_hash == stamp.uv_lock_hash


def _git(args: list[str], cwd: Path) -> None:
    res = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, f"git {args} failed: {res.stderr}"


def _write_workspace(ws: Path, *, with_cache_key: bool, sentinel: str) -> None:
    cache_block = (
        '\n[tool.uv]\n'
        'cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]\n'
        if with_cache_key
        else ""
    )
    (ws / "pyproject.toml").write_text(
        f"""
[project]
name = "stale-probe"
version = "0.0.0"
requires-python = ">=3.12"

[tool.uv.workspace]
members = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/stale_probe"]
{cache_block}""",
        encoding="utf-8",
    )
    pkg = ws / "src" / "stale_probe"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(
        f'SENTINEL = "{sentinel}"\n', encoding="utf-8"
    )


def _installed_sentinel(home: Path) -> str:
    py = (
        home / ".venv" / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else home / ".venv" / "bin" / "python"
    )
    out = subprocess.run(
        [str(py), "-c", "import stale_probe; print(stale_probe.SENTINEL)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.mark.skipif(not _uv_available(), reason="uv not on PATH")
@pytest.mark.parametrize(
    ("with_cache_key", "expected_after_refresh"),
    [
        pytest.param(True, "V2", id="with-cache-key-picks-up-new-code"),
        pytest.param(False, "V1", id="without-cache-key-reproduces-defect-a"),
    ],
)
def test_defect_a_source_only_change_is_picked_up(
    tmp_path: Path, with_cache_key: bool, expected_after_refresh: str
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_workspace(ws, with_cache_key=with_cache_key, sentinel="V1")
    _git(["init"], ws)
    _git(["config", "user.email", "test@example.com"], ws)
    _git(["config", "user.name", "Test"], ws)
    _git(["add", "-A"], ws)
    _git(["commit", "-m", "initial"], ws)

    lock = subprocess.run(
        ["uv", "lock"], cwd=ws, capture_output=True, text=True, check=False
    )
    assert lock.returncode == 0, lock.stderr

    home = tmp_path / "home"
    home.mkdir()
    pyver = f"{sys.version_info.major}.{sys.version_info.minor}"

    run_install(home=home, workspace=ws, extra=None, python_version=pyver)
    assert _installed_sentinel(home) == "V1"

    # Source-only change (pyproject.toml NOT touched), then commit.
    (ws / "src" / "stale_probe" / "__init__.py").write_text(
        'SENTINEL = "V2"\n', encoding="utf-8"
    )
    _git(["add", "-A"], ws)
    _git(["commit", "-m", "change sentinel to V2"], ws)

    # The "daemon refresh" re-install.
    run_install(home=home, workspace=ws, extra=None, python_version=pyver)

    # The `without-cache-key` param intentionally asserts V1 here: without the
    # git cache-key uv reuses the stale wheel (Defect A). This param is a
    # documentation/regression guard of the hazard, NOT a flaky test — do not
    # "fix" it to V2.
    assert _installed_sentinel(home) == expected_after_refresh
