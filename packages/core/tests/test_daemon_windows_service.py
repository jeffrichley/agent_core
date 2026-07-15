"""Unit tests for daemon/windows_service.py — Windows Service registration."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_core.daemon.windows_service import (
    RESET_PERIOD_INFINITE,
    SC_ACTION_RESTART,
    SERVICE_NAME,
    build_failure_actions,
    install_windows_service,
    uninstall_windows_service,
)

# ---------------------------------------------------------------------------
# Pure tests (platform-agnostic, no win32 imports needed)
# ---------------------------------------------------------------------------

def test_service_name_constant() -> None:
    assert SERVICE_NAME == "AgentCoreProdDaemon"


def test_build_failure_actions_has_three_entries() -> None:
    fa = build_failure_actions()
    assert len(fa["Actions"]) == 3


def test_build_failure_actions_all_restart_type() -> None:
    fa = build_failure_actions()
    for action_type, _ in fa["Actions"]:
        assert action_type == SC_ACTION_RESTART  # == 1


def test_build_failure_actions_all_zero_delay() -> None:
    fa = build_failure_actions()
    for _, delay_ms in fa["Actions"]:
        assert delay_ms == 0


def test_build_failure_actions_infinite_reset_period() -> None:
    fa = build_failure_actions()
    assert fa["ResetPeriod"] == RESET_PERIOD_INFINITE  # == 0xFFFF_FFFF


# ---------------------------------------------------------------------------
# Helper: build a minimal win32service mock
# ---------------------------------------------------------------------------

def _make_w32() -> MagicMock:
    m = MagicMock()
    m.SC_MANAGER_ALL_ACCESS = 0xF003F
    m.SERVICE_ALL_ACCESS = 0xF01FF
    m.SERVICE_WIN32_OWN_PROCESS = 0x10
    m.SERVICE_AUTO_START = 0x2
    m.SERVICE_ERROR_NORMAL = 0x1
    m.SERVICE_CONFIG_FAILURE_ACTIONS = 0x3
    m.error = Exception  # exception type caught in guards
    m.OpenService.side_effect = Exception("not found")  # default: not found
    return m


# ---------------------------------------------------------------------------
# Mocked-SCM tests (run on any OS via _win32service injection)
# ---------------------------------------------------------------------------

def test_install_service_creates_service_with_correct_name() -> None:
    w32 = _make_w32()
    install_windows_service(
        venv_python=Path(r"C:\Users\x\.agent-core\.venv\Scripts\python.exe"),
        account="x",
        password="secret",
        _win32service=w32,
    )
    # CreateService positional arg 1 is service name
    assert w32.CreateService.call_args.args[1] == SERVICE_NAME


def test_install_service_sets_failure_actions() -> None:
    w32 = _make_w32()
    install_windows_service(
        venv_python=Path(r"C:\x\python.exe"),
        account="x",
        password="p",
        _win32service=w32,
    )
    w32.ChangeServiceConfig2.assert_called_once()
    _, config_type, config_value = w32.ChangeServiceConfig2.call_args.args
    assert config_type == w32.SERVICE_CONFIG_FAILURE_ACTIONS
    assert config_value == build_failure_actions()


def test_install_service_uses_dot_backslash_account() -> None:
    w32 = _make_w32()
    install_windows_service(
        venv_python=Path(r"C:\x\python.exe"),
        account="jeffr",
        password="p",
        _win32service=w32,
    )
    # lpServiceStartName is arg index 11 of CreateService
    assert w32.CreateService.call_args.args[11] == r".\jeffr"


def test_install_service_bin_path_references_python_and_module() -> None:
    w32 = _make_w32()
    venv_py = Path(r"C:\Users\x\.agent-core\.venv\Scripts\python.exe")
    install_windows_service(
        venv_python=venv_py,
        account="x",
        password="p",
        _win32service=w32,
    )
    bin_path = w32.CreateService.call_args.args[7]
    assert str(venv_py) in bin_path
    assert "agent_core.daemon.windows_service" in bin_path


def test_install_service_is_idempotent_deletes_existing() -> None:
    w32 = _make_w32()
    existing = MagicMock(name="existing_svc")
    w32.OpenService.side_effect = None
    w32.OpenService.return_value = existing

    install_windows_service(
        venv_python=Path(r"C:\x\python.exe"),
        account="x",
        password="p",
        _win32service=w32,
    )
    w32.DeleteService.assert_any_call(existing)
    w32.CreateService.assert_called_once()


def test_install_service_raises_on_non_windows_without_injection() -> None:
    if sys.platform == "win32":
        pytest.skip("non-Windows only")
    with pytest.raises(RuntimeError, match="Windows-only"):
        install_windows_service(
            venv_python=Path("/fake/python"),
            account="user",
            password="pw",
        )


def test_uninstall_service_returns_true_on_delete() -> None:
    w32 = _make_w32()
    w32.OpenService.side_effect = None
    w32.OpenService.return_value = MagicMock()

    assert uninstall_windows_service(_win32service=w32) is True
    w32.DeleteService.assert_called_once()


def test_uninstall_service_returns_false_when_absent() -> None:
    w32 = _make_w32()
    # OpenService raises (default side_effect from _make_w32)
    assert uninstall_windows_service(_win32service=w32) is False
    w32.DeleteService.assert_not_called()


def test_uninstall_raises_on_non_windows_without_injection() -> None:
    if sys.platform == "win32":
        pytest.skip("non-Windows only")
    with pytest.raises(RuntimeError, match="Windows-only"):
        uninstall_windows_service()


# ---------------------------------------------------------------------------
# Slow integration test (Windows + SCM only)
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_service_round_trip_via_real_scm() -> None:
    """Install, verify via OpenService, and uninstall using the real SCM.

    Requires Windows and the right to create services (admin or
    SeServiceLogonRight granted). Skipped on non-Windows.
    Uses a fake exe path — the service is registered but never started.
    """
    import getpass

    if sys.platform != "win32":
        pytest.skip("SCM round-trip is Windows-only")
    try:
        import win32service
    except ImportError:
        pytest.skip("pywin32 not installed")

    account = getpass.getuser()
    fake_py = Path(r"C:\Windows\System32\cmd.exe")

    try:
        install_windows_service(
            venv_python=fake_py,
            account=account,
            password="",
        )
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
        try:
            svc = win32service.OpenService(
                scm, SERVICE_NAME, win32service.SERVICE_QUERY_CONFIG
            )
            win32service.CloseServiceHandle(svc)
        finally:
            win32service.CloseServiceHandle(scm)
    finally:
        uninstall_windows_service()
