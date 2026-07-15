"""Unit tests for daemon/autostart_macos.py — launchd LaunchAgent registration."""
from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from agent_core.daemon.autostart_macos import (
    LABEL,
    build_launchd_plist,
    install_launchd_plist,
    uninstall_launchd_plist,
)


def test_label_constant() -> None:
    assert LABEL == "com.jeffrichley.agent-core.daemon.prod"


def test_build_launchd_plist_is_valid_xml() -> None:
    venv_bin = Path("/Users/jeff/.agent-core/.venv/bin")
    home = Path("/Users/jeff/.agent-core")
    xml = build_launchd_plist(venv_bin=venv_bin, home=home, label=LABEL, uid=501)
    plist = plistlib.loads(xml.encode())
    assert plist["Label"] == LABEL
    assert plist["KeepAlive"] is True
    assert plist["RunAtLoad"] is True
    args = plist["ProgramArguments"]
    assert str(venv_bin / "agent-core-daemon") in args
    assert "start" in args
    assert "--instance" in args
    assert "prod" in args
    assert plist["StandardOutPath"] == str(home / "daemon.log")
    assert plist["StandardErrorPath"] == str(home / "daemon.log")


def test_install_launchd_plist_writes_file_and_calls_launchctl(
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

    monkeypatch.setattr("agent_core.daemon.autostart_macos.subprocess.run", fake_run)
    plist_path = tmp_path / f"{LABEL}.plist"
    install_launchd_plist(plist_path, "<plist/>", uid=501, label=LABEL)

    assert plist_path.exists()
    assert plist_path.read_text() == "<plist/>"
    # bootout (idempotent) then bootstrap
    assert any("bootout" in c for c in calls)
    assert any("bootstrap" in c for c in calls)
    # bootstrap must reference the plist path
    bootstrap_call = next(c for c in calls if "bootstrap" in c)
    assert str(plist_path) in bootstrap_call


def test_install_launchd_plist_ignores_bootout_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        rc = 1 if "bootout" in cmd else 0

        class _R:
            returncode = rc
            stdout = ""
            stderr = "Could not find service"
        return _R()

    monkeypatch.setattr("agent_core.daemon.autostart_macos.subprocess.run", fake_run)
    # Should not raise — bootout errors are suppressed.
    install_launchd_plist(tmp_path / "p.plist", "<plist/>", uid=501, label=LABEL)


def test_install_launchd_plist_raises_on_bootstrap_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if "bootstrap" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr="Bootstrap failed.")

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr("agent_core.daemon.autostart_macos.subprocess.run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        install_launchd_plist(tmp_path / "p.plist", "<plist/>", uid=501, label=LABEL)


def test_uninstall_launchd_plist_returns_true_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_core.daemon.autostart_macos.subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    plist_path = tmp_path / "p.plist"
    plist_path.write_text("<plist/>")
    result = uninstall_launchd_plist(plist_path, uid=501, label=LABEL)
    assert result is True
    assert not plist_path.exists()


def test_uninstall_launchd_plist_returns_false_when_service_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_core.daemon.autostart_macos.subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})(),
    )
    result = uninstall_launchd_plist(tmp_path / "absent.plist", uid=501, label=LABEL)
    assert result is False


def test_uninstall_launchd_plist_removes_file_even_if_bootout_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_core.daemon.autostart_macos.subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})(),
    )
    plist_path = tmp_path / "p.plist"
    plist_path.write_text("<plist/>")
    uninstall_launchd_plist(plist_path, uid=501, label=LABEL)
    assert not plist_path.exists()
