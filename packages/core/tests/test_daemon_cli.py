"""Tests for `agent-core daemon` CLI."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import typer
from typer.testing import CliRunner

from agent_core.daemon.cli import (
    _daemon_python,
)
from agent_core.daemon.cli import (
    app as daemon_app,
)
from agent_core.daemon.supervisor import is_alive, read_pid

runner = CliRunner()


def test_status_when_not_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0
    assert "not running" in result.stdout.lower()


def test_stop_when_not_running_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["stop"])
    assert result.exit_code == 0


def test_start_refuses_without_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["start"])
    assert result.exit_code == 1
    assert "agent_core.yaml" in result.stdout


def test_status_with_stale_pid_reports_not_running_and_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("999999")  # very unlikely to be alive
    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0
    assert "not running" in result.stdout.lower()
    assert not pid_file.exists()  # stale file cleaned


def test_start_writes_pid_file_and_stop_kills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """End-to-end: write a config, daemon start, daemon stop. Real subprocess."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
endpoints:
  - type: builtin.stub
    name: stub
""",
        encoding="utf-8",
    )

    start_res = runner.invoke(daemon_app, ["start"])
    assert start_res.exit_code == 0
    pid_file = tmp_path / "daemon.pid"
    assert pid_file.exists()
    pid = read_pid(pid_file)
    assert pid is not None

    # Give the daemon a moment to come up.
    for _ in range(40):
        if is_alive(pid):
            break
        time.sleep(0.1)
    assert is_alive(pid) is True

    # Second start refuses.
    again = runner.invoke(daemon_app, ["start"])
    assert again.exit_code == 1
    assert str(pid) in again.stdout

    # Stop kills it cleanly.
    stop_res = runner.invoke(daemon_app, ["stop"])
    assert stop_res.exit_code == 0
    for _ in range(40):
        if not is_alive(pid):
            break
        time.sleep(0.1)
    assert is_alive(pid) is False
    assert not pid_file.exists()


def test_daemon_python_returns_sys_executable_when_venv_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    # No ~/.agent-core/.venv/ exists in tmp_path.
    assert _daemon_python() == sys.executable


def test_daemon_python_returns_daemon_venv_python_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    if sys.platform == "win32":
        venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("# placeholder")

    assert _daemon_python() == str(venv_python)


def test_install_refuses_when_daemon_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(str(os.getpid()))  # current process is alive
    result = runner.invoke(daemon_app, ["install"])
    assert result.exit_code == 1
    assert "currently running" in result.stdout.lower()
    assert "refresh" in result.stdout.lower()


def test_install_invokes_run_install_with_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    captured: dict = {}

    fake_stamp = MagicMock(
        installed_sha="abc1234",
        extra="cu130",
        python_version="3.12",
        installed_at="2026-05-15T19:31:04Z",
    )

    def fake_run_install(*, home, workspace, extra, python_version):
        captured["home"] = home
        captured["workspace"] = workspace
        captured["extra"] = extra
        captured["python_version"] = python_version
        return fake_stamp

    monkeypatch.setattr("agent_core.daemon.cli.run_install", fake_run_install)
    monkeypatch.setattr(
        "agent_core.daemon.cli.find_workspace_root",
        lambda _start: tmp_path / "fake-workspace",
    )

    result = runner.invoke(daemon_app, ["install", "--extra", "cu130"])
    assert result.exit_code == 0, result.stdout
    assert captured["home"] == tmp_path
    assert captured["workspace"] == tmp_path / "fake-workspace"
    assert captured["extra"] == "cu130"
    assert captured["python_version"] == "3.12"
    assert "abc1234" in result.stdout  # stamp sha echoed


def test_install_reports_workspace_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    def raise_not_found(_start):
        from agent_core.daemon.install import WorkspaceNotFoundError

        raise WorkspaceNotFoundError("can't find workspace")

    monkeypatch.setattr("agent_core.daemon.cli.find_workspace_root", raise_not_found)

    result = runner.invoke(daemon_app, ["install"])
    assert result.exit_code == 1
    assert "workspace" in result.stdout.lower()


def test_install_reports_uv_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    def fake_run_install(*, home, workspace, extra, python_version):
        from agent_core.daemon.install import UvNotFoundError

        raise UvNotFoundError("uv not found on PATH")

    monkeypatch.setattr("agent_core.daemon.cli.run_install", fake_run_install)
    monkeypatch.setattr(
        "agent_core.daemon.cli.find_workspace_root",
        lambda _start: tmp_path / "fake-workspace",
    )

    result = runner.invoke(daemon_app, ["install"])
    assert result.exit_code == 1
    assert "uv not found" in result.stdout.lower()


def test_install_reports_uv_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uv subprocess failure surfaces returncode + stderr cleanly."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    def fake_run_install(*, home, workspace, extra, python_version):
        import subprocess

        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=["uv", "sync", "--frozen"],
            output="",
            stderr="error: failed to resolve packages",
        )

    monkeypatch.setattr("agent_core.daemon.cli.run_install", fake_run_install)
    monkeypatch.setattr(
        "agent_core.daemon.cli.find_workspace_root",
        lambda _start: tmp_path / "fake-workspace",
    )

    result = runner.invoke(daemon_app, ["install"])
    assert result.exit_code == 1
    assert "uv exited with code 1" in result.stdout
    assert "failed to resolve" in result.stdout


def test_refresh_calls_stop_install_start_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    order: list[str] = []

    def fake_stop() -> None:
        order.append("stop")

    def fake_install(extra: str | None = None, python_version: str = "3.12") -> None:
        order.append(f"install:extra={extra}")

    def fake_start() -> None:
        order.append("start")

    monkeypatch.setattr("agent_core.daemon.cli.stop", fake_stop)
    monkeypatch.setattr("agent_core.daemon.cli.install", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start", fake_start)

    result = runner.invoke(daemon_app, ["refresh", "--extra", "cu130"])
    assert result.exit_code == 0, result.stdout
    assert order == ["stop", "install:extra=cu130", "start"]


def test_refresh_aborts_start_when_install_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    order: list[str] = []

    def fake_stop() -> None:
        order.append("stop")

    def fake_install(extra: str | None = None, python_version: str = "3.12") -> None:
        order.append("install")
        raise typer.Exit(code=1)

    def fake_start() -> None:
        order.append("start")  # must not be called

    monkeypatch.setattr("agent_core.daemon.cli.stop", fake_stop)
    monkeypatch.setattr("agent_core.daemon.cli.install", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start", fake_start)

    result = runner.invoke(daemon_app, ["refresh"])
    assert result.exit_code != 0
    assert "start" not in order
    assert order == ["stop", "install"]


def test_refresh_reuses_stamped_extra_when_no_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When --extra is omitted, refresh passes the stamped extra to install."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    # Write a stamp with extra='cu130'
    from agent_core.daemon.install import InstallStamp, write_stamp

    write_stamp(
        tmp_path,
        InstallStamp(
            installed_at="2026-05-15T19:31:04Z",
            installed_sha="abc1234",
            python_version="3.12",
            extra="cu130",
            uv_lock_hash="sha256:abc",
        ),
    )

    captured: dict = {}

    def fake_install(extra: str | None = None, python_version: str = "3.12") -> None:
        captured["extra"] = extra

    monkeypatch.setattr("agent_core.daemon.cli.stop", lambda: None)
    monkeypatch.setattr("agent_core.daemon.cli.install", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start", lambda: None)

    result = runner.invoke(daemon_app, ["refresh"])
    assert result.exit_code == 0
    assert captured["extra"] == "cu130"


def test_status_shows_fallback_warning_when_no_daemon_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _daemon_python falls back to sys.executable, status warns."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    # Daemon is "running" — write a PID for the current process so is_alive=True
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))

    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0
    assert "fallback" in result.stdout.lower()
    assert "vulnerable to uv sync" in result.stdout.lower()


def test_status_no_warning_when_daemon_venv_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))

    if sys.platform == "win32":
        venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("# placeholder")

    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0
    assert "fallback" not in result.stdout.lower()


def test_status_shows_stamp_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_core.daemon.install import InstallStamp, write_stamp

    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))
    write_stamp(
        tmp_path,
        InstallStamp(
            installed_at="2026-05-15T19:31:04Z",
            installed_sha="abc1234",
            python_version="3.12",
            extra="cu130",
            uv_lock_hash="sha256:deadbeef",
        ),
    )

    def _skip_workspace(_start):
        from agent_core.daemon.install import WorkspaceNotFoundError

        raise WorkspaceNotFoundError("hermetic test: skip drift check")

    monkeypatch.setattr(
        "agent_core.daemon.cli.find_workspace_root", _skip_workspace
    )

    result = runner.invoke(daemon_app, ["status"])
    assert "abc1234" in result.stdout
    assert "2026-05-15" in result.stdout


def test_status_flags_lock_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_core.daemon.install import InstallStamp, write_stamp

    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))
    # Stamp says the lock was hash "stale-hash"; workspace lock has different content.
    write_stamp(
        tmp_path,
        InstallStamp(
            installed_at="2026-05-15T19:31:04Z",
            installed_sha="abc1234",
            python_version="3.12",
            extra=None,
            uv_lock_hash="sha256:STALE",
        ),
    )

    # Fake workspace with a uv.lock whose hash is different.
    fake_ws = tmp_path / "fake-workspace"
    fake_ws.mkdir()
    (fake_ws / "uv.lock").write_text("# new content\n", encoding="utf-8")
    monkeypatch.setattr(
        "agent_core.daemon.cli.find_workspace_root", lambda _start: fake_ws
    )

    result = runner.invoke(daemon_app, ["status"])
    assert "stale" in result.stdout.lower() or "refresh" in result.stdout.lower()


def test_status_handles_missing_workspace_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """status must not crash if workspace discovery fails — drift check is optional."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))

    def raise_not_found(_start):
        from agent_core.daemon.install import WorkspaceNotFoundError

        raise WorkspaceNotFoundError("no workspace")

    monkeypatch.setattr("agent_core.daemon.cli.find_workspace_root", raise_not_found)

    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0  # still succeeds
