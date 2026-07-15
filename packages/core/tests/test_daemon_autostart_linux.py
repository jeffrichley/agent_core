"""Unit tests for daemon/autostart_linux.py — systemd --user unit registration."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agent_core.daemon.autostart_linux import (
    UNIT_NAME,
    build_systemd_unit,
    install_systemd_unit,
    uninstall_systemd_unit,
)


def test_unit_name_constant() -> None:
    assert UNIT_NAME == "agent-core.service"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="systemd unit is Linux-only; the golden string asserts native POSIX path rendering",
)
def test_build_systemd_unit_golden_string() -> None:
    venv_bin = Path("/home/jeff/.agent-core/.venv/bin")
    home = Path("/home/jeff/.agent-core")
    unit = build_systemd_unit(venv_bin=venv_bin, home=home)
    assert "Type=forking" in unit
    assert f"PIDFile={home}/daemon.pid" in unit
    assert f"ExecStart={venv_bin}/agent-core-daemon start --instance prod" in unit
    assert f"ExecStop={venv_bin}/agent-core-daemon stop --instance prod" in unit
    assert "Restart=always" in unit
    assert "RestartSec=5" in unit
    assert "WatchdogSec=60" in unit
    assert "WantedBy=default.target" in unit


def test_install_systemd_unit_writes_file_and_calls_systemctl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr("agent_core.daemon.autostart_linux.subprocess.run", fake_run)
    unit_path = tmp_path / "agent-core.service"
    install_systemd_unit("[Unit]\nDescription=test\n", unit_path)

    assert unit_path.exists()
    assert unit_path.read_text() == "[Unit]\nDescription=test\n"
    # daemon-reload then enable --now
    assert any("daemon-reload" in " ".join(c) for c in calls)
    assert any("enable" in c and "--now" in c for c in calls)


def test_install_systemd_unit_creates_parent_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_core.daemon.autostart_linux.subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    nested = tmp_path / "a" / "b" / "c" / "agent-core.service"
    install_systemd_unit("content", nested)
    assert nested.exists()


def test_install_systemd_unit_raises_on_systemctl_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_check(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if "daemon-reload" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr="Failed to connect to bus.")

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(
        "agent_core.daemon.autostart_linux.subprocess.run", fake_run_check
    )
    with pytest.raises(subprocess.CalledProcessError):
        install_systemd_unit("content", tmp_path / "u.service")


def test_uninstall_systemd_unit_calls_disable_and_removes_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "agent_core.daemon.autostart_linux.subprocess.run",
        lambda cmd, **kw: (calls.append(list(cmd)),
                           type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())[1],
    )
    unit_path = tmp_path / "agent-core.service"
    unit_path.write_text("content")
    uninstall_systemd_unit(unit_path)

    assert not unit_path.exists()
    assert any("disable" in c for c in calls)
    assert any("daemon-reload" in " ".join(c) for c in calls)


def test_uninstall_systemd_unit_is_idempotent_when_file_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_core.daemon.autostart_linux.subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})(),
    )
    # File does not exist — should not raise.
    uninstall_systemd_unit(tmp_path / "agent-core.service")
