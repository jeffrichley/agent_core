# Spec: Windows headless service — true service, unbounded restart (issue #306)

## Goal

Replace the `InteractiveToken` Task Scheduler autostart (`daemon/autostart.py:46`) with a true Windows Service that is headless (no console window), runs as the current user (vault + keyring access), and restarts on failure with no count limit. This eliminates the console-window death class that caused two bus outages on 2026-07-13 and the scheduled-task 3-restart limit that B-1's watchdog self-terminate would exhaust.

Issue: https://github.com/jeffrichley/agent_core/issues/306  
Parent epic: #262 · Theme B (#265) · B-3 of 3.  
**Blocked by B-2** (needs the OS-dispatch framework; implement `windows_service.py` first, then wire CLI after B-2 lands).

## Acceptance criteria

- `packages/core/src/agent_core/daemon/windows_service.py` exists with:
  - `SERVICE_NAME = "AgentCoreProdDaemon"` (distinguishable from the old task name `"agent-core-daemon-prod"`)
  - `build_failure_actions() -> dict` — pure, importable on any OS; returns `{"ResetPeriod": 0xFFFFFFFF, "Actions": [(1, 0), (1, 0), (1, 0)]}` (3 × RESTART at 0 ms delay, infinite reset period)
  - `install_windows_service(*, venv_python: Path, account: str, password: str, _win32service=None) -> None` — idempotent (deletes-then-creates); sets failure actions via `ChangeServiceConfig2`; `_win32service` injectable for CI testing
  - `uninstall_windows_service(*, _win32service=None) -> bool` — returns `True` if deleted, `False` if not found; never raises for missing service
  - `AgentCoreDaemonService(win32serviceutil.ServiceFramework)` — spawns `bus run` with `CREATE_NO_WINDOW`; `SvcStop()` terminates subprocess; `SvcDoRun()` blocks until subprocess exits, then calls `ReportServiceStatus(SERVICE_STOPPED)` (SCM then applies failure actions)
- `build_failure_actions()` is verified correct on any CI OS (pure test)
- `install_windows_service` and `uninstall_windows_service` are tested with a mocked `win32service` module (no real SCM in CI)
- All SCM calls are guarded: functions raise `RuntimeError("Windows-only")` on non-Windows when `_win32service` is `None`; pure code is importable on Linux
- `pywin32>=300; sys_platform == "win32"` added to `packages/core/pyproject.toml` `dependencies`
- New `agent-core daemon install-service [--password] [--start/--no-start]` and `uninstall-service` commands in `cli.py` (prod-only, Windows-only guards matching existing autostart commands)
- `docs/setup/daemon.md` "Auto-start at boot" section updated: new commands documented, migration note (uninstall old task → install service) added, console-window regression test checklist included
- One `@pytest.mark.slow` Windows-only SCM round-trip integration test (install → query → uninstall)
- Existing `autostart.py`, `test_daemon_autostart.py`, `install-autostart`, and `uninstall-autostart` are **not** removed (migration path; users transition on their own schedule)

## Approach

No GoF pattern applies. Engineering principle: **SRP** — the existing pure/impure split convention (mirrors `autostart.py`). `windows_service.py` follows the same structure: platform-agnostic pure functions at the top, Windows-only impure wrappers below with lazy `import win32service`.

**Service wrapper**: `pywin32` (`win32service`, `win32serviceutil`, `win32event`) — already transitively in `uv.lock`; adding it as an explicit conditional dependency makes the requirement unambiguous. WinSW ruled out (see Alternatives).

**Failure actions**: The Windows SCM accepts up to three failure actions; the last action repeats indefinitely when exceeded. Setting all three to `SC_ACTION_RESTART` with 0 ms delay and `ResetPeriod = 0xFFFFFFFF` (INFINITE) gives unbounded immediate restart — exactly what B-1's watchdog self-terminate requires.

**Headless guarantee**: Windows Services run in Session 0 — no interactive console by design. The subprocess spawned in `SvcDoRun` uses `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` so the daemon process inherits no console handle.

**Runs as user**: `win32service.CreateService` accepts `lpServiceStartName = ".\\{account}"` and `lpPassword`. The user's Windows password is stored in LSA by the SCM. The `install-service` CLI command prompts for the password interactively (or reads it from `--password`). This is the only supported path to DPAPI / Windows Credential Manager access from a service context.

**Testing without Windows**: Inject a `MagicMock` via `_win32service=...` into `install_windows_service` / `uninstall_windows_service`. Pure functions (`build_failure_actions`, constants) need no mocking. The service class (`AgentCoreDaemonService`) is conditionally defined under `if sys.platform == "win32"` and is not tested on Linux; its behavior is covered by the slow Windows integration test.

**CLI wiring**: Sub-request 4 adds `install-service` / `uninstall-service` as direct callers of the module. If B-2's OS-dispatch framework restructures this call path, the Worker should adjust these commands to fit B-2's interface — the `windows_service.py` module itself has no dependency on B-2.

## Sub-requests (topologically sorted)

1. **Add `pywin32` dependency** — in `packages/core/pyproject.toml`, append `"pywin32>=300; sys_platform == 'win32'"` to the `dependencies` list.

2. **Create `packages/core/src/agent_core/daemon/windows_service.py`** with the following content:

   ```python
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
       try:
           import win32event
           import win32service
           import win32serviceutil

           class AgentCoreDaemonService(win32serviceutil.ServiceFramework):
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
                   win32serviceutil.ServiceFramework.__init__(self, args)
                   self._proc: subprocess.Popen | None = None

               def SvcStop(self) -> None:  # noqa: N802
                   self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
                   if self._proc is not None and self._proc.poll() is None:
                       self._proc.terminate()
                       try:
                           self._proc.wait(timeout=10)
                       except subprocess.TimeoutExpired:
                           self._proc.kill()

               def SvcDoRun(self) -> None:  # noqa: N802
                   from agent_core.daemon.instance import Instance, home_for

                   home = home_for(Instance.PROD)
                   venv_python = home / ".venv" / "Scripts" / "python.exe"
                   config = home / "agent_core.yaml"

                   self.ReportServiceStatus(win32service.SERVICE_RUNNING)
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
                   self.ReportServiceStatus(win32service.SERVICE_STOPPED)

       except ImportError:
           pass  # pywin32 not installed; skipped gracefully


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
           import win32service as _win32service  # noqa: N812

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
           import win32service as _win32service  # noqa: N812

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
           import win32serviceutil
           win32serviceutil.HandleCommandLine(AgentCoreDaemonService)
   ```

3. **Write `packages/core/tests/test_daemon_windows_service.py`**:

   ```python
   """Unit tests for daemon/windows_service.py — Windows Service registration."""
   from __future__ import annotations

   import sys
   from pathlib import Path
   from unittest.mock import MagicMock

   import pytest

   from agent_core.daemon.windows_service import (
       SC_ACTION_RESTART,
       RESET_PERIOD_INFINITE,
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
   ```

4. **Add `install_service` and `uninstall_service` commands to `packages/core/src/agent_core/daemon/cli.py`**. Add the import at the top alongside the existing `from agent_core.daemon import autostart` line:

   ```python
   from agent_core.daemon import autostart, windows_service as _win_svc
   ```

   Then append two new `@app.command()` functions at the end of the file (before the `if __name__ == "__main__":` block, if any — there is none):

   ```python
   @app.command()
   def install_service(
       instance: str | None = _INSTANCE_OPTION,
       password: str | None = typer.Option(
           None,
           "--password",
           help="Windows account password for the service logon. Prompted interactively if omitted.",
       ),
       start: bool | None = typer.Option(
           None,
           "--start/--no-start",
           help="Start the daemon now without prompting.",
       ),
   ) -> None:
       """Register the prod daemon as a headless Windows Service (unbounded restart)."""
       inst = _resolve(instance)
       if inst is not Instance.PROD:
           console.print("[red]service install is prod-only.[/red]")
           raise typer.Exit(code=1)
       if sys.platform != "win32":
           console.print("[red]Windows Service install is Windows-only.[/red]")
           raise typer.Exit(code=1)

       home = home_for(inst)
       venv_python = home / ".venv" / "Scripts" / "python.exe"
       if not venv_python.exists():
           console.print(
               f"[red]prod daemon is not installed ({venv_python} missing).[/red]\n"
               "   Run [bold]agent-core daemon install[/bold] first."
           )
           raise typer.Exit(code=1)

       account = getpass.getuser()
       svc_password = password
       if svc_password is None:
           import getpass as _gp
           svc_password = _gp.getpass(f"Windows password for account '{account}': ")

       try:
           _win_svc.install_windows_service(
               venv_python=venv_python,
               account=account,
               password=svc_password,
           )
       except Exception as exc:
           console.print(f"[red]SCM install failed: {exc}[/red]")
           raise typer.Exit(code=1) from exc

       console.print(
           f"[green]registered service '{_win_svc.SERVICE_NAME}'[/green] — "
           "the prod daemon will start at boot as a headless Windows Service."
       )
       should_start = start
       if should_start is None:
           should_start = typer.confirm("Start the prod daemon now?", default=False)
       if should_start:
           start_daemon(instance="prod")


   @app.command()
   def uninstall_service(instance: str | None = _INSTANCE_OPTION) -> None:
       """Remove the prod daemon Windows Service registration."""
       inst = _resolve(instance)
       if inst is not Instance.PROD:
           console.print("[red]service install is prod-only.[/red]")
           raise typer.Exit(code=1)
       if sys.platform != "win32":
           console.print("[red]Windows Service uninstall is Windows-only.[/red]")
           raise typer.Exit(code=1)

       if _win_svc.uninstall_windows_service():
           console.print(
               f"[green]removed service '{_win_svc.SERVICE_NAME}'[/green]"
           )
       else:
           console.print(
               f"[yellow]no service '{_win_svc.SERVICE_NAME}' to remove[/yellow]"
           )
   ```

5. **Update `docs/setup/daemon.md`**: Replace the "Auto-start at boot (Windows)" section (lines 226–254) with the following:

   ```markdown
   ## Auto-start at boot (Windows) — Windows Service

   The prod daemon registers as a **true Windows Service** (`AgentCoreProdDaemon`):
   headless (no console window), runs as your user account (vault + keyring
   access), and restarts immediately on failure with **no count limit**.

   ### Setup

   ```bash
   # Install the prod daemon first (service needs the venv exe to exist).
   agent-core daemon install

   # Register the service. You will be prompted for your Windows password
   # (stored in LSA by the SCM; never written to disk by this command).
   agent-core daemon install-service

   # Optional: remove the service.
   agent-core daemon uninstall-service
   ```

   `install-service` registers `AgentCoreProdDaemon` as an auto-start service
   that runs `python -m agent_core.daemon.windows_service` from the prod venv.
   On service start, the daemon spawns `bus run` as a hidden subprocess
   (`CREATE_NO_WINDOW`). When the daemon exits (crash, watchdog self-terminate,
   etc.), the service exits and the SCM restarts it immediately — indefinitely.

   ### Manual verification checklist

   After `install-service` and a reboot (or after manually starting the
   service via `sc start AgentCoreProdDaemon` / Services snap-in):

   - [ ] `agent-core daemon status` shows `prod daemon is running`.
   - [ ] Task Manager → Details tab: no `agent-core.exe` console window visible
         under the user's session processes.
   - [ ] Services snap-in: `AgentCoreProdDaemon` shows `Status: Running`,
         `Startup type: Automatic`, `Log On As: <your account>`.
   - [ ] Failure Actions (right-click → Properties → Recovery): all three
         actions are **Restart the Service**, delay 0 minutes.
   - [ ] Kill the bus process manually (`taskkill /F /IM python.exe`) → wait
         5 s → `agent-core daemon status` shows running again (SCM restarted it).

   ### Migration from the old scheduled task

   If you previously ran `install-autostart`, migrate as follows:

   ```bash
   agent-core daemon uninstall-autostart   # remove the old Task Scheduler task
   agent-core daemon install-service        # register the Windows Service
   ```

   The old `install-autostart`/`uninstall-autostart` commands remain available
   for rollback but are superseded.

   ### Why not Task Scheduler?

   The `install-autostart` approach used `LogonType: InteractiveToken`, which
   attaches the task to the interactive user session. When the session ends
   (or a console window appears), the task — and the daemon — die. The
   Windows Service runs in Session 0 (no interactive session, no console window)
   and is independent of login/logout cycles. The scheduled task's
   `RestartOnFailure` count was also bounded (max 3), which the B-1 watchdog
   self-terminate exhausted; the SCM's failure-action list repeats the last
   action indefinitely.
   ```

## File-level changes

| File | Change |
|---|---|
| `packages/core/pyproject.toml` | Add `"pywin32>=300; sys_platform == 'win32'"` to `dependencies` list |
| `packages/core/src/agent_core/daemon/windows_service.py` | **New file**: `SERVICE_NAME`, `SC_ACTION_RESTART`, `RESET_PERIOD_INFINITE`, `build_failure_actions()` (pure); `AgentCoreDaemonService` (conditional on `sys.platform == "win32"`); `install_windows_service()`, `uninstall_windows_service()` (lazy-import win32, injectable for tests) |
| `packages/core/src/agent_core/daemon/cli.py` | Add `from agent_core.daemon import windows_service as _win_svc` to imports; add `install_service()` and `uninstall_service()` Typer commands at end of file |
| `packages/core/tests/test_daemon_windows_service.py` | **New file**: 5 pure tests, 7 mocked-SCM tests, 1 `@pytest.mark.slow` SCM round-trip |
| `docs/setup/daemon.md` | Replace "Auto-start at boot (Windows)" section with service commands, verification checklist, migration note, and rationale |

No changes to `autostart.py`, `test_daemon_autostart.py`, or the `install-autostart` / `uninstall-autostart` CLI commands.

## Alternatives considered

1. **WinSW (Windows Service Wrapper)**: XML-configured Java-based service wrapper. Requires distributing a separate binary and managing its presence in the install. Ruled out: `pywin32` is already in `uv.lock` (transitively via `keyring`); pure-Python keeps the dependency surface minimal.

2. **Task Scheduler with `S4U` logon type** (`LogonType: S4U`): headless (no console) and runs as the user without requiring a password at install. Ruled out: `S4U` tokens do not have access to DPAPI master keys, so the Windows Credential Manager (keyring) is inaccessible — the explicit requirement "sees vault + keyring" is violated. Task Scheduler's restart count is also bounded.

3. **Windows Service running as `LocalSystem`**: no password needed at install; service runs in Session 0 (headless). Ruled out: `LocalSystem` does not have access to the user's DPAPI keys or Credential Manager entries, breaking keyring access. The acceptance criteria explicitly requires "running as the user (sees vault + keyring)".

## Open questions

1. **B-2 OS-dispatch framework interface**: The issue states B-3 is blocked by B-2. If B-2 adds a dispatch layer (e.g., `lifecycle_dispatch.py`) that the CLI routes lifecycle commands through, sub-request 4's CLI changes may need to call B-2's dispatch rather than `_win_svc` directly. The `windows_service.py` module itself has no dependency on B-2 and can be implemented immediately. The Worker should confirm B-2's interface before finalizing the CLI wiring.

2. **Service logon password with blank accounts**: The slow integration test uses `password=""`. On some Windows configurations a blank password install fails with an SCM error even if the account has no password set. If the real-SCM integration test consistently fails, the `--password` prompt approach may need a `--no-password` fallback that installs as `LocalSystem` (accepting reduced keyring access). Leave this to the real-Windows verification run.

## Out of scope

- Removing `autostart.py`, `test_daemon_autostart.py`, or the `install-autostart`/`uninstall-autostart` CLI commands — kept for migration rollback.
- macOS (`launchd`) or Linux (`systemd --user`) equivalents.
- B-2's OS-dispatch framework itself.
- The watchdog self-terminate mechanism (B-1).
- Active liveness detection for a silently-wedged daemon (Theme A, future ticket).
- Tray icon or OS toast notifications on service state changes.
