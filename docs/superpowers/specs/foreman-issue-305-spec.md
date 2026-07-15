# Spec: cross-platform headless autostart framework + Linux/macOS (issue #305)

## Goal

Extend `agent-core daemon install-autostart` / `uninstall-autostart` to support Linux (systemd `--user`) and macOS (launchd LaunchAgent) in addition to the existing Windows (Task Scheduler) path. Replace the `sys.platform != "win32"` hard-exits at `daemon/cli.py:408–410` and `cli.py:453–455` with an OS-dispatch. Add an `agent-core-daemon` console-script entry point so init-system units can invoke the daemon at the stable `~/.agent-core/.venv` path. See issue #305.

## Acceptance criteria

- `build_systemd_unit(venv_bin, home)` emits a systemd unit string containing `Type=forking`, `PIDFile=<home>/daemon.pid`, `ExecStart`/`ExecStop` pointing to `<venv_bin>/agent-core-daemon`, `Restart=on-failure`, `RestartSec=5`, and `WatchdogSec=60`; validated by an inline golden-string test that runs on Linux CI (no platform skip).
- `build_launchd_plist(venv_bin, home, label, uid)` emits a valid XML plist; parsed via `plistlib.loads()` in a test that asserts `Label`, `ProgramArguments`, `KeepAlive=True`, `RunAtLoad=True`, `StandardOutPath`, and `StandardErrorPath`; test runs on Linux CI.
- `install-autostart` on Linux writes the unit to `~/.config/systemd/user/agent-core.service`, calls `systemctl --user daemon-reload` then `systemctl --user enable --now agent-core.service`, and prints a `loginctl enable-linger <user>` advisory; all subprocess calls mocked in CI.
- `install-autostart` on macOS writes the plist to `~/Library/LaunchAgents/com.jeffrichley.agent-core.daemon.prod.plist`, calls `launchctl bootout gui/<uid>/<label>` (exit code ignored), then `launchctl bootstrap gui/<uid> <plist>`; all subprocess calls mocked.
- `uninstall-autostart` on Linux calls `systemctl --user disable --now agent-core.service` (non-zero exit not fatal) then removes the unit file; mocked.
- `uninstall-autostart` on macOS calls `launchctl bootout gui/<uid>/<label>` and removes the plist file (idempotent — not fatal if absent); mocked.
- `install-autostart` and `uninstall-autostart` no longer print "autostart is Windows-only" and hard-exit on Linux or macOS.
- On an unsupported platform both commands print an error mentioning the platform name and exit 1.
- Re-running `install-autostart` on the same OS is idempotent (unit/plist is overwritten; `bootout` ignores "not loaded" errors; `enable --now` on an already-enabled unit succeeds).
- Both commands still require `--instance prod` (unchanged prod-only guard).
- `packages/core/pyproject.toml` `[project.scripts]` section includes `agent-core-daemon = "agent_core.daemon.cli:app"`.
- Windows path (Task Scheduler) is unchanged: no modifications to `autostart.py`, and existing `test_daemon_autostart.py` passes.

## Approach

No GoF pattern is needed. The engineering principle is **OCP (Open/Closed)**: the dispatch shell in `cli.py` is open to new platforms (add an `elif` branch) but closed for modification of existing ones. Each platform is an independent, narrow module following the established pure/impure split from `autostart.py`.

The existing `autostart.py` (Windows) is left completely untouched. The `sys.platform != "win32"` guards in both CLI commands are replaced with an `if/elif/elif/else` chain that imports and calls the appropriate module.

### New module: `packages/core/src/agent_core/daemon/autostart_linux.py`

```python
"""Linux daemon auto-start — systemd --user unit for the prod daemon.

Pure/impure split (mirrors autostart.py):
- build_systemd_unit: pure, returns the unit file content.
- install_systemd_unit: impure, writes file + calls systemctl.
- uninstall_systemd_unit: impure, calls systemctl + removes file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

UNIT_NAME = "agent-core.service"


def build_systemd_unit(*, venv_bin: Path, home: Path) -> str:
    """Return the systemd --user unit file content for the prod daemon.

    Uses Type=forking + PIDFile because daemon start forks the bus subprocess
    and exits while writing daemon.pid. Systemd monitors the forked PID.
    WatchdogSec=60 is included but requires B-1 (#304) sd_notify to activate.
    """
    pid_file = home / "daemon.pid"
    exec_bin = venv_bin / "agent-core-daemon"
    return (
        "[Unit]\n"
        "Description=agent-core prod daemon\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=forking\n"
        f"PIDFile={pid_file}\n"
        f"ExecStart={exec_bin} start --instance prod\n"
        f"ExecStop={exec_bin} stop --instance prod\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "WatchdogSec=60\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def install_systemd_unit(unit_content: str, unit_path: Path) -> None:
    """Write the unit file, reload the daemon, and enable + start the service."""
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit_content, encoding="utf-8")
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", UNIT_NAME],
        check=True, capture_output=True, text=True,
    )


def uninstall_systemd_unit(unit_path: Path) -> None:
    """Disable + stop the service and remove the unit file. Idempotent."""
    # Non-zero exit (unit not loaded/enabled) is not an error — suppress it.
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", UNIT_NAME],
        capture_output=True, text=True, check=False,
    )
    unit_path.unlink(missing_ok=True)
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True, text=True, check=False,
    )
```

### New module: `packages/core/src/agent_core/daemon/autostart_macos.py`

```python
"""macOS daemon auto-start — launchd LaunchAgent plist for the prod daemon.

Pure/impure split (mirrors autostart.py):
- build_launchd_plist: pure, returns the plist XML.
- install_launchd_plist: impure, writes file + calls launchctl.
- uninstall_launchd_plist: impure, calls launchctl + removes file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

LABEL = "com.jeffrichley.agent-core.daemon.prod"


def build_launchd_plist(*, venv_bin: Path, home: Path, label: str, uid: int) -> str:
    """Return the launchd LaunchAgent plist XML for the prod daemon.

    KeepAlive=true: launchd respawns the job if it exits.
    RunAtLoad=true: job starts immediately when the plist is bootstrapped.
    Note: `uid` is accepted for future use (could appear in ProgramArguments
    or environment); currently used only by the impure installer.
    """
    exec_bin = venv_bin / "agent-core-daemon"
    log_path = home / "daemon.log"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n'
        '          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        f"    <string>{label}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"        <string>{exec_bin}</string>\n"
        "        <string>start</string>\n"
        "        <string>--instance</string>\n"
        "        <string>prod</string>\n"
        "    </array>\n"
        "    <key>KeepAlive</key>\n"
        "    <true/>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        "    <key>StandardOutPath</key>\n"
        f"    <string>{log_path}</string>\n"
        "    <key>StandardErrorPath</key>\n"
        f"    <string>{log_path}</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def install_launchd_plist(
    plist_path: Path,
    plist_content: str,
    *,
    uid: int,
    label: str,
) -> None:
    """Write the plist and bootstrap it into the user launchd session. Idempotent."""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content, encoding="utf-8")
    # Idempotent unload — ignore non-zero exit (service not currently loaded).
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{label}"],
        capture_output=True, text=True, check=False,
    )
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
        check=True, capture_output=True, text=True,
    )


def uninstall_launchd_plist(
    plist_path: Path,
    *,
    uid: int,
    label: str,
) -> bool:
    """Unload and remove the launchd plist. Returns True if bootout succeeded."""
    result = subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{label}"],
        capture_output=True, text=True, check=False,
    )
    plist_path.unlink(missing_ok=True)
    return result.returncode == 0
```

### Modified: `packages/core/src/agent_core/daemon/cli.py`

Replace the body of `install_autostart` from the `if sys.platform != "win32":` guard onward (lines 408–443) with an OS dispatch. Also update imports: add `import os` (for `os.getuid()` on macOS, platform-guarded). The prod-only guard at the top of the function is unchanged.

```python
@app.command()
def install_autostart(
    instance: str | None = _INSTANCE_OPTION,
    start: bool | None = typer.Option(
        None,
        "--start/--no-start",
        help="Start the daemon now without prompting (for non-interactive use).",
    ),
) -> None:
    """Register the prod daemon to auto-start at logon (systemd/launchd/Task Scheduler)."""
    inst = _resolve(instance)
    if inst is not Instance.PROD:
        console.print(
            "[red]autostart is prod-only[/red] — the source instance is started "
            "by hand from the workspace."
        )
        raise typer.Exit(code=1)

    home = home_for(inst)

    if sys.platform == "linux":
        from agent_core.daemon import autostart_linux

        venv_bin = home / ".venv" / "bin"
        daemon_bin = venv_bin / "agent-core-daemon"
        if not daemon_bin.exists():
            console.print(
                f"[red]prod daemon is not installed ({daemon_bin} missing).[/red]\n"
                "   Run [bold]agent-core daemon install[/bold] first."
            )
            raise typer.Exit(code=1)

        unit_path = Path.home() / ".config" / "systemd" / "user" / autostart_linux.UNIT_NAME
        unit_content = autostart_linux.build_systemd_unit(venv_bin=venv_bin, home=home)
        try:
            autostart_linux.install_systemd_unit(unit_content, unit_path)
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]systemctl failed (exit {exc.returncode}).[/red]")
            if exc.stderr:
                console.print(exc.stderr.rstrip())
            raise typer.Exit(code=1) from exc

        console.print(
            f"[green]registered systemd user unit '{autostart_linux.UNIT_NAME}'[/green] — "
            "the prod daemon will start at every login."
        )
        console.print(
            f"[yellow]Advisory: run 'loginctl enable-linger {getpass.getuser()}' to start "
            "the daemon at boot without being logged in.[/yellow]"
        )

    elif sys.platform == "darwin":
        import os as _os
        from agent_core.daemon import autostart_macos

        venv_bin = home / ".venv" / "bin"
        daemon_bin = venv_bin / "agent-core-daemon"
        if not daemon_bin.exists():
            console.print(
                f"[red]prod daemon is not installed ({daemon_bin} missing).[/red]\n"
                "   Run [bold]agent-core daemon install[/bold] first."
            )
            raise typer.Exit(code=1)

        label = autostart_macos.LABEL
        uid = _os.getuid()
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        plist_content = autostart_macos.build_launchd_plist(
            venv_bin=venv_bin, home=home, label=label, uid=uid
        )
        try:
            autostart_macos.install_launchd_plist(plist_path, plist_content, uid=uid, label=label)
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]launchctl failed (exit {exc.returncode}).[/red]")
            if exc.stderr:
                console.print(exc.stderr.rstrip())
            raise typer.Exit(code=1) from exc

        console.print(
            f"[green]registered launchd LaunchAgent '{label}'[/green] — "
            "the prod daemon will start at every login."
        )

    elif sys.platform == "win32":
        exe = home / ".venv" / "Scripts" / "agent-core.exe"
        if not exe.exists():
            console.print(
                f"[red]prod daemon is not installed ({exe} missing).[/red]\n"
                "   Run [bold]agent-core daemon install[/bold] first."
            )
            raise typer.Exit(code=1)

        account = getpass.getuser()
        xml = autostart.build_autostart_task(agent_core_exe=exe, account=account)
        try:
            autostart.install_autostart(xml)
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]schtasks failed (exit {exc.returncode}).[/red]")
            if exc.stderr:
                console.print(exc.stderr.rstrip())
            raise typer.Exit(code=1) from exc

        console.print(
            f"[green]registered autostart task '{autostart.TASK_NAME}'[/green] — "
            "the prod daemon will start at every logon."
        )

    else:
        console.print(f"[red]autostart is not supported on {sys.platform}.[/red]")
        raise typer.Exit(code=1)

    should_start = start
    if should_start is None:
        should_start = typer.confirm("Start the prod daemon now?", default=False)
    if should_start:
        start_daemon(instance="prod")
```

Replace `uninstall_autostart` body (lines 447–465) with:

```python
@app.command()
def uninstall_autostart(instance: str | None = _INSTANCE_OPTION) -> None:
    """Remove the prod daemon auto-start task/unit/plist."""
    inst = _resolve(instance)
    if inst is not Instance.PROD:
        console.print("[red]autostart is prod-only.[/red]")
        raise typer.Exit(code=1)

    home = home_for(inst)

    if sys.platform == "linux":
        from agent_core.daemon import autostart_linux

        unit_path = Path.home() / ".config" / "systemd" / "user" / autostart_linux.UNIT_NAME
        autostart_linux.uninstall_systemd_unit(unit_path)
        console.print(
            f"[green]removed systemd user unit '{autostart_linux.UNIT_NAME}'[/green]"
        )

    elif sys.platform == "darwin":
        import os as _os
        from agent_core.daemon import autostart_macos

        label = autostart_macos.LABEL
        uid = _os.getuid()
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        removed = autostart_macos.uninstall_launchd_plist(plist_path, uid=uid, label=label)
        if removed:
            console.print(f"[green]removed launchd LaunchAgent '{label}'[/green]")
        else:
            console.print(f"[yellow]no launchd LaunchAgent '{label}' to remove[/yellow]")

    elif sys.platform == "win32":
        if autostart.uninstall_autostart():
            console.print(
                f"[green]removed autostart task '{autostart.TASK_NAME}'[/green]"
            )
        else:
            console.print(
                f"[yellow]no autostart task '{autostart.TASK_NAME}' to remove[/yellow]"
            )

    else:
        console.print(f"[red]autostart is not supported on {sys.platform}.[/red]")
        raise typer.Exit(code=1)
```

### Tests: `packages/core/tests/test_daemon_autostart_linux.py` (new)

```python
"""Unit tests for daemon/autostart_linux.py — systemd --user unit registration."""
from __future__ import annotations

import subprocess
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


def test_build_systemd_unit_golden_string() -> None:
    venv_bin = Path("/home/jeff/.agent-core/.venv/bin")
    home = Path("/home/jeff/.agent-core")
    unit = build_systemd_unit(venv_bin=venv_bin, home=home)
    assert "Type=forking" in unit
    assert f"PIDFile={home}/daemon.pid" in unit
    assert f"ExecStart={venv_bin}/agent-core-daemon start --instance prod" in unit
    assert f"ExecStop={venv_bin}/agent-core-daemon stop --instance prod" in unit
    assert "Restart=on-failure" in unit
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
    call_count = 0

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1

        class _R:
            returncode = 1 if call_count == 1 else 0  # fail on daemon-reload
            stdout = ""
            stderr = "Failed to connect to bus."
        return _R()

    # check=True is honoured because subprocess.run raises on non-zero when check=True.
    # Our fake needs to simulate that.
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
```

### Tests: `packages/core/tests/test_daemon_autostart_macos.py` (new)

```python
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
```

### CLI dispatch tests (add to `packages/core/tests/test_daemon_cli.py`)

Add these tests to `test_daemon_cli.py`, using `monkeypatch.setattr("sys.platform", ...)`:

```python
def test_install_autostart_linux_dispatches_to_linux_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")
    # Create the daemon binary so the existence check passes.
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "agent-core-daemon").write_text("#!/bin/sh\n")
    installed: list[str] = []
    monkeypatch.setattr(
        "agent_core.daemon.autostart_linux.install_systemd_unit",
        lambda content, path: installed.append(str(path)),
    )
    result = runner.invoke(daemon_app, ["install-autostart", "--no-start"])
    assert result.exit_code == 0, result.stdout
    assert len(installed) == 1
    assert "agent-core.service" in installed[0]


def test_install_autostart_macos_dispatches_to_macos_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("os.getuid", lambda: 501)
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "agent-core-daemon").write_text("#!/bin/sh\n")
    installed: list[str] = []
    monkeypatch.setattr(
        "agent_core.daemon.autostart_macos.install_launchd_plist",
        lambda path, content, uid, label: installed.append(label),
    )
    result = runner.invoke(daemon_app, ["install-autostart", "--no-start"])
    assert result.exit_code == 0, result.stdout
    assert installed == ["com.jeffrichley.agent-core.daemon.prod"]


def test_install_autostart_unsupported_platform_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "freebsd14")
    result = runner.invoke(daemon_app, ["install-autostart"])
    assert result.exit_code == 1
    assert "freebsd14" in result.stdout.lower() or "not supported" in result.stdout.lower()


def test_install_autostart_linux_errors_when_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")
    # No .venv/bin/agent-core-daemon created.
    result = runner.invoke(daemon_app, ["install-autostart"])
    assert result.exit_code == 1

def test_uninstall_autostart_linux_dispatches_to_linux_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")
    removed: list[str] = []
    monkeypatch.setattr(
        "agent_core.daemon.autostart_linux.uninstall_systemd_unit",
        lambda path: removed.append(str(path)),
    )
    result = runner.invoke(daemon_app, ["uninstall-autostart"])
    assert result.exit_code == 0, result.stdout
    assert len(removed) == 1


def test_uninstall_autostart_unsupported_platform_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "freebsd14")
    result = runner.invoke(daemon_app, ["uninstall-autostart"])
    assert result.exit_code == 1
```

## Sub-requests (topologically sorted)

1. **`packages/core/pyproject.toml`**: Add `agent-core-daemon = "agent_core.daemon.cli:app"` under `[project.scripts]` (one line, after the existing `agent-core` entry).
2. **`packages/core/src/agent_core/daemon/autostart_linux.py`**: Create with `UNIT_NAME`, pure `build_systemd_unit(*, venv_bin, home)`, impure `install_systemd_unit(unit_content, unit_path)`, impure `uninstall_systemd_unit(unit_path)` — exactly as shown in the Approach section.
3. **`packages/core/src/agent_core/daemon/autostart_macos.py`**: Create with `LABEL`, pure `build_launchd_plist(*, venv_bin, home, label, uid)`, impure `install_launchd_plist(plist_path, plist_content, *, uid, label)`, impure `uninstall_launchd_plist(plist_path, *, uid, label) -> bool` — exactly as shown.
4. **`packages/core/src/agent_core/daemon/cli.py`**: Replace `install_autostart` (keeping the prod-only guard, replacing the `sys.platform != "win32"` guard + Windows block with the four-branch dispatch shown above). Replace `uninstall_autostart` similarly. No other changes to `cli.py`.
5. **`packages/core/tests/test_daemon_autostart_linux.py`**: Create with all tests shown above.
6. **`packages/core/tests/test_daemon_autostart_macos.py`**: Create with all tests shown above.
7. **`packages/core/tests/test_daemon_cli.py`**: Add the six CLI dispatch tests shown above (Linux install/uninstall, macOS install, unsupported platform install/uninstall, binary-missing install).

## File-level changes

| File | Change |
|---|---|
| `packages/core/pyproject.toml` | Add `agent-core-daemon = "agent_core.daemon.cli:app"` to `[project.scripts]` |
| `packages/core/src/agent_core/daemon/autostart_linux.py` | **New**: `UNIT_NAME`, `build_systemd_unit()`, `install_systemd_unit()`, `uninstall_systemd_unit()` |
| `packages/core/src/agent_core/daemon/autostart_macos.py` | **New**: `LABEL`, `build_launchd_plist()`, `install_launchd_plist()`, `uninstall_launchd_plist()` |
| `packages/core/src/agent_core/daemon/cli.py` | Replace `install_autostart` and `uninstall_autostart` command bodies with OS dispatch; add `import os as _os` inside the darwin branch (platform-guarded) |
| `packages/core/tests/test_daemon_autostart_linux.py` | **New**: `UNIT_NAME` constant, `build_systemd_unit` golden-string, `install_systemd_unit` writes+calls, `uninstall_systemd_unit` calls+removes, idempotent tests |
| `packages/core/tests/test_daemon_autostart_macos.py` | **New**: `LABEL` constant, `build_launchd_plist` plistlib parse, `install_launchd_plist` calls+error paths, `uninstall_launchd_plist` true/false/file-removal |
| `packages/core/tests/test_daemon_cli.py` | Add 6 platform-dispatch tests for `install-autostart` / `uninstall-autostart` |

`packages/core/src/agent_core/daemon/autostart.py` — **not modified** (Windows code is untouched).

## Alternatives considered

1. **Single `autostart.py` with platform dispatch inside it**: all three platform implementations in one module. Ruled out — the file would exceed 250 lines mixing three implementations and two test files, contradicts the Windows module's narrow-responsibility precedent, and makes per-platform unit tests import the whole combined module.
2. **Formal ABC/Protocol `AutostartBackend`**: OOP abstraction with `build()`, `install()`, `uninstall()` methods. Ruled out — three concrete platforms, no runtime polymorphism needed, three additional module-level classes for zero benefit over plain module-level functions that already match the `autostart.py` style.
3. **Invoke bus run directly in unit/plist (bypass `daemon start`)**: units would call `~/.agent-core/.venv/bin/python -m agent_core.cli bus run --config ...` in the foreground, which is the semantically cleanest approach for systemd `Type=simple` and macOS `KeepAlive=true`. Ruled out because the issue explicitly names `agent-core-daemon` as the entry point for units and does not ask for a foreground runner mode; implementing the issue literally first and iterating is lower risk.
4. **`Restart=always` (as stated in the issue) vs `Restart=on-failure` (spec choice)**: The issue body lists `Restart=always` in the Linux scope bullet. The spec deviates to `Restart=on-failure`. The reason: with `Type=forking`, the monitored PID is the forked bus subprocess. `Restart=always` means systemd re-starts the unit immediately after *any* exit — including a clean `systemctl --user stop`. The operator would be unable to stop the daemon intentionally; every `stop` would be defeated by an immediate restart. `Restart=on-failure` (exit code ≠ 0 only) correctly handles crashes while permitting clean operator-initiated stops. This is an intentional deviation from the issue's stated value and is documented here for operator approval before the Worker ships it.

## Manual smoke-test checklist

The issue acceptance criteria requires "manual per-OS verification documented." The following commands constitute the post-install verification steps for each platform. These are intended for the human operator performing the first on-device install; they are not run in CI.

### Linux (systemd --user)

```bash
# After running: agent-core daemon install-autostart --instance prod
systemctl --user status agent-core.service   # should show Active: active (running)
systemctl --user is-enabled agent-core.service  # should print "enabled"

# Verify linger is in effect (daemon survives logout):
loginctl show-user "$USER" --property=Linger  # shows "Linger=yes" after loginctl enable-linger
```

### macOS (launchd LaunchAgent)

```bash
# After running: agent-core daemon install-autostart --instance prod
launchctl list | grep agent-core             # should show the job PID and label
launchctl print "gui/$(id -u)/com.jeffrichley.agent-core.daemon.prod"  # full job detail

# After running: agent-core daemon uninstall-autostart --instance prod
launchctl list | grep agent-core             # should return nothing
```

## Open questions

1. **macOS `KeepAlive=true` + fork-and-exit**: `daemon start` spawns the bus subprocess and exits immediately. With `KeepAlive=true`, launchd sees the job exit and respawns it. The second invocation finds the daemon already running and exits 1; launchd throttles and retries (~10 s cycle). The daemon stays alive in practice (the bus subprocess is not a launchd child), but launchd will log repeated job exits. If on-macOS testing shows this respawn noise is unacceptable, the Worker should either (a) add a `--foreground` flag to `daemon start` that keeps the process alive by waiting on the child PID (for launchd use), or (b) use a `KeepAlive=<PathState>` dictionary keyed on the PID file (run only while PID file absent). Either variant is a follow-up and does not block CI.

## Out of scope

- Windows B-3 — the existing Windows Task Scheduler path is unchanged and tested.
- B-1 (#304) `sd_notify` implementation — `WatchdogSec=60` appears in the systemd unit content but this ticket does not add sd_notify signalling to the daemon process; deployment of the Linux unit is gated on B-1 merging first.
- Tray icon, OS notification, or Discord surfacing of autostart status (#265).
- Source / test instance autostart (prod-only, unchanged).
- Automated deployment of the manual smoke-test steps in CI — the `Manual smoke-test checklist` section above satisfies the issue's "manual per-OS verification documented" acceptance gate; running those steps in CI is not required.
- Changes to `daemon start`/`stop`/`status`/`install`/`refresh` command behaviour.
