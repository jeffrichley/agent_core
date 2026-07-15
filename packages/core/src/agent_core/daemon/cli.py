"""`agent-core daemon` — process supervision for the bus daemon.

start: spawn `agent-core bus run --config <home>/agent_core.yaml`
       detached; write the resulting PID to <home>/daemon.pid.
stop:  read the PID file, kill the process tree, remove the PID file.
status: report running/not-running, PID, last 20 lines of daemon.log.
install: populate the prod or test daemon venv from a GitHub Release.
refresh: stop -> (prod/test: install) -> start.
init:   scaffold a minimal agent_core.yaml for an instance.

Three instances are supported (Phase 3.5): `prod` (port 8789, home
~/.agent-core/, release-installed venv), `source` (port 8788, home
~/.agent-core-source/, runs editable from the workspace .venv), and
`test` (port 8787, home ~/.agent-core-test/, release-installed venv).
The instance is resolved per-invocation from `--instance` / the
AGENT_CORE_INSTANCE env var / default `prod`. AGENT_CORE_HOME still
overrides the home dir directly (test escape hatch).
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from agent_core.daemon import autostart
from agent_core.daemon import windows_service as _win_svc
from agent_core.daemon.config_template import build_default_config
from agent_core.daemon.install import (
    InstallStamp,
    WorkspaceNotFoundError,
    find_workspace_root,
    read_stamp,
    write_stamp,
)
from agent_core.daemon.instance import Instance, home_for, resolve_instance
from agent_core.daemon.release import (
    NoReleasesError,
    download_requirements,
    download_wheels,
    ensure_venv,
    install_requirements,
    install_wheels,
    list_release_wheels,
    resolve_version,
)
from agent_core.daemon.supervisor import is_alive, kill_tree, read_pid, remove_pid, write_pid

RELEASE_REPO = "jeffrichley/agent_core"

app = typer.Typer(
    help="Daemon process supervision: start, stop, status, install, refresh, init."
)
console = Console()

# Shared --instance option definition (reused by every command).
_INSTANCE_OPTION = typer.Option(
    None, "--instance", help="Daemon instance: 'prod' (default), 'source', or 'test'."
)


def _resolve(instance: str | None) -> Instance:
    """Resolve the instance from the flag + AGENT_CORE_INSTANCE env."""
    try:
        return resolve_instance(
            flag=instance, env=os.environ.get("AGENT_CORE_INSTANCE")
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


def _pid_path(home: Path) -> Path:
    return home / "daemon.pid"


def _config_path(home: Path) -> Path:
    return home / "agent_core.yaml"


def _log_path(home: Path) -> Path:
    return home / "daemon.log"


def _prod_venv_python(home: Path) -> Path:
    """Fixed path to the prod daemon venv's python, regardless of existence."""
    if sys.platform == "win32":
        return home / ".venv" / "Scripts" / "python.exe"
    return home / ".venv" / "bin" / "python"


def _workspace_venv_python() -> Path:
    """Path to the workspace .venv python (the source daemon interpreter).

    Resolves the workspace root from the current directory. Raises
    WorkspaceNotFoundError if the cwd is not inside the agent_core repo.
    """
    workspace = find_workspace_root(Path.cwd())
    if sys.platform == "win32":
        return workspace / ".venv" / "Scripts" / "python.exe"
    return workspace / ".venv" / "bin" / "python"


def _daemon_python(instance: Instance, home: Path) -> str:
    """Return the interpreter the supervisor should spawn the bus with.

    prod/test: the daemon venv if present, else sys.executable (fallback).
    source:    the workspace .venv python (editable install). Raises
               WorkspaceNotFoundError if not inside the repo.
    """
    if instance is Instance.SOURCE:
        return str(_workspace_venv_python())
    candidate = _prod_venv_python(home)
    return str(candidate) if candidate.exists() else sys.executable


@app.command()
def start(instance: str | None = _INSTANCE_OPTION) -> None:
    """Spawn `agent-core bus run` detached, write the PID file."""
    inst = _resolve(instance)
    home = home_for(inst)
    pid_file = _pid_path(home)
    cfg = _config_path(home)
    log_file = _log_path(home)

    if not cfg.exists():
        console.print(
            f"[red]No daemon config at {cfg}.[/red] "
            f"Run [bold]agent-core daemon init --instance {inst}[/bold] first."
        )
        raise typer.Exit(code=1)

    existing = read_pid(pid_file)
    if existing is not None and is_alive(existing):
        console.print(f"[yellow]daemon already running (PID: {existing})[/yellow]")
        raise typer.Exit(code=1)
    if existing is not None:
        remove_pid(pid_file)  # stale

    try:
        daemon_py = _daemon_python(inst, home)
    except WorkspaceNotFoundError as exc:
        console.print(
            f"[red]source daemon needs the workspace .venv but {exc}[/red]\n"
            "   Run this from inside the agent_core repo."
        )
        raise typer.Exit(code=1) from exc

    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_file, "ab", buffering=0)

    proc = subprocess.Popen(
        [daemon_py, "-m", "agent_core.cli", "bus", "run", "--config", str(cfg)],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    write_pid(pid_file, proc.pid)
    console.print(f"[green]{inst} daemon started (PID: {proc.pid})[/green]")


# Alias so other commands can call `start` without ambiguity.
start_daemon = start


@app.command()
def stop(instance: str | None = _INSTANCE_OPTION) -> None:
    """Kill the daemon and clean the PID file. Idempotent."""
    inst = _resolve(instance)
    home = home_for(inst)
    pid_file = _pid_path(home)
    pid = read_pid(pid_file)
    if pid is None:
        console.print(f"[yellow]{inst} daemon is not running[/yellow]")
        return
    if not is_alive(pid):
        console.print(
            f"[yellow]{inst} daemon is not running (stale PID file removed)[/yellow]"
        )
        remove_pid(pid_file)
        return
    kill_tree(pid)
    remove_pid(pid_file)
    console.print(f"[green]{inst} daemon stopped (PID: {pid})[/green]")


@app.command()
def status(instance: str | None = _INSTANCE_OPTION) -> None:
    """Report daemon liveness and tail the log."""
    inst = _resolve(instance)
    home = home_for(inst)
    pid_file = _pid_path(home)
    log_file = _log_path(home)
    pid = read_pid(pid_file)

    if pid is None:
        console.print(f"[yellow]{inst} daemon is not running[/yellow]")
        return
    if not is_alive(pid):
        console.print(
            f"[yellow]{inst} daemon is not running (stale PID file removed)[/yellow]"
        )
        remove_pid(pid_file)
        return

    console.print(f"[green]{inst} daemon is running (PID: {pid})[/green]")

    # Diagnostic: which interpreter the supervisor would use.
    try:
        daemon_py = _daemon_python(inst, home)
        console.print(f"running from: {daemon_py}")
    except WorkspaceNotFoundError:
        console.print("running from: [dim red](workspace .venv not found)[/dim red]")

    # Diagnostic: install stamp — prod and test (release-installed); source is editable.
    if inst is not Instance.SOURCE:
        stamp = read_stamp(home)
        if stamp is not None:
            console.print(f"installed at: {stamp.installed_at}")
            console.print(f"installed sha: {stamp.installed_sha}")
            console.print(f"installed version: {stamp.installed_version}")

    if log_file.exists():
        console.print("\n[dim]--- last 20 lines of daemon.log ---[/dim]")
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-20:]:
            console.print(line)


@app.command()
def init(
    instance: str | None = _INSTANCE_OPTION,
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing agent_core.yaml."
    ),
) -> None:
    """Scaffold a minimal agent_core.yaml for an instance."""
    inst = _resolve(instance)
    home = home_for(inst)
    cfg = _config_path(home)

    if cfg.exists() and not force:
        console.print(
            f"[red]config already exists at {cfg}[/red] — pass --force to overwrite."
        )
        raise typer.Exit(code=1)

    home.mkdir(parents=True, exist_ok=True)
    cfg.write_text(build_default_config(instance=inst, home=home), encoding="utf-8")
    console.print(f"[green]wrote {inst} daemon config: {cfg}[/green]")


@app.command()
def install(
    instance: str | None = _INSTANCE_OPTION,
    release: str | None = typer.Option(
        None,
        "--release",
        help="Release tag to install (e.g. v0.1.0). Default: latest release.",
    ),
) -> None:
    """Populate the prod or test daemon venv from a GitHub Release artifact."""
    inst = _resolve(instance)

    if inst is Instance.SOURCE:
        console.print(
            "[red]the source instance is not installed[/red] — it runs editable "
            "from the workspace .venv.\n"
            "   Just run [bold]agent-core daemon start --instance source[/bold]."
        )
        raise typer.Exit(code=1)

    home = home_for(inst)
    pid_file = _pid_path(home)
    existing = read_pid(pid_file)
    if existing is not None and is_alive(existing):
        console.print(
            f"[red]daemon is currently running (PID {existing}).[/red]\n"
            "   • Run [bold]agent-core daemon stop[/bold] and re-run install, or\n"
            "   • Run [bold]agent-core daemon refresh[/bold] to stop/install/start "
            "in one step."
        )
        raise typer.Exit(code=1)

    home.mkdir(parents=True, exist_ok=True)

    # Resolve version (None -> latest).
    try:
        tag = resolve_version(release, repo=RELEASE_REPO)
    except NoReleasesError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    # Fetch + download artifacts.
    cache_dir = home / "releases" / tag
    assets = list_release_wheels(tag, repo=RELEASE_REPO)
    if not assets:
        console.print(f"[red]release {tag} has no .whl assets attached[/red]")
        raise typer.Exit(code=1)
    wheel_paths = download_wheels(assets, dest=cache_dir)
    try:
        req_path = download_requirements(tag, repo=RELEASE_REPO, dest=cache_dir)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    # Ensure daemon venv exists (creates if first install on this box).
    venv = home / ".venv"
    try:
        ensure_venv(venv, python_version="3.12")
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]uv venv failed (exit {exc.returncode}).[/red]")
        raise typer.Exit(code=1) from exc

    venv_python = _prod_venv_python(home)

    # Install pinned dependencies (no-op-fast on upgrades when deps unchanged).
    try:
        install_requirements(req_path, venv_python=venv_python)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]dep install failed (exit {exc.returncode}).[/red]")
        raise typer.Exit(code=1) from exc

    # Surgically replace the agent_core* packages.
    try:
        install_wheels(wheel_paths, venv_python=venv_python)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]wheel install failed (exit {exc.returncode}).[/red]")
        raise typer.Exit(code=1) from exc

    # Stamp it.
    version = tag.removeprefix("v")
    stamp = InstallStamp(
        installed_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        installed_sha=_git_sha_of_tag(tag),
        installed_version=version,
        python_version="3.12",
        extra=None,
        release_tag=tag,
    )
    write_stamp(home, stamp)

    console.print(f"[green]{inst} daemon updated to {tag}[/green]")


@app.command()
def refresh(
    instance: str | None = _INSTANCE_OPTION,
    release: str | None = typer.Option(
        None,
        "--release",
        help="Release tag to install (prod only). Default: latest release.",
    ),
) -> None:
    """Stop daemon -> (prod/test: install release) -> start daemon.

    For source this is a plain bounce: the source daemon runs editable from
    the workspace .venv, so a stop/start picks up the latest source edits.
    """
    inst = _resolve(instance)
    stop(instance=instance)
    if inst is not Instance.SOURCE:
        install(instance=instance, release=release)
    start(instance=instance)


def _git_sha_of_tag(tag: str) -> str:
    """Best-effort: resolve a tag to a git short sha. Returns 'unknown' on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", tag],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


@app.command()
def install_autostart(
    instance: str | None = _INSTANCE_OPTION,
    start: bool | None = typer.Option(
        None,
        "--start/--no-start",
        help="Start the daemon now without prompting (for non-interactive use).",
    ),
) -> None:
    """Register the prod daemon to auto-start at logon (Windows Task Scheduler)."""
    inst = _resolve(instance)
    if inst is not Instance.PROD:
        console.print(
            "[red]autostart is prod-only[/red] — the source instance is started "
            "by hand from the workspace."
        )
        raise typer.Exit(code=1)
    if sys.platform != "win32":
        console.print("[red]autostart is Windows-only.[/red]")
        raise typer.Exit(code=1)

    home = home_for(inst)
    exe = home / ".venv" / "Scripts" / "agent-core.exe"
    if not exe.exists():
        console.print(
            f"[red]prod daemon is not installed ({exe} missing).[/red]\n"
            "   Run [bold]agent-core daemon install[/bold] first."
        )
        raise typer.Exit(code=1)

    # getpass.getuser() resolves the username cross-platform from env vars
    # (USER / USERNAME / ...) without os.getlogin(), which fails on headless
    # hosts.
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

    should_start = start
    if should_start is None:
        should_start = typer.confirm("Start the prod daemon now?", default=False)
    if should_start:
        start_daemon(instance="prod")


@app.command()
def uninstall_autostart(instance: str | None = _INSTANCE_OPTION) -> None:
    """Remove the prod daemon auto-start task (Windows Task Scheduler)."""
    inst = _resolve(instance)
    if inst is not Instance.PROD:
        console.print("[red]autostart is prod-only.[/red]")
        raise typer.Exit(code=1)
    if sys.platform != "win32":
        console.print("[red]autostart is Windows-only.[/red]")
        raise typer.Exit(code=1)

    if autostart.uninstall_autostart():
        console.print(
            f"[green]removed autostart task '{autostart.TASK_NAME}'[/green]"
        )
    else:
        console.print(
            f"[yellow]no autostart task '{autostart.TASK_NAME}' to remove[/yellow]"
        )


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
