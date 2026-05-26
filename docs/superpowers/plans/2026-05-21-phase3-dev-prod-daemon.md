# Phase 3 — Dev/Prod Daemon Instance-Parameterization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fully isolated `dev` daemon instance (port 8788, home `~/.agent-core-dev/`, runs editable from the workspace `.venv`) alongside the existing `prod` daemon (port 8789, home `~/.agent-core/`), so daemon code can be iterated on without bouncing prod.

**Architecture:** A new pure `daemon/instance.py` resolves `prod` vs `dev` from a `--instance` flag / `AGENT_CORE_INSTANCE` env / default. A new pure `daemon/config_template.py` scaffolds a minimal `agent_core.yaml`. `daemon/cli.py` is rewired so every command resolves an instance and derives its home + python from it; `daemon init` is added; `install --instance dev` errors; `refresh --instance dev` is a plain bounce.

**Tech Stack:** Python 3.12, Typer, uv workspace, pytest. Build backend hatchling.

**Spec:** `docs/superpowers/specs/2026-05-21-phase3-dev-prod-daemon-design.md`

**Worktree:** already created at `.worktrees/phase3-dev-prod-daemon` on branch `feat/phase3-dev-prod-daemon` (off `origin/main`, has Phase 2.5 + v0.2.0). Run all commands from inside that worktree.

---

## File structure

### New files
- `packages/core/src/agent_core/daemon/instance.py` — pure: `Instance` enum, `resolve_instance`, `home_for`, `default_port`.
- `packages/core/src/agent_core/daemon/config_template.py` — pure: `build_default_config`.
- `packages/core/tests/test_daemon_instance.py` — unit tests for instance.py.
- `packages/core/tests/test_daemon_config_template.py` — unit tests for config_template.py.

### Modified files
- `packages/core/src/agent_core/daemon/cli.py` — path helpers become instance-aware; every command gains `--instance`; new `init` command; `install`/`refresh` branch on instance.
- `packages/core/tests/test_daemon_cli.py` — update `refresh` tests for the new signature; add `--instance dev` coverage.
- `docs/setup/daemon.md` — document the dev instance + the `refresh --instance dev` loop.

### Unchanged (referenced)
- `packages/core/src/agent_core/daemon/install.py` — `find_workspace_root` / `WorkspaceNotFoundError` are reused by cli.py; no edit.

---

## Task 1: `daemon/instance.py` — pure instance module (TDD)

**Files:**
- Create: `packages/core/src/agent_core/daemon/instance.py`
- Test: `packages/core/tests/test_daemon_instance.py`

- [ ] **Step 1: Write the failing tests**

Write to `packages/core/tests/test_daemon_instance.py`:
```python
"""Unit tests for daemon/instance.py — pure instance resolution."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.daemon.instance import (
    Instance,
    default_port,
    home_for,
    resolve_instance,
)


# ---- resolve_instance -------------------------------------------------------

def test_resolve_instance_defaults_to_prod() -> None:
    assert resolve_instance(flag=None, env=None) is Instance.PROD


def test_resolve_instance_env_selects_dev() -> None:
    assert resolve_instance(flag=None, env="dev") is Instance.DEV


def test_resolve_instance_flag_beats_env() -> None:
    # flag says prod, env says dev — flag wins
    assert resolve_instance(flag="prod", env="dev") is Instance.PROD


def test_resolve_instance_is_case_insensitive() -> None:
    assert resolve_instance(flag="DEV", env=None) is Instance.DEV


def test_resolve_instance_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown instance"):
        resolve_instance(flag="staging", env=None)


# ---- home_for ---------------------------------------------------------------

def test_home_for_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_CORE_HOME", raising=False)
    assert home_for(Instance.PROD) == Path.home() / ".agent-core"


def test_home_for_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_CORE_HOME", raising=False)
    assert home_for(Instance.DEV) == Path.home() / ".agent-core-dev"


def test_home_for_honors_agent_core_home_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    # Override wins for BOTH instances.
    assert home_for(Instance.PROD) == tmp_path
    assert home_for(Instance.DEV) == tmp_path


# ---- default_port -----------------------------------------------------------

def test_default_port_prod() -> None:
    assert default_port(Instance.PROD) == 8789


def test_default_port_dev() -> None:
    assert default_port(Instance.DEV) == 8788
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_instance.py -v`
Expected: `ModuleNotFoundError: No module named 'agent_core.daemon.instance'`

- [ ] **Step 3: Implement `daemon/instance.py`**

Write to `packages/core/src/agent_core/daemon/instance.py`:
```python
"""Daemon instance selection — pure resolution of prod vs dev.

An "instance" picks the daemon's home directory and default bus port.
Resolution precedence (most specific wins):
  1. explicit --instance flag
  2. AGENT_CORE_INSTANCE env var
  3. default: prod

AGENT_CORE_HOME is a separate escape hatch (used by tests): when set,
home_for() returns it directly, bypassing the instance -> home mapping.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path


class Instance(StrEnum):
    """The daemon instance: production or development."""

    PROD = "prod"
    DEV = "dev"


def resolve_instance(*, flag: str | None, env: str | None) -> Instance:
    """Resolve the daemon instance from a CLI flag and an env var.

    `flag` wins over `env`; `env` wins over the default (`prod`). Raises
    ValueError on an unrecognized value.
    """
    raw = (flag or env or "prod").lower()
    try:
        return Instance(raw)
    except ValueError:
        raise ValueError(
            f"unknown instance {raw!r} — expected 'prod' or 'dev'"
        ) from None


def home_for(instance: Instance) -> Path:
    """Return the home directory for an instance.

    AGENT_CORE_HOME, when set, overrides the mapping entirely (test
    escape hatch). Otherwise prod -> ~/.agent-core/, dev ->
    ~/.agent-core-dev/.
    """
    override = os.environ.get("AGENT_CORE_HOME")
    if override:
        return Path(override)
    name = ".agent-core" if instance is Instance.PROD else ".agent-core-dev"
    return Path.home() / name


def default_port(instance: Instance) -> int:
    """Default bus port for an instance (used by `daemon init` scaffolding)."""
    return 8789 if instance is Instance.PROD else 8788
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_instance.py -v`
Expected: 10 tests pass.

- [ ] **Step 5: Lint + typecheck**

Run: `uv run --no-sync ruff check packages/core/src/agent_core/daemon/instance.py packages/core/tests/test_daemon_instance.py && uv run --no-sync mypy packages/core/src/agent_core/daemon/instance.py`
Expected: no errors. If ruff reports fixable issues, run it with `--fix`.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/daemon/instance.py packages/core/tests/test_daemon_instance.py
git commit -m "feat(daemon): instance.py — pure prod/dev resolution"
```

---

## Task 2: `daemon/config_template.py` — pure config scaffold (TDD)

**Files:**
- Create: `packages/core/src/agent_core/daemon/config_template.py`
- Test: `packages/core/tests/test_daemon_config_template.py`

- [ ] **Step 1: Write the failing tests**

Write to `packages/core/tests/test_daemon_config_template.py`:
```python
"""Unit tests for daemon/config_template.py — minimal agent_core.yaml scaffold."""
from __future__ import annotations

from pathlib import Path

import yaml

from agent_core.daemon.config_template import build_default_config
from agent_core.daemon.instance import Instance


def test_build_default_config_prod_port(tmp_path: Path) -> None:
    text = build_default_config(instance=Instance.PROD, home=tmp_path)
    data = yaml.safe_load(text)
    assert data["http"]["bind_port"] == 8789


def test_build_default_config_dev_port(tmp_path: Path) -> None:
    text = build_default_config(instance=Instance.DEV, home=tmp_path)
    data = yaml.safe_load(text)
    assert data["http"]["bind_port"] == 8788


def test_build_default_config_storage_path_under_home(tmp_path: Path) -> None:
    text = build_default_config(instance=Instance.DEV, home=tmp_path)
    data = yaml.safe_load(text)
    assert data["bus"]["storage_path"] == str(tmp_path / "bus.sqlite")


def test_build_default_config_is_parseable_yaml(tmp_path: Path) -> None:
    text = build_default_config(instance=Instance.PROD, home=tmp_path)
    data = yaml.safe_load(text)  # must not raise
    assert data["http"]["bind_host"] == "127.0.0.1"
    assert isinstance(data["endpoints"], list)
    assert data["endpoints"][0]["type"] == "builtin.stub"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_config_template.py -v`
Expected: `ModuleNotFoundError: No module named 'agent_core.daemon.config_template'`

- [ ] **Step 3: Implement `daemon/config_template.py`**

Write to `packages/core/src/agent_core/daemon/config_template.py`:
```python
"""Pure generator for a minimal daemon `agent_core.yaml`.

Used by `agent-core daemon init` to scaffold a fresh config for an
instance. Minimal by design: bus + http (correct port) + one stub
endpoint. Specific endpoints are added by hand when they are being
tested.
"""

from __future__ import annotations

from pathlib import Path

from agent_core.daemon.instance import Instance, default_port


def build_default_config(*, instance: Instance, home: Path) -> str:
    """Return the text of a minimal `agent_core.yaml` for `instance`.

    `storage_path` points inside `home`; `bind_port` is the instance
    default (8789 prod / 8788 dev).
    """
    port = default_port(instance)
    storage = home / "bus.sqlite"
    return (
        "bus:\n"
        f"  storage_path: {storage}\n"
        "\n"
        "http:\n"
        "  bind_host: 127.0.0.1\n"
        f"  bind_port: {port}\n"
        "\n"
        "endpoints:\n"
        "  - type: builtin.stub\n"
        "    name: stub\n"
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_config_template.py -v`
Expected: 4 tests pass.

- [ ] **Step 5: Lint + typecheck**

Run: `uv run --no-sync ruff check packages/core/src/agent_core/daemon/config_template.py packages/core/tests/test_daemon_config_template.py && uv run --no-sync mypy packages/core/src/agent_core/daemon/config_template.py`
Expected: no errors. Run ruff with `--fix` if it reports fixable issues.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/daemon/config_template.py packages/core/tests/test_daemon_config_template.py
git commit -m "feat(daemon): config_template.py — minimal agent_core.yaml scaffold"
```

---

## Task 3: Rewire `daemon/cli.py` for instances

This is the central task. The whole file is rewritten: path helpers become
instance-aware, every command gains `--instance`, `daemon init` is added,
and `install`/`refresh` branch on instance. Because `_home()` changing
signature breaks every caller at once, this must land as one atomic change.

**Files:**
- Modify: `packages/core/src/agent_core/daemon/cli.py` (full rewrite)
- Modify: `packages/core/tests/test_daemon_cli.py` (update refresh tests + add instance coverage)

- [ ] **Step 1: Write the new `daemon/cli.py`**

Replace the entire contents of `packages/core/src/agent_core/daemon/cli.py` with:
```python
"""`agent-core daemon` — process supervision for the bus daemon.

start: spawn `agent-core bus run --config <home>/agent_core.yaml`
       detached; write the resulting PID to <home>/daemon.pid.
stop:  read the PID file, kill the process tree, remove the PID file.
status: report running/not-running, PID, last 20 lines of daemon.log.
install: populate the prod daemon venv from a GitHub Release.
refresh: stop -> (prod: install) -> start.
init:   scaffold a minimal agent_core.yaml for an instance.

Two instances are supported (Phase 3): `prod` (port 8789, home
~/.agent-core/, release-installed venv) and `dev` (port 8788, home
~/.agent-core-dev/, runs editable from the workspace .venv). The
instance is resolved per-invocation from `--instance` / the
AGENT_CORE_INSTANCE env var / default `prod`. AGENT_CORE_HOME still
overrides the home dir directly (test escape hatch).
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

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
    None, "--instance", help="Daemon instance: 'prod' (default) or 'dev'."
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
    """Path to the workspace .venv python (the dev daemon interpreter).

    Resolves the workspace root from the current directory. Raises
    WorkspaceNotFoundError if the cwd is not inside the agent_core repo.
    """
    workspace = find_workspace_root(Path.cwd())
    if sys.platform == "win32":
        return workspace / ".venv" / "Scripts" / "python.exe"
    return workspace / ".venv" / "bin" / "python"


def _daemon_python(instance: Instance, home: Path) -> str:
    """Return the interpreter the supervisor should spawn the bus with.

    prod: the prod daemon venv if present, else sys.executable (fallback).
    dev:  the workspace .venv python (editable install). Raises
          WorkspaceNotFoundError if not inside the repo.
    """
    if instance is Instance.DEV:
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
            f"[red]dev daemon needs the workspace .venv but {exc}[/red]\n"
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

    # Diagnostic: install stamp — prod only (dev is editable, no stamp).
    if inst is Instance.PROD:
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
    """Populate the prod daemon venv from a GitHub Release artifact."""
    inst = _resolve(instance)

    if inst is Instance.DEV:
        console.print(
            "[red]the dev instance is not installed[/red] — it runs editable "
            "from the workspace .venv.\n"
            "   Just run [bold]agent-core daemon start --instance dev[/bold]."
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

    console.print(f"[green]prod daemon updated to {tag}[/green]")


@app.command()
def refresh(
    instance: str | None = _INSTANCE_OPTION,
    release: str | None = typer.Option(
        None,
        "--release",
        help="Release tag to install (prod only). Default: latest release.",
    ),
) -> None:
    """Stop daemon -> (prod: install release) -> start daemon.

    For dev this is a plain bounce: the dev daemon runs editable from the
    workspace .venv, so a stop/start picks up the latest source edits.
    """
    inst = _resolve(instance)
    stop(instance=instance)
    if inst is Instance.PROD:
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
```

Notes for the implementer:
- `_installed_version` (the subprocess-based wheel-metadata query) is **removed**. `status` now prints `stamp.installed_version` directly — Phase 2.5 added that field to the stamp, so the subprocess call is redundant. Removing it also drops a slow call from `status`.
- `_daemon_python_path` and `_daemon_venv_exists` (Phase 2.5 helpers) are removed; `_prod_venv_python` replaces both usages.
- The B2 "fallback — no daemon venv" warning is dropped: `status` now just prints `running from: <path>`. The warning's value was telling you the prod venv was missing; with `daemon init` + `install` as explicit steps that's less of a footgun, and dev legitimately runs from a non-prod-venv python. Keeping `status` quiet and factual is cleaner.

- [ ] **Step 2: Verify the module imports**

Run: `uv run --no-sync python -c "from agent_core.daemon import cli; print('cli imports OK')"`
Expected: `cli imports OK`

- [ ] **Step 3: Run the existing daemon CLI test suite to find what broke**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_cli.py -v`
Expected: most tests pass (backward compat — no `--instance` resolves to prod). The `refresh` tests will FAIL because their fake `install`/`stop`/`start` functions have the old signature (no `instance` kwarg). Note which tests fail.

- [ ] **Step 4: Fix the `refresh` tests for the new signature**

In `packages/core/tests/test_daemon_cli.py`, the refresh tests monkeypatch
`stop`, `install`, `start`. Update each fake to accept the new keyword
arguments. Replace the body of `test_refresh_calls_stop_install_start_in_order`:
```python
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
```

Replace the body of `test_refresh_aborts_start_when_install_fails`:
```python
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
```

If `test_install_release_orchestrates_full_chain` references the old
`status`/`_installed_version`, leave it — it asserts on stamp JSON, which
is unchanged. If any test imports `_daemon_python`, `_daemon_venv_exists`,
or `_installed_version` directly, update it: `_daemon_venv_exists` and
`_installed_version` no longer exist; `_daemon_python` now takes
`(instance, home)`. For `test_daemon_python_*` tests, rewrite them as:
```python
def test_daemon_python_prod_uses_prod_venv_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    from agent_core.daemon.cli import _daemon_python
    from agent_core.daemon.instance import Instance

    if sys.platform == "win32":
        venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("# placeholder")

    assert _daemon_python(Instance.PROD, tmp_path) == str(venv_python)


def test_daemon_python_prod_falls_back_to_sys_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    from agent_core.daemon.cli import _daemon_python
    from agent_core.daemon.instance import Instance

    assert _daemon_python(Instance.PROD, tmp_path) == sys.executable
```

Delete `test_status_shows_fallback_warning_when_no_daemon_venv` and
`test_status_b2_no_false_positive_when_invoked_from_daemon_venv` — the
"fallback" warning was removed in Step 1, so those tests no longer
describe real behavior. Leave a one-line comment in their place:
```python
# Phase 3 removed the status "fallback" warning — status is now factual
# only. The B2 false-positive it guarded against is moot.
```

- [ ] **Step 5: Add new instance-coverage tests**

Append to `packages/core/tests/test_daemon_cli.py`:
```python
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
```

Note: `test_start_refuses_without_config` (an existing test) asserts the
old message text `"agent_core.yaml"`. The new message still contains the
config path (which ends in `agent_core.yaml`), so it should still pass.
If it asserts other old wording, update it to match the new message
(`Run agent-core daemon init`).

- [ ] **Step 6: Run the full daemon test suite**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_cli.py packages/core/tests/test_daemon_instance.py packages/core/tests/test_daemon_config_template.py packages/core/tests/test_daemon_install.py packages/core/tests/test_daemon_release.py -v`
Expected: all pass. Fix any failures (most likely: a test referencing a removed helper, or old message wording).

- [ ] **Step 7: Lint + typecheck**

Run: `uv run --no-sync ruff check packages/core && uv run --no-sync mypy packages/core/src/agent_core/daemon/cli.py`
Expected: no errors. Run ruff `--fix` for fixable issues.

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/agent_core/daemon/cli.py packages/core/tests/test_daemon_cli.py
git commit -m "feat(daemon): --instance prod/dev on every command + daemon init"
```

---

## Task 4: Documentation — `docs/setup/daemon.md`

**Files:**
- Modify: `docs/setup/daemon.md`

- [ ] **Step 1: Read the current daemon doc**

Run: `cat docs/setup/daemon.md`
Note its structure and headings.

- [ ] **Step 2: Add a "Dev instance" section**

Append to `docs/setup/daemon.md` a new section (adjust heading depth to
match the file's existing style):
```markdown
## Dev instance (Phase 3)

The daemon supports two instances, selected with `--instance`:

| | `prod` (default) | `dev` |
|---|---|---|
| Home | `~/.agent-core/` | `~/.agent-core-dev/` |
| Bus port | 8789 | 8788 |
| Code source | release artifacts (`daemon install`) | the workspace `.venv` (editable) |

The dev instance lets you iterate on daemon code without bouncing the
prod daemon that Pepper and Wren depend on.

### One-time dev setup

```
agent-core daemon init --instance dev      # scaffolds ~/.agent-core-dev/agent_core.yaml
agent-core daemon start --instance dev     # runs from the workspace .venv
```

`start --instance dev` must be run from inside the agent_core repo (it
resolves the workspace `.venv` for the daemon interpreter).

### The dev loop

Edit daemon code in the repo, then:

```
agent-core daemon refresh --instance dev
```

For dev, `refresh` is a plain stop + start — no install step. The dev
daemon runs editable from the workspace `.venv`, so the restart picks up
your latest source edits.

`agent-core daemon install --instance dev` is intentionally an error:
the dev instance is never installed.

Instance can also be set with the `AGENT_CORE_INSTANCE` env var. With
neither the flag nor the env var, every command resolves to `prod` —
existing behavior is unchanged.
```

- [ ] **Step 3: Commit**

```bash
git add docs/setup/daemon.md
git commit -m "docs(daemon): document the dev instance + refresh --instance dev loop"
```

---

## Task 5: Slow coexistence test

**Files:**
- Modify: `packages/core/tests/test_daemon_cli.py` (add one `slow`-marked test)

- [ ] **Step 1: Add the coexistence test**

Append to `packages/core/tests/test_daemon_cli.py`:
```python
@pytest.mark.slow
def test_prod_and_dev_daemons_coexist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A prod and a dev daemon run simultaneously on different ports,
    each isolated — stopping one does not disturb the other.

    Both instances are pointed at tmp homes via AGENT_CORE_HOME-style
    isolation: we drive each instance with its own home dir by invoking
    the commands with explicit AGENT_CORE_HOME set per call.
    """
    prod_home = tmp_path / "prod"
    dev_home = tmp_path / "dev"

    def _run(args: list[str], home: Path):
        monkeypatch.setenv("AGENT_CORE_HOME", str(home))
        return runner.invoke(daemon_app, args)

    # Scaffold minimal configs with distinct ports.
    assert _run(["init"], prod_home).exit_code == 0
    assert _run(["init", "--instance", "dev"], dev_home).exit_code == 0

    # Rewrite each config to a free, distinct port to avoid clashing with
    # a real daemon on 8789/8788.
    import yaml as _yaml
    for home, port in ((prod_home, 8991), (dev_home, 8992)):
        cfg = home / "agent_core.yaml"
        data = _yaml.safe_load(cfg.read_text())
        data["http"]["bind_port"] = port
        cfg.write_text(_yaml.safe_dump(data), encoding="utf-8")

    try:
        assert _run(["start"], prod_home).exit_code == 0
        assert _run(["start"], dev_home).exit_code == 0

        # Both alive.
        prod_status = _run(["status"], prod_home)
        dev_status = _run(["status"], dev_home)
        assert "is running" in prod_status.stdout
        assert "is running" in dev_status.stdout

        # Stop prod — dev must still be alive.
        assert _run(["stop"], prod_home).exit_code == 0
        assert "is running" in _run(["status"], dev_home).stdout
        assert "not running" in _run(["status"], prod_home).stdout
    finally:
        _run(["stop"], prod_home)
        _run(["stop"], dev_home)
```

Note: this test drives both instances through the `AGENT_CORE_HOME`
escape hatch (one home per call) rather than `--instance`, because the
test process can only hold one home at a time. It still proves the core
property: two daemons, two homes, two ports, independent lifecycle. The
`--instance` flag's mapping to homes is already covered by Task 1's
`home_for` tests.

- [ ] **Step 2: Run the slow test**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_cli.py::test_prod_and_dev_daemons_coexist -v -m slow`
Expected: PASS. (If the bus needs a real config with more than a stub
endpoint to bind HTTP, and the test fails on startup, check the daemon
log under each tmp home; the `builtin.stub` endpoint + `http` block
should be enough to bind a port.)

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/test_daemon_cli.py
git commit -m "test(daemon): slow coexistence test — prod + dev daemons run independently"
```

---

## Task 6: Final validation + PR

**Files:** none — verification only.

- [ ] **Step 1: Full `just check`**

Run: `just check`
Expected: PASS — lint, typecheck, contracts, all fast tests green.

- [ ] **Step 2: Review the commit history**

Run: `git log origin/main..HEAD --oneline`
Expected: ~7 commits (spec + Tasks 1-5), all conventional-commit messages.

- [ ] **Step 3: Push + open the PR**

```bash
git push -u origin feat/phase3-dev-prod-daemon
```

Then:
```bash
gh pr create --title "feat(daemon): Phase 3 — dev/prod daemon instance-parameterization" --body-file - <<'EOF'
## Summary
- New `--instance {prod|dev}` on every `agent-core daemon` command (default `prod`)
- `dev` instance: port 8788, home `~/.agent-core-dev/`, runs editable from the workspace `.venv`
- New pure modules `daemon/instance.py` + `daemon/config_template.py`
- New `daemon init` command scaffolds a minimal `agent_core.yaml`
- `install --instance dev` errors (dev is editable, never installed); `refresh --instance dev` is a plain bounce
- Backward compatible: no flag → `prod`, port 8789, unchanged

Spec: `docs/superpowers/specs/2026-05-21-phase3-dev-prod-daemon-design.md`
Plan: `docs/superpowers/plans/2026-05-21-phase3-dev-prod-daemon.md`

## Test plan
- [ ] All `phase1-main-gate` checks green (ubuntu + windows + integration + PR title)
- [ ] After merge: `agent-core daemon init --instance dev` then `daemon start --instance dev` on the box
EOF
```

- [ ] **Step 4: Drive CI green**

This repo's release-please/CI automation has a known gap (issue #107):
the `phase1-main-gate` required checks should fire normally for a
human-pushed branch like this one (the gap only affects bot-created
PRs). If for any reason a required check is missing, close + reopen the
PR to re-fire `pull_request` events, and edit the title once to fire
`pull_request_target` for the title lint.

Wait for all four checks (`check (ubuntu-latest)`, `check
(windows-latest)`, `integration`, `Validate PR title`) to pass. Do NOT
bypass the gate.

- [ ] **Step 5: Report the PR URL**

Report the PR URL and the green check status. Squash-merge is the human
step (the repo is squash-merge-only).

---

## Self-review check

Spec coverage:
- §2.1 precedence — Task 1 (`resolve_instance` tests) + Task 3 (`_resolve` wiring)
- §2.2 home/port mapping — Task 1 (`home_for`, `default_port` tests)
- §2.3 daemon python per instance — Task 3 (`_daemon_python`, `_workspace_venv_python`) + Task 3 Step 4 tests
- §3 CLI surface (every command + per-instance behavior) — Task 3
- §3.1 `daemon init` scaffold — Task 2 (`build_default_config`) + Task 3 (`init` command)
- §4 code structure (pure instance.py / config_template.py, impure cli.py) — Tasks 1, 2, 3
- §5 safety invariant — proven by Task 5 coexistence test
- §6 backward compatibility — Task 3 Step 5 (`test_start_without_config`, default-prod tests) + existing tests passing unchanged
- §7 testing strategy — Tasks 1, 2, 3, 5 cover every listed test group
- §8 rollout — Task 6 (PR) + Task 4 (docs for the one-time dev setup)

No spec section is uncovered. No placeholders. Type/name consistency
checked: `Instance`, `resolve_instance`, `home_for`, `default_port`,
`build_default_config`, `_resolve`, `_daemon_python`,
`_prod_venv_python`, `_workspace_venv_python` are used consistently
across all tasks.
