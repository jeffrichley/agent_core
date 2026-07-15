"""Windows Service wrapper for the agent-core prod daemon.

Pure/impure split (project convention, mirrors autostart.py):
- `SERVICE_NAME`, `SC_ACTION_RESTART`, `RESET_PERIOD_INFINITE`,
  `build_failure_actions()`: pure, platform-agnostic, importable on any OS.
- `AgentCoreDaemonService`, `install_windows_service()`,
  `uninstall_windows_service()`: Windows-only (lazy-import win32*).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants / pure config
# ---------------------------------------------------------------------------

SERVICE_NAME = "AgentCoreProdDaemon"
SERVICE_DISPLAY_NAME = "Agent Core Prod Daemon"
SERVICE_DESCRIPTION = (
    "agent-core bus daemon — headless Windows Service. "
    "Restarts automatically on failure (unbounded)."
)

# Mirrors win32service.SC_ACTION_RESTART = 1. Defined here so
# build_failure_actions() is importable without pywin32.
SC_ACTION_RESTART = 1
RESET_PERIOD_INFINITE = 0xFFFFFFFF


def build_failure_actions() -> dict:
    """Return failure-action config dict for win32service.ChangeServiceConfig2.

    Three RESTART actions at 0 ms delay; ResetPeriod = INFINITE so the
    failure count never resets and the last action (RESTART) repeats forever.
    """
    return {
        "ResetPeriod": RESET_PERIOD_INFINITE,
        "RebootMsg": None,
        "Command": None,
        "Actions": [
            (SC_ACTION_RESTART, 0),  # 1st failure: restart immediately
            (SC_ACTION_RESTART, 0),  # 2nd failure: restart immediately
            (SC_ACTION_RESTART, 0),  # 3rd+: restart immediately (repeats)
        ],
    }


# ---------------------------------------------------------------------------
# Windows Service class (conditionally defined on win32)
# ---------------------------------------------------------------------------

if sys.platform == "win32":  # pragma: no cover
    from agent_core.daemon import _win32

    class AgentCoreDaemonService(_win32.ServiceFramework):
        """Windows Service that wraps the agent-core prod bus daemon.

        SvcDoRun spawns ``bus run`` as a headless subprocess (no console
        window). When the watchdog self-terminates the daemon, proc.wait()
        returns, SvcDoRun calls ReportServiceStatus(SERVICE_STOPPED), and
        the SCM applies failure actions → restart immediately, unbounded.
        """

        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_ = SERVICE_DESCRIPTION

        def __init__(self, args: list[str]) -> None:
            _win32.ServiceFramework.__init__(self, args)
            self._proc: subprocess.Popen | None = None

        def SvcStop(self) -> None:
            self.ReportServiceStatus(_win32.win32service.SERVICE_STOP_PENDING)
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()

        def SvcDoRun(self) -> None:
            from agent_core.daemon.instance import Instance, home_for

            home = home_for(Instance.PROD)
            venv_python = home / ".venv" / "Scripts" / "python.exe"
            config = home / "agent_core.yaml"

            self.ReportServiceStatus(_win32.win32service.SERVICE_RUNNING)
            self._proc = subprocess.Popen(
                [
                    str(venv_python),
                    "-m", "agent_core.cli",
                    "bus", "run",
                    "--config", str(config),
                ],
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                ),
                stdin=subprocess.DEVNULL,
            )
            self._proc.wait()
            self.ReportServiceStatus(_win32.win32service.SERVICE_STOPPED)


# ---------------------------------------------------------------------------
# Impure: SCM install / uninstall (Windows-only, lazy-import via injection)
# ---------------------------------------------------------------------------

def install_windows_service(
    *,
    venv_python: Path,
    account: str,
    password: str,
    _win32service=None,
) -> None:
    """Install AgentCoreProdDaemon in the Windows SCM.

    Creates (or replaces if existing) the service to run as ``account``
    (the current user) with the user's ``password``, then sets failure actions
    (3× RESTART at 0 ms delay, infinite reset period).

    ``_win32service`` is injected in tests; pass ``None`` to use the real module.
    Raises ``RuntimeError`` on non-Windows when ``_win32service`` is ``None``.
    Raises ``win32service.error`` on SCM failures.
    """
    if _win32service is None:
        if sys.platform != "win32":
            raise RuntimeError("install_windows_service is Windows-only")
        from agent_core.daemon import _win32
        _win32service = _win32.win32service

    bin_path = f'"{venv_python}" -m agent_core.daemon.windows_service'
    user_name = f".\\{account}"

    scm = _win32service.OpenSCManager(None, None, _win32service.SC_MANAGER_ALL_ACCESS)
    try:
        # Idempotent: delete existing service if present
        try:
            existing = _win32service.OpenService(
                scm, SERVICE_NAME, _win32service.SERVICE_ALL_ACCESS
            )
            _win32service.DeleteService(existing)
            _win32service.CloseServiceHandle(existing)
        except _win32service.error:
            pass  # service did not exist

        svc = _win32service.CreateService(
            scm,
            SERVICE_NAME,
            SERVICE_DISPLAY_NAME,
            _win32service.SERVICE_ALL_ACCESS,
            _win32service.SERVICE_WIN32_OWN_PROCESS,
            _win32service.SERVICE_AUTO_START,
            _win32service.SERVICE_ERROR_NORMAL,
            bin_path,
            None, 0, None,
            user_name,
            password,
        )
        try:
            _win32service.ChangeServiceConfig2(
                svc,
                _win32service.SERVICE_CONFIG_FAILURE_ACTIONS,
                build_failure_actions(),
            )
        finally:
            _win32service.CloseServiceHandle(svc)
    finally:
        _win32service.CloseServiceHandle(scm)


def uninstall_windows_service(*, _win32service=None) -> bool:
    """Delete AgentCoreProdDaemon from the SCM.

    Returns ``True`` if deleted, ``False`` if no such service existed.
    Never raises for a missing service; raises ``win32service.error`` for
    other SCM failures.
    """
    if _win32service is None:
        if sys.platform != "win32":
            raise RuntimeError("uninstall_windows_service is Windows-only")
        from agent_core.daemon import _win32
        _win32service = _win32.win32service

    scm = _win32service.OpenSCManager(None, None, _win32service.SC_MANAGER_ALL_ACCESS)
    try:
        try:
            svc = _win32service.OpenService(
                scm, SERVICE_NAME, _win32service.SERVICE_ALL_ACCESS
            )
        except _win32service.error:
            return False
        _win32service.DeleteService(svc)
        _win32service.CloseServiceHandle(svc)
        return True
    finally:
        _win32service.CloseServiceHandle(scm)


if __name__ == "__main__":  # pragma: no cover
    # SCM entry point: ``python -m agent_core.daemon.windows_service``
    if sys.platform == "win32":
        from agent_core.daemon import _win32
        _win32.win32serviceutil.HandleCommandLine(AgentCoreDaemonService)
