"""`agent-core daemon` — process supervision for the bus daemon.

start: spawn `agent-core bus run --config <home>/agent_core.yaml`
       detached; write the resulting PID to <home>/daemon.pid.
stop:  read the PID file, kill the process tree, remove the PID file.
status: report running/not-running, PID, last 20 lines of daemon.log.

The daemon's home directory defaults to ~/.agent-core/ but can be
overridden via the AGENT_CORE_HOME env var (used by tests).
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from agent_core.daemon.install import InstallStamp, read_stamp, write_stamp
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

app = typer.Typer(help="Daemon process supervision: start, stop, status, install, refresh.")
console = Console()


def _home() -> Path:
    """Return ~/.agent-core/ unless AGENT_CORE_HOME overrides it."""
    override = os.environ.get("AGENT_CORE_HOME")
    if override:
        return Path(override)
    return Path.home() / ".agent-core"


def _pid_path() -> Path:
    return _home() / "daemon.pid"


def _config_path() -> Path:
    return _home() / "agent_core.yaml"


def _log_path() -> Path:
    return _home() / "daemon.log"


def _daemon_python() -> str:
    """Return the daemon's preferred interpreter, with fallback.

    Prefers `~/.agent-core/.venv/Scripts/python.exe` (Windows) or
    `~/.agent-core/.venv/bin/python` (POSIX) when present; falls back
    to `sys.executable` (today's behavior) when the daemon venv is
    missing. This keeps the supervisor working unchanged on machines
    that haven't run `agent-core daemon install` yet.
    """
    if sys.platform == "win32":
        candidate = _home() / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = _home() / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _daemon_python_path() -> Path:
    """The fixed path to the daemon venv's python, regardless of existence."""
    if sys.platform == "win32":
        return _home() / ".venv" / "Scripts" / "python.exe"
    return _home() / ".venv" / "bin" / "python"


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


def _installed_version(python: str) -> str:
    """Best-effort: the agent-core version installed in the daemon venv.

    Read from the wheel metadata (the true 'what's running' signal — the
    version is VCS-derived at build time, Phase 2). Never raises.
    """
    try:
        result = subprocess.run(
            [python, "-c",
             "import importlib.metadata as m; print(m.version('agent-core'))"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


@app.command()
def start() -> None:
    """Spawn `agent-core bus run` detached, write the PID file."""
    pid_file = _pid_path()
    cfg = _config_path()
    log_file = _log_path()

    if not cfg.exists():
        console.print(
            f"[red]No daemon config at {cfg}.[/red] "
            f"Create it manually for v1 (sub-project C handles auto-init)."
        )
        raise typer.Exit(code=1)

    existing = read_pid(pid_file)
    if existing is not None and is_alive(existing):
        console.print(f"[yellow]daemon already running (PID: {existing})[/yellow]")
        raise typer.Exit(code=1)
    if existing is not None:
        # Stale.
        remove_pid(pid_file)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_file, "ab", buffering=0)

    proc = subprocess.Popen(
        [_daemon_python(), "-m", "agent_core.cli", "bus", "run", "--config", str(cfg)],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    write_pid(pid_file, proc.pid)
    console.print(f"[green]daemon started (PID: {proc.pid})[/green]")


@app.command()
def stop() -> None:
    """Kill the daemon and clean the PID file. Idempotent."""
    pid_file = _pid_path()
    pid = read_pid(pid_file)
    if pid is None:
        console.print("[yellow]daemon is not running[/yellow]")
        return
    if not is_alive(pid):
        console.print("[yellow]daemon is not running (stale PID file removed)[/yellow]")
        remove_pid(pid_file)
        return
    kill_tree(pid)
    remove_pid(pid_file)
    console.print(f"[green]daemon stopped (PID: {pid})[/green]")


@app.command()
def status() -> None:
    """Report daemon liveness and tail the log."""
    pid_file = _pid_path()
    log_file = _log_path()
    pid = read_pid(pid_file)

    if pid is None:
        console.print("[yellow]daemon is not running[/yellow]")
        return
    if not is_alive(pid):
        console.print("[yellow]daemon is not running (stale PID file removed)[/yellow]")
        remove_pid(pid_file)
        return

    console.print(f"[green]daemon is running (PID: {pid})[/green]")

    # Diagnostic: which interpreter the supervisor would use today.
    daemon_py = _daemon_python()
    suffix = ""
    if daemon_py == sys.executable:
        suffix = (
            " [dim red](fallback — vulnerable to uv sync; "
            "run `agent-core daemon install`)[/dim red]"
        )
    console.print(f"running from: {daemon_py}{suffix}")

    # Diagnostic: stamp metadata, if present.
    stamp = read_stamp(_home())
    if stamp is not None:
        console.print(f"installed at: {stamp.installed_at}")
        console.print(f"installed sha: {stamp.installed_sha}")
        console.print(f"installed version: {_installed_version(daemon_py)}")

    if log_file.exists():
        console.print("\n[dim]--- last 20 lines of daemon.log ---[/dim]")
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-20:]:
            console.print(line)


@app.command()
def install(
    release: str | None = typer.Option(
        None,
        "--release",
        help="Release tag to install (e.g. v0.1.0). Default: latest release.",
    ),
) -> None:
    """Populate ~/.agent-core/.venv/ from a GitHub Release artifact."""
    pid_file = _pid_path()
    existing = read_pid(pid_file)
    if existing is not None and is_alive(existing):
        console.print(
            f"[red]daemon is currently running (PID {existing}).[/red]\n"
            "   • Run [bold]agent-core daemon stop[/bold] and re-run install, or\n"
            "   • Run [bold]agent-core daemon refresh[/bold] to stop/install/start in one step."
        )
        raise typer.Exit(code=1)

    home = _home()
    home.mkdir(parents=True, exist_ok=True)

    # Resolve version (None → latest).
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

    venv_python = _daemon_python_path()

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

    console.print(f"[green]daemon updated to {tag}[/green]")


@app.command()
def refresh(
    release: str | None = typer.Option(
        None,
        "--release",
        help="Release tag to install (e.g. v0.1.0). Default: latest release.",
    ),
) -> None:
    """Stop daemon → install release artifacts → start daemon."""
    stop()
    install(release=release)
    start()
