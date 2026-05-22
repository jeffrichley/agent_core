"""Unit tests for daemon/autostart.py — Task Scheduler registration."""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from agent_core.daemon.autostart import (
    TASK_NAME,
    build_autostart_task,
    install_autostart,
    uninstall_autostart,
)

# Task Scheduler XML namespace.
_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


# ---- build_autostart_task (pure) -------------------------------------------

def test_task_name_constant() -> None:
    assert TASK_NAME == "agent-core-daemon-prod"


def test_build_autostart_task_is_well_formed_xml() -> None:
    xml = build_autostart_task(
        agent_core_exe=Path(r"C:\Users\x\.agent-core\.venv\Scripts\agent-core.exe"),
        account="x",
    )
    # Must parse without error.
    ET.fromstring(xml)


def test_build_autostart_task_action_command_and_args() -> None:
    exe = Path(r"C:\Users\x\.agent-core\.venv\Scripts\agent-core.exe")
    xml = build_autostart_task(agent_core_exe=exe, account="x")
    root = ET.fromstring(xml)
    command = root.find(".//t:Actions/t:Exec/t:Command", _NS)
    arguments = root.find(".//t:Actions/t:Exec/t:Arguments", _NS)
    assert command is not None and command.text == str(exe)
    assert arguments is not None
    assert arguments.text == "daemon start --instance prod"


def test_build_autostart_task_has_logon_trigger_for_account() -> None:
    xml = build_autostart_task(
        agent_core_exe=Path(r"C:\x\agent-core.exe"), account="jeffr"
    )
    root = ET.fromstring(xml)
    trigger = root.find(".//t:Triggers/t:LogonTrigger", _NS)
    assert trigger is not None
    user = trigger.find("t:UserId", _NS)
    assert user is not None and user.text == "jeffr"


def test_build_autostart_task_settings() -> None:
    xml = build_autostart_task(
        agent_core_exe=Path(r"C:\x\agent-core.exe"), account="x"
    )
    root = ET.fromstring(xml)
    settings = root.find("t:Settings", _NS)
    assert settings is not None
    assert settings.findtext("t:MultipleInstancesPolicy", namespaces=_NS) == "IgnoreNew"
    assert settings.findtext("t:StartWhenAvailable", namespaces=_NS) == "true"
    assert settings.findtext("t:ExecutionTimeLimit", namespaces=_NS) == "PT0S"
    restart = settings.find("t:RestartOnFailure", _NS)
    assert restart is not None
    assert restart.findtext("t:Interval", namespaces=_NS) == "PT1M"
    assert restart.findtext("t:Count", namespaces=_NS) == "3"


def test_build_autostart_task_principal_is_least_privilege() -> None:
    xml = build_autostart_task(
        agent_core_exe=Path(r"C:\x\agent-core.exe"), account="jeffr"
    )
    root = ET.fromstring(xml)
    principal = root.find(".//t:Principals/t:Principal", _NS)
    assert principal is not None
    assert principal.findtext("t:RunLevel", namespaces=_NS) == "LeastPrivilege"
    assert principal.findtext("t:UserId", namespaces=_NS) == "jeffr"


# ---- install_autostart / uninstall_autostart (impure, faked subprocess) ----

def test_install_autostart_invokes_schtasks_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(cmd)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr("agent_core.daemon.autostart.subprocess.run", fake_run)

    install_autostart("<Task></Task>")

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "schtasks"
    assert "/create" in cmd
    assert "/tn" in cmd and TASK_NAME in cmd
    assert "/xml" in cmd
    assert "/f" in cmd


def test_install_autostart_raises_on_schtasks_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        class _R:
            returncode = 1
            stdout = ""
            stderr = "ERROR: Access is denied."
        return _R()

    monkeypatch.setattr("agent_core.daemon.autostart.subprocess.run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        install_autostart("<Task></Task>")


def test_uninstall_autostart_returns_true_on_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        class _R:
            returncode = 0
            stdout = "SUCCESS"
            stderr = ""
        return _R()

    monkeypatch.setattr("agent_core.daemon.autostart.subprocess.run", fake_run)
    assert uninstall_autostart() is True


def test_uninstall_autostart_returns_false_when_task_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        class _R:
            returncode = 1
            stdout = ""
            stderr = "ERROR: The system cannot find the file specified."
        return _R()

    monkeypatch.setattr("agent_core.daemon.autostart.subprocess.run", fake_run)
    assert uninstall_autostart() is False
