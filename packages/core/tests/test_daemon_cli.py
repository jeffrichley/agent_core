"""Tests for `agent-core daemon` CLI."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

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


def test_daemon_python_prod_falls_back_to_sys_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    from agent_core.daemon.instance import Instance

    # No prod .venv exists in tmp_path -> fallback to sys.executable.
    assert _daemon_python(Instance.PROD, tmp_path) == sys.executable


def test_daemon_python_prod_uses_prod_venv_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    from agent_core.daemon.instance import Instance

    if sys.platform == "win32":
        venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("# placeholder")

    assert _daemon_python(Instance.PROD, tmp_path) == str(venv_python)


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


def test_refresh_calls_stop_install_start_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    order: list[str] = []

    def fake_stop(instance: str | None = None) -> None:
        order.append("stop")

    def fake_install(instance: str | None = None, release: str | None = None) -> None:
        order.append(f"install:release={release}")

    def fake_start(instance: str | None = None) -> None:
        order.append("start")

    monkeypatch.setattr("agent_core.daemon.cli.stop", fake_stop)
    monkeypatch.setattr("agent_core.daemon.cli.install", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start", fake_start)

    result = runner.invoke(daemon_app, ["refresh", "--release", "v0.1.0"])
    assert result.exit_code == 0, result.stdout
    assert order == ["stop", "install:release=v0.1.0", "start"]


def test_refresh_aborts_start_when_install_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    order: list[str] = []

    def fake_stop(instance: str | None = None) -> None:
        order.append("stop")

    def fake_install(instance: str | None = None, release: str | None = None) -> None:
        order.append("install")
        raise typer.Exit(code=1)

    def fake_start(instance: str | None = None) -> None:
        order.append("start")  # must not be called

    monkeypatch.setattr("agent_core.daemon.cli.stop", fake_stop)
    monkeypatch.setattr("agent_core.daemon.cli.install", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start", fake_start)

    result = runner.invoke(daemon_app, ["refresh"])
    assert result.exit_code != 0
    assert "start" not in order
    assert order == ["stop", "install"]


def test_install_release_orchestrates_full_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install --release vX.Y.Z fetches, downloads, installs, and stamps."""
    import json
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    fake_release_json = json.dumps({
        "tag_name": "v0.2.0",
        "assets": [
            {"name": "agent_core-0.2.0-py3-none-any.whl",
             "browser_download_url": "https://example/agent_core-0.2.0.whl"},
            {"name": "requirements.txt",
             "browser_download_url": "https://example/requirements.txt"},
        ],
    }).encode("utf-8")

    calls: list[list[str]] = []

    def fake_fetcher(url: str) -> bytes:
        if url.endswith("/releases/tags/v0.2.0"):
            return fake_release_json
        if url.endswith(".whl"):
            return b"FAKE_WHEEL_BYTES"
        if url.endswith("/requirements.txt"):
            return b"# pinned\ntorch==2.12.0+cu130\n"
        raise RuntimeError(f"unexpected URL: {url}")

    def fake_subprocess_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(cmd)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr("agent_core.daemon.release._default_fetcher", fake_fetcher)
    monkeypatch.setattr("agent_core.daemon.release.subprocess.run", fake_subprocess_run)

    result = runner.invoke(daemon_app, ["install", "--release", "v0.2.0"])

    # At least 3 uv invocations (uv venv, uv pip install --requirement, uv pip install --no-deps)
    assert result.exit_code == 0, result.stdout
    uv_calls = [c for c in calls if c and c[0] == "uv"]
    assert len(uv_calls) >= 2, f"expected at least 2 uv calls, got: {uv_calls}"
    assert any("--requirement" in c for c in uv_calls), "requirements.txt install missing"
    assert any("--no-deps" in c for c in uv_calls), "wheel surgical install missing"
    # Stamp written with the right version + tag
    stamp_text = (tmp_path / ".daemon-install-stamp.json").read_text()
    stamp = json.loads(stamp_text)
    assert stamp["installed_version"] == "0.2.0"
    assert stamp["release_tag"] == "v0.2.0"


# Phase 3 removed the status "fallback" warning — status is now factual
# only. The B2 false-positive it guarded against is moot.


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
            installed_version="0.1.0",
            python_version="3.12",
            extra="cu130",
            release_tag="v0.1.0",
        ),
    )

    result = runner.invoke(daemon_app, ["status"])
    assert "abc1234" in result.stdout
    assert "2026-05-15" in result.stdout


# test_status_flags_lock_drift removed in Phase 2.5: lock-drift check is gone
# (the daemon is no longer source-installed from a workspace lock; releases
# carry their own pinned requirements.txt).

# test_status_handles_missing_workspace_gracefully removed in Phase 2.5:
# status no longer touches workspace at all (no lock-drift check, no
# find_workspace_root call from cli.py status command).


def test_install_dev_instance_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`install --instance dev` must error — dev is editable, not installed."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["install", "--instance", "dev"])
    assert result.exit_code == 1
    assert "not installed" in result.stdout.lower()


def test_refresh_dev_is_stop_then_start_no_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`refresh --instance dev` bounces (stop + start), never install."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    order: list[str] = []

    def fake_stop(instance: str | None = None) -> None:
        order.append("stop")

    def fake_install(instance: str | None = None, release: str | None = None) -> None:
        order.append("install")  # must NOT be called for dev

    def fake_start(instance: str | None = None) -> None:
        order.append("start")

    monkeypatch.setattr("agent_core.daemon.cli.stop", fake_stop)
    monkeypatch.setattr("agent_core.daemon.cli.install", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start", fake_start)

    result = runner.invoke(daemon_app, ["refresh", "--instance", "dev"])
    assert result.exit_code == 0, result.stdout
    assert order == ["stop", "start"]
    assert "install" not in order


def test_init_writes_config_and_refuses_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`init` scaffolds a config and won't overwrite without --force."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    first = runner.invoke(daemon_app, ["init"])
    assert first.exit_code == 0, first.stdout
    cfg = tmp_path / "agent_core.yaml"
    assert cfg.exists()

    # Second init without --force is refused.
    second = runner.invoke(daemon_app, ["init"])
    assert second.exit_code == 1
    assert "already exists" in second.stdout.lower()

    # With --force it succeeds.
    forced = runner.invoke(daemon_app, ["init", "--force"])
    assert forced.exit_code == 0, forced.stdout


def test_unknown_instance_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["status", "--instance", "staging"])
    assert result.exit_code == 1
    assert "unknown instance" in result.stdout.lower()


def test_start_without_config_points_at_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start with no config tells you to run `daemon init`."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["start"])
    assert result.exit_code == 1
    assert "daemon init" in result.stdout
