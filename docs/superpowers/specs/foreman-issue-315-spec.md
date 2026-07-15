# Spec: per-being pinned venv builder + absolute uv resolution (issue #315)

## Goal

Implement `agent-core venv build <target>` (and `upgrade` as an alias) so each being and the daemon gets its own pinned venv at a stable path (`~/.<target>/.venv`), and venv builds never depend on a bare `uv` being on the launching process's PATH. This is mechanism M1 of the interpreter/venv-resolution design and closes the `[P1]` bare-`uv` and `[P1]` shared-mutable-venv world-class-eval items.

Design authority: [`docs/superpowers/specs/2026-07-14-interpreter-venv-resolution-design.md`](docs/superpowers/specs/2026-07-14-interpreter-venv-resolution-design.md) (D1–D6, M1). Issue: https://github.com/jeffrichley/agent_core/issues/315. **Blocked by #310** (sidecar PyPI publish); the install step will fail in production until that lands.

## Acceptance criteria

- `agent-core venv build <target>` and `agent-core venv upgrade <target>` are runnable commands; `upgrade` is an alias that calls the same logic.
- `<target>` is a being name (e.g. `wren`, `pepper`) or the literal string `daemon`.
- **uv resolution**: `resolve_uv()` checks `~/.cargo/bin/uv[.exe]` then `~/.local/bin/uv[.exe]` then `shutil.which("uv")`; it never assumes PATH first. If not found, exits 1 with a message that includes an install URL.
- **Versioned dir created**: `<home>/.agent-core/venvs/<version>/` for beings; `<home>/venvs/<version>/` for daemon (where `<version>` = `importlib.metadata.version("agent-core")`).
- **Sidecar install**: `uv pip install agent-core-busproxy agent-core-channel agent-core-notify` runs inside the versioned dir using the resolved absolute `uv` path.
- **Verification before swap**: `python -c "import agent_core_busproxy, agent_core_channel, agent_core_notify"` runs in the new venv; if it exits non-zero the command exits 1 and the stable symlink/junction is **not** modified.
- **Atomic repoint**: only after verification passes is `<home>/.venv` → new versioned dir created or updated. On POSIX: `os.symlink` + `os.rename` (atomic). On Windows: delete existing junction/symlink then `mklink /J`.
- **Old versioned dir untouched**: the directory the stable path previously pointed to is left intact (GC deferred to C2-3).
- Unit tests in `packages/core/tests/test_venv_builder.py` cover: all four `resolve_uv` probe paths, both path-layout functions for being vs daemon target, `create_venv` idempotency, `install_sidecars` command shape, `verify_sidecars` pass/fail, `atomic_repoint` POSIX creates/replaces symlink + leaves old dir, `build_being_venv` happy path and verify-fails-aborts-before-repoint.
- CLI tests in `packages/core/tests/test_venv_cli.py` cover: `build` exits 0 on happy path, `build` exits 1 on `UvNotFoundError`, `build` exits 1 on `SidecarVerifyError`, `upgrade` invokes identical code path.
- `just check` passes (lint-clean via `ruff check packages/core` + full test suite).

## Approach

No GoF pattern applies. Guiding principles: **SRP** — `builder.py` is a pure-functions module with injected subprocess calls; the CLI layer in `cli.py` is its only consumer. **DIP** — every `subprocess.run` call is accepted via a `runner` parameter (default `subprocess.run`) so tests monkeypatch without touching the process, matching `daemon/release.py`'s `fetcher` pattern exactly.

**New package `agent_core.venv`** at `packages/core/src/agent_core/venv/` follows the `agent_core.daemon` precedent for subsystem modules inside the core package.

**Path layout** (D2 — stable per-being path, versioned swap target, atomic junction):

For a being `wren` (home = `~/.wren/`):
```
~/.wren/.agent-core/venvs/0.8.0/   ← real venv, versioned dir
~/.wren/.venv                        ← STABLE symlink/junction → above
```

For daemon (home = `~/.agent-core/` per `instance.py:home_for(Instance.PROD)`):
```
~/.agent-core/venvs/0.8.0/          ← real venv, versioned dir
~/.agent-core/.venv                  ← STABLE symlink/junction → above
```

The daemon's versioned dir lives at `<home>/venvs/<version>/` (not `<home>/.agent-core/venvs/<version>/`) because the daemon's home IS `~/.agent-core/` — adding another `.agent-core/` segment would produce the ugly double-nested `~/.agent-core/.agent-core/venvs/`. `versioned_venv_dir()` special-cases `target == "daemon"` to suppress the nested segment. See Open questions.

**uv resolution** (`resolve_uv()`): probes `~/.cargo/bin/uv[.exe]` → `~/.local/bin/uv[.exe]` → `shutil.which("uv")`. Raises `UvNotFoundError` with a `curl … astral.sh/uv` install hint if all three fail. Returns an absolute `Path` via `.resolve()`.

**Atomic repoint on POSIX**: writes `<stable>.tmp` symlink then `os.rename(tmp, stable)` — atomic on any POSIX filesystem. The `.tmp` path is cleaned up if left from a prior interrupted run.

**Windows junction**: checks `Path.is_junction()` (Python 3.12+) and `Path.is_symlink()` on the existing stable path; removes it with `os.unlink()`; then calls `cmd /c mklink /J <stable> <target>`. Not atomic, but build-alongside-then-repoint means the old venv is live until the replacement is verified.

**Wiring into main CLI**: add `from agent_core.venv.cli import venv_app` and `app.add_typer(venv_app, name="venv")` to `packages/core/src/agent_core/cli.py`, matching the `daemon_app` pattern at lines 47–48 of that file.

## Sub-requests (topologically sorted)

1. **Create `packages/core/src/agent_core/venv/__init__.py`** — empty package marker.

2. **Create `packages/core/src/agent_core/venv/builder.py`** — `UvNotFoundError`, `SidecarVerifyError`, `SIDECAR_PACKAGES`, `resolve_uv`, `home_for_target`, `versioned_venv_dir`, `stable_venv_path`, `python_in_venv`, `create_venv`, `install_sidecars`, `verify_sidecars`, `atomic_repoint`, `build_being_venv`. See File-level changes for exact content.

3. **Create `packages/core/src/agent_core/venv/cli.py`** — `venv_app` Typer with `build` and `upgrade` commands. See File-level changes for exact content.

4. **Modify `packages/core/src/agent_core/cli.py`** — add import and `app.add_typer` call for `venv_app`. Exact diff in File-level changes.

5. **Create `packages/core/tests/test_venv_builder.py`** — unit tests for all `builder.py` functions. See File-level changes for exact content.

6. **Create `packages/core/tests/test_venv_cli.py`** — CLI-layer tests via Typer's `CliRunner`. See File-level changes for exact content.

## File-level changes

| File | Change |
|------|--------|
| `packages/core/src/agent_core/venv/__init__.py` | **New** — empty |
| `packages/core/src/agent_core/venv/builder.py` | **New** — all builder pure functions (see below) |
| `packages/core/src/agent_core/venv/cli.py` | **New** — `venv_app` with `build` + `upgrade` commands |
| `packages/core/src/agent_core/cli.py` | **Modify** — two-line addition: import + `add_typer` |
| `packages/core/tests/test_venv_builder.py` | **New** — unit tests for builder.py |
| `packages/core/tests/test_venv_cli.py` | **New** — CLI tests |

### Exact content: `packages/core/src/agent_core/venv/builder.py`

```python
"""Per-being and daemon venv builder — C2-1, issue #315.

Implements M1 from the interpreter/venv-resolution design:
  1. Resolve uv to an absolute path (D5).
  2. Create <home>/.agent-core/venvs/<version>/ (D1, D2).
  3. uv pip install slim sidecar set from PyPI (D4).
  4. Verify sidecars import before touching the stable path (M1, D3).
  5. Atomically repoint <home>/.venv → versioned dir (D2, D3).

All subprocess calls accept an injected ``runner`` (default: subprocess.run)
so unit tests can monkeypatch without spawning real processes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib.metadata import version as _metadata_version
from pathlib import Path
from typing import Any

SIDECAR_PACKAGES: list[str] = [
    "agent-core-busproxy",
    "agent-core-channel",
    "agent-core-notify",
]


class UvNotFoundError(Exception):
    """uv binary not found at any known location. Message includes install instructions."""


class SidecarVerifyError(Exception):
    """The sidecar import verification step failed in the new venv."""


def resolve_uv() -> Path:
    """Find the uv binary and return its absolute path.

    Probe order:
    1. ~/.cargo/bin/uv[.exe]   — default rustup/cargo install location
    2. ~/.local/bin/uv[.exe]   — pipx / manual install
    3. shutil.which("uv")      — PATH fallback

    Raises UvNotFoundError with an actionable install hint if not found (D5).
    """
    suffix = ".exe" if sys.platform == "win32" else ""
    candidates: list[Path] = [
        Path.home() / ".cargo" / "bin" / f"uv{suffix}",
        Path.home() / ".local" / "bin" / f"uv{suffix}",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    found = shutil.which("uv")
    if found:
        return Path(found).resolve()

    raise UvNotFoundError(
        "uv not found. Install it with:\n"
        "  curl -LsSf https://astral.sh/uv/install.sh | sh\n"
        "or visit: https://docs.astral.sh/uv/getting-started/installation/"
    )


def home_for_target(target: str) -> Path:
    """Map a CLI target name to its home directory.

    'daemon' → ~/.agent-core/  (prod daemon home; mirrors instance.py:home_for(PROD))
    '<being>' → ~/.<being>/
    """
    if target == "daemon":
        return Path.home() / ".agent-core"
    return Path.home() / f".{target}"


def versioned_venv_dir(home: Path, version: str, *, target: str) -> Path:
    """Return the path for the new versioned venv directory.

    Being:  <home>/.agent-core/venvs/<version>/
    Daemon: <home>/venvs/<version>/

    The daemon special-case avoids double-nesting: the daemon's home IS
    ~/.agent-core/, so we omit the extra .agent-core segment.
    """
    if target == "daemon":
        return home / "venvs" / version
    return home / ".agent-core" / "venvs" / version


def stable_venv_path(home: Path) -> Path:
    """Return the stable venv path: <home>/.venv (never changes across upgrades)."""
    return home / ".venv"


def python_in_venv(venv_dir: Path) -> Path:
    """Return the Python interpreter path inside venv_dir."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def create_venv(
    uv: Path,
    venv_dir: Path,
    *,
    python_version: str = "3.12",
    runner: Any = subprocess.run,
) -> None:
    """Create a venv at venv_dir using the absolute uv path.

    Idempotent: no-op if the venv python already exists.
    Creates parent directories as needed.
    """
    python_path = python_in_venv(venv_dir)
    if python_path.exists():
        return
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    runner(
        [str(uv), "venv", str(venv_dir), "--python", python_version],
        check=True,
    )


def install_sidecars(
    uv: Path,
    venv_python: Path,
    *,
    runner: Any = subprocess.run,
) -> None:
    """Install agent-core-busproxy, agent-core-channel, agent-core-notify from PyPI.

    Uses the absolute uv path resolved by resolve_uv() (D5). Requires
    C1-2 (#310) PyPI publish before this can succeed in production.
    """
    runner(
        [
            str(uv), "pip", "install",
            "--python", str(venv_python),
            *SIDECAR_PACKAGES,
        ],
        check=True,
    )


def verify_sidecars(
    venv_python: Path,
    *,
    runner: Any = subprocess.run,
) -> None:
    """Verify the three sidecar packages import cleanly in the new venv.

    Called BEFORE atomic_repoint (D3) so a broken install never touches
    the working stable venv. Raises SidecarVerifyError on non-zero exit.
    """
    result = runner(
        [
            str(venv_python),
            "-c",
            "import agent_core_busproxy, agent_core_channel, agent_core_notify",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SidecarVerifyError(
            f"sidecar import failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )


def atomic_repoint(stable: Path, target_dir: Path) -> None:
    """Point the stable venv symlink/junction at target_dir.

    POSIX: writes a <stable>.tmp symlink then os.rename() — atomic on
    any POSIX filesystem. The .tmp path is removed first if present
    (can be left from a prior interrupted run).

    Windows: removes any existing junction (Path.is_junction(), Python
    3.12+) or symlink, then creates a directory junction via mklink /J.
    Not atomic, but build-alongside-then-repoint means the old venv is
    live until the replacement is verified.

    Does NOT remove the previously-targeted versioned dir — GC is C2-3.
    """
    stable.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        if stable.is_junction() or stable.is_symlink():
            os.unlink(stable)
        elif stable.is_dir():
            stable.rmdir()
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(stable), str(target_dir)],
            check=True,
            capture_output=True,
        )
    else:
        tmp = stable.parent / (stable.name + ".tmp")
        if tmp.is_symlink():
            tmp.unlink()
        os.symlink(target_dir, tmp)
        os.rename(tmp, stable)


def build_being_venv(
    target: str,
    *,
    python_version: str = "3.12",
    runner: Any = subprocess.run,
) -> Path:
    """Orchestrate the full per-being (or daemon) venv build.

    Steps (D1–D6, M1):
    1. Resolve uv to an absolute path.
    2. Compute home, versioned_dir, stable_path from target + installed version.
    3. Create the versioned venv directory.
    4. Install the sidecar packages.
    5. Verify sidecars import cleanly.
    6. Atomically repoint the stable symlink/junction.

    Returns the stable venv path (<home>/.venv).
    Raises UvNotFoundError, SidecarVerifyError, or subprocess.CalledProcessError.
    """
    uv = resolve_uv()
    version = _metadata_version("agent-core")

    home = home_for_target(target)
    venv_dir = versioned_venv_dir(home, version, target=target)
    stable = stable_venv_path(home)
    venv_python = python_in_venv(venv_dir)

    create_venv(uv, venv_dir, python_version=python_version, runner=runner)
    install_sidecars(uv, venv_python, runner=runner)
    verify_sidecars(venv_python, runner=runner)
    atomic_repoint(stable, venv_dir)

    return stable
```

### Exact content: `packages/core/src/agent_core/venv/cli.py`

```python
"""agent-core venv — per-being and daemon pinned venv CLI (C2-1, issue #315)."""

from __future__ import annotations

import typer
from rich.console import Console

from agent_core.venv.builder import SidecarVerifyError, UvNotFoundError, build_being_venv

venv_app = typer.Typer(
    name="venv",
    help="Per-being and daemon pinned venv builder.",
    no_args_is_help=True,
)
console = Console()

_TARGET_ARG = typer.Argument(
    ...,
    help="Being name (e.g. 'wren', 'pepper') or 'daemon'.",
)
_PYTHON_OPT = typer.Option(
    "3.12",
    "--python",
    help="Python version for the venv.",
)


def _do_build(target: str, python_version: str) -> None:
    """Shared implementation for build and upgrade."""
    console.print(f"[bold]Building venv for[/bold] {target!r}…")
    try:
        stable = build_being_venv(target, python_version=python_version)
    except UvNotFoundError as exc:
        console.print(f"[red]uv not found[/red]\n{exc}")
        raise typer.Exit(code=1) from exc
    except SidecarVerifyError as exc:
        console.print(f"[red]sidecar verification failed[/red]\n{exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"[red]venv build failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]venv ready:[/green] {stable}")


@venv_app.command("build")
def build(
    target: str = _TARGET_ARG,
    python_version: str = _PYTHON_OPT,
) -> None:
    """Build (or rebuild) a pinned venv for a being or the daemon."""
    _do_build(target, python_version)


@venv_app.command("upgrade")
def upgrade(
    target: str = _TARGET_ARG,
    python_version: str = _PYTHON_OPT,
) -> None:
    """Alias for build: upgrade the pinned venv for a being or the daemon."""
    _do_build(target, python_version)
```

### Modification to `packages/core/src/agent_core/cli.py`

After line `from agent_core.vault_migration_plan import vault_app` (currently the last `from` import in the block), add:

```python
from agent_core.venv.cli import venv_app
```

After line `app.add_typer(vault_app, name="vault")`, add:

```python
app.add_typer(venv_app, name="venv")
```

### Exact content: `packages/core/tests/test_venv_builder.py`

```python
"""Unit tests for agent_core.venv.builder (C2-1, issue #315)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agent_core.venv.builder import (
    SIDECAR_PACKAGES,
    SidecarVerifyError,
    UvNotFoundError,
    atomic_repoint,
    build_being_venv,
    create_venv,
    home_for_target,
    install_sidecars,
    python_in_venv,
    resolve_uv,
    stable_venv_path,
    verify_sidecars,
    versioned_venv_dir,
)


# ---------------------------------------------------------------------------
# resolve_uv
# ---------------------------------------------------------------------------

class TestResolveUv:
    def test_finds_cargo_bin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        suffix = ".exe" if sys.platform == "win32" else ""
        uv = tmp_path / ".cargo" / "bin" / f"uv{suffix}"
        uv.parent.mkdir(parents=True)
        uv.write_text("")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert resolve_uv() == uv.resolve()

    def test_finds_local_bin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        suffix = ".exe" if sys.platform == "win32" else ""
        uv = tmp_path / ".local" / "bin" / f"uv{suffix}"
        uv.parent.mkdir(parents=True)
        uv.write_text("")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert resolve_uv() == uv.resolve()

    def test_falls_back_to_which(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        empty_home = tmp_path / "home"
        empty_home.mkdir()
        uv_on_path = tmp_path / "uv"
        uv_on_path.write_text("")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: empty_home))
        monkeypatch.setattr("agent_core.venv.builder.shutil.which", lambda _: str(uv_on_path))
        assert resolve_uv() == uv_on_path.resolve()

    def test_raises_with_install_hint_when_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty_home = tmp_path / "home"
        empty_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: empty_home))
        monkeypatch.setattr("agent_core.venv.builder.shutil.which", lambda _: None)
        with pytest.raises(UvNotFoundError, match="astral.sh/uv"):
            resolve_uv()


# ---------------------------------------------------------------------------
# Path layout helpers
# ---------------------------------------------------------------------------

class TestPathLayout:
    def test_home_for_daemon(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert home_for_target("daemon") == tmp_path / ".agent-core"

    def test_home_for_being(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert home_for_target("wren") == tmp_path / ".wren"

    def test_versioned_venv_dir_being(self, tmp_path: Path) -> None:
        home = tmp_path / ".wren"
        result = versioned_venv_dir(home, "0.8.0", target="wren")
        assert result == home / ".agent-core" / "venvs" / "0.8.0"

    def test_versioned_venv_dir_daemon_no_nested_agent_core(self, tmp_path: Path) -> None:
        home = tmp_path / ".agent-core"
        result = versioned_venv_dir(home, "0.8.0", target="daemon")
        # Must NOT produce ~/.agent-core/.agent-core/venvs/…
        assert result == home / "venvs" / "0.8.0"
        assert ".agent-core" not in str(result.relative_to(home))

    def test_stable_venv_path(self, tmp_path: Path) -> None:
        assert stable_venv_path(tmp_path / ".wren") == tmp_path / ".wren" / ".venv"

    def test_python_in_venv_posix(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("agent_core.venv.builder.sys.platform", "linux")
        assert python_in_venv(tmp_path) == tmp_path / "bin" / "python"

    def test_python_in_venv_windows(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("agent_core.venv.builder.sys.platform", "win32")
        assert python_in_venv(tmp_path) == tmp_path / "Scripts" / "python.exe"


# ---------------------------------------------------------------------------
# create_venv
# ---------------------------------------------------------------------------

class TestCreateVenv:
    def test_invokes_uv_venv_when_python_absent(self, tmp_path: Path) -> None:
        uv = tmp_path / "uv"
        venv_dir = tmp_path / "venv"
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        create_venv(uv, venv_dir, python_version="3.12", runner=fake_runner)

        assert len(calls) == 1
        assert str(uv) in calls[0]
        assert "venv" in calls[0]
        assert str(venv_dir) in calls[0]
        assert "--python" in calls[0] and "3.12" in calls[0]

    def test_no_op_when_python_exists(self, tmp_path: Path) -> None:
        uv = tmp_path / "uv"
        venv_dir = tmp_path / "venv"
        # Pre-create the python binary
        py = python_in_venv(venv_dir)
        py.parent.mkdir(parents=True)
        py.write_text("")

        calls: list = []
        def fake_runner(cmd, **kw):
            calls.append(cmd)
            class _R:
                returncode = 0
            return _R()

        create_venv(uv, venv_dir, runner=fake_runner)
        assert calls == []

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        uv = tmp_path / "uv"
        venv_dir = tmp_path / "a" / "b" / "c"

        def fake_runner(cmd, **kw):
            class _R:
                returncode = 0
            return _R()

        create_venv(uv, venv_dir, runner=fake_runner)
        assert venv_dir.parent.exists()


# ---------------------------------------------------------------------------
# install_sidecars
# ---------------------------------------------------------------------------

class TestInstallSidecars:
    def test_installs_all_three_packages(self, tmp_path: Path) -> None:
        uv = tmp_path / "uv"
        venv_python = tmp_path / "bin" / "python"
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        install_sidecars(uv, venv_python, runner=fake_runner)

        assert len(calls) == 1
        for pkg in SIDECAR_PACKAGES:
            assert pkg in calls[0]

    def test_uses_absolute_uv_path(self, tmp_path: Path) -> None:
        uv = tmp_path / "uv"
        venv_python = tmp_path / "bin" / "python"
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        install_sidecars(uv, venv_python, runner=fake_runner)
        assert calls[0][0] == str(uv)

    def test_passes_python_flag(self, tmp_path: Path) -> None:
        uv = tmp_path / "uv"
        venv_python = tmp_path / "bin" / "python"
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        install_sidecars(uv, venv_python, runner=fake_runner)
        cmd = calls[0]
        assert "--python" in cmd
        idx = cmd.index("--python")
        assert cmd[idx + 1] == str(venv_python)


# ---------------------------------------------------------------------------
# verify_sidecars
# ---------------------------------------------------------------------------

class TestVerifySidecars:
    def test_passes_on_zero_exit(self, tmp_path: Path) -> None:
        venv_python = tmp_path / "bin" / "python"

        def fake_runner(cmd, **kw):
            class _R:
                returncode = 0
                stderr = ""
            return _R()

        verify_sidecars(venv_python, runner=fake_runner)  # must not raise

    def test_raises_on_non_zero_exit(self, tmp_path: Path) -> None:
        venv_python = tmp_path / "bin" / "python"

        def fake_runner(cmd, **kw):
            class _R:
                returncode = 1
                stderr = "ModuleNotFoundError: No module named 'agent_core_busproxy'"
            return _R()

        with pytest.raises(SidecarVerifyError, match="agent_core_busproxy"):
            verify_sidecars(venv_python, runner=fake_runner)

    def test_import_command_includes_all_three_modules(self, tmp_path: Path) -> None:
        venv_python = tmp_path / "bin" / "python"
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
                stderr = ""
            return _R()

        verify_sidecars(venv_python, runner=fake_runner)

        assert len(calls) == 1
        cmd_joined = " ".join(calls[0])
        assert "agent_core_busproxy" in cmd_joined
        assert "agent_core_channel" in cmd_joined
        assert "agent_core_notify" in cmd_joined


# ---------------------------------------------------------------------------
# atomic_repoint (POSIX only)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink tests")
class TestAtomicRepointPosix:
    def test_creates_symlink(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "venvs" / "0.8.0"
        target_dir.mkdir(parents=True)
        stable = tmp_path / ".venv"

        atomic_repoint(stable, target_dir)

        assert stable.is_symlink()
        assert os.readlink(stable) == str(target_dir)

    def test_replaces_existing_symlink(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "venvs" / "0.7.0"
        new_dir = tmp_path / "venvs" / "0.8.0"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        stable = tmp_path / ".venv"

        atomic_repoint(stable, old_dir)
        atomic_repoint(stable, new_dir)

        assert os.readlink(stable) == str(new_dir)

    def test_old_versioned_dir_not_removed(self, tmp_path: Path) -> None:
        """D3 — never destroy old venv; GC is C2-3's job."""
        old_dir = tmp_path / "venvs" / "0.7.0"
        new_dir = tmp_path / "venvs" / "0.8.0"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        stable = tmp_path / ".venv"

        atomic_repoint(stable, old_dir)
        atomic_repoint(stable, new_dir)

        assert old_dir.exists()

    def test_cleans_leftover_tmp_symlink(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "venvs" / "0.8.0"
        target_dir.mkdir(parents=True)
        stable = tmp_path / ".venv"
        stale_tmp = tmp_path / ".venv.tmp"
        os.symlink(tmp_path / "stale", stale_tmp)  # stale from prior interrupted run

        atomic_repoint(stable, target_dir)

        assert stable.is_symlink()
        assert not stale_tmp.exists()


# ---------------------------------------------------------------------------
# build_being_venv integration (monkeypatched subprocess)
# ---------------------------------------------------------------------------

class TestBuildBeingVenv:
    def _plant_uv(self, tmp_path: Path) -> Path:
        suffix = ".exe" if sys.platform == "win32" else ""
        uv = tmp_path / ".cargo" / "bin" / f"uv{suffix}"
        uv.parent.mkdir(parents=True)
        uv.write_text("")
        return uv

    def test_happy_path_returns_stable_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr("agent_core.venv.builder._metadata_version", lambda _: "0.8.0")
        self._plant_uv(tmp_path)

        def fake_runner(cmd, **kw):
            class _R:
                returncode = 0
                stderr = ""
            return _R()

        monkeypatch.setattr("agent_core.venv.builder.atomic_repoint", lambda s, t: None)

        stable = build_being_venv("wren", runner=fake_runner)
        assert stable == tmp_path / ".wren" / ".venv"

    def test_three_subprocess_calls_in_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create_venv → install_sidecars → verify_sidecars; atomic_repoint is last."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr("agent_core.venv.builder._metadata_version", lambda _: "0.8.0")
        self._plant_uv(tmp_path)

        call_log: list[str] = []

        def fake_runner(cmd, **kw):
            # Identify step by command shape
            cmd_list = list(cmd)
            if "venv" in cmd_list:
                call_log.append("create_venv")
            elif "pip" in cmd_list:
                call_log.append("install_sidecars")
            else:
                call_log.append("verify_sidecars")
            class _R:
                returncode = 0
                stderr = ""
            return _R()

        monkeypatch.setattr("agent_core.venv.builder.atomic_repoint", lambda s, t: None)
        build_being_venv("wren", runner=fake_runner)

        assert call_log == ["create_venv", "install_sidecars", "verify_sidecars"]

    def test_verify_failure_aborts_before_repoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr("agent_core.venv.builder._metadata_version", lambda _: "0.8.0")
        self._plant_uv(tmp_path)

        repoint_calls: list = []
        monkeypatch.setattr(
            "agent_core.venv.builder.atomic_repoint",
            lambda s, t: repoint_calls.append((s, t)),
        )

        def fake_runner(cmd, **kw):
            cmd_list = list(cmd)
            # Fail the verify step
            if "venv" not in cmd_list and "pip" not in cmd_list:
                class _R:
                    returncode = 1
                    stderr = "ModuleNotFoundError"
                return _R()
            class _R:
                returncode = 0
                stderr = ""
            return _R()

        with pytest.raises(SidecarVerifyError):
            build_being_venv("wren", runner=fake_runner)

        assert repoint_calls == [], "atomic_repoint must not be called after verify failure"

    def test_daemon_versioned_dir_has_no_nested_agent_core(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr("agent_core.venv.builder._metadata_version", lambda _: "0.8.0")
        self._plant_uv(tmp_path)

        captured_venv_dir: list[Path] = []

        def fake_runner(cmd, **kw):
            class _R:
                returncode = 0
                stderr = ""
            return _R()

        original_repoint = atomic_repoint

        def capturing_repoint(stable: Path, target_dir: Path) -> None:
            captured_venv_dir.append(target_dir)

        monkeypatch.setattr("agent_core.venv.builder.atomic_repoint", capturing_repoint)
        build_being_venv("daemon", runner=fake_runner)

        assert len(captured_venv_dir) == 1
        venv_dir = captured_venv_dir[0]
        daemon_home = tmp_path / ".agent-core"
        # Must be ~/.agent-core/venvs/0.8.0/, NOT ~/.agent-core/.agent-core/venvs/0.8.0/
        assert venv_dir == daemon_home / "venvs" / "0.8.0"
```

### Exact content: `packages/core/tests/test_venv_cli.py`

```python
"""CLI tests for agent_core.venv (C2-1, issue #315)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_core.venv.cli import venv_app


@pytest.fixture()
def cli_runner() -> CliRunner:
    return CliRunner()


def _patch_build(monkeypatch: pytest.MonkeyPatch, *, raises=None, returns=None):
    """Monkeypatch build_being_venv in the CLI module."""
    def fake_build(target, *, python_version="3.12", runner=None):
        if raises is not None:
            raise raises
        return returns or Path("/fake/.wren/.venv")

    monkeypatch.setattr("agent_core.venv.cli.build_being_venv", fake_build)


class TestVenvBuildCommand:
    def test_happy_path_exits_zero(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_build(monkeypatch, returns=Path("/home/user/.wren/.venv"))
        result = cli_runner.invoke(venv_app, ["build", "wren"])
        assert result.exit_code == 0
        assert "venv ready" in result.output

    def test_uv_not_found_exits_one(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_core.venv.builder import UvNotFoundError

        _patch_build(monkeypatch, raises=UvNotFoundError("uv not found"))
        result = cli_runner.invoke(venv_app, ["build", "wren"])
        assert result.exit_code == 1
        assert "uv not found" in result.output

    def test_sidecar_verify_failure_exits_one(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_core.venv.builder import SidecarVerifyError

        _patch_build(monkeypatch, raises=SidecarVerifyError("import failed"))
        result = cli_runner.invoke(venv_app, ["build", "wren"])
        assert result.exit_code == 1
        assert "sidecar verification failed" in result.output

    def test_unexpected_error_exits_one(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_build(monkeypatch, raises=RuntimeError("something exploded"))
        result = cli_runner.invoke(venv_app, ["build", "wren"])
        assert result.exit_code == 1
        assert "venv build failed" in result.output


class TestVenvUpgradeCommand:
    def test_upgrade_is_alias_for_build(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called_with: list[str] = []

        def fake_build(target, *, python_version="3.12", runner=None):
            called_with.append(target)
            return Path("/home/user/.wren/.venv")

        monkeypatch.setattr("agent_core.venv.cli.build_being_venv", fake_build)
        result = cli_runner.invoke(venv_app, ["upgrade", "pepper"])
        assert result.exit_code == 0
        assert called_with == ["pepper"]

    def test_upgrade_uv_not_found_exits_one(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_core.venv.builder import UvNotFoundError

        _patch_build(monkeypatch, raises=UvNotFoundError("uv not found"))
        result = cli_runner.invoke(venv_app, ["upgrade", "daemon"])
        assert result.exit_code == 1
```

## Alternatives considered

1. **Keep a single shared venv under `~/.agent-core/` (current state)**: Ruled out — this is exactly items `[P1]` D1 + D2: one mutable venv means a version bump strands all beings simultaneously, and Windows `.pyd` locks prevent in-place upgrades. The per-being layout was designed specifically to close these.

2. **In-place `uv pip install --upgrade` on the existing venv**: Simpler to implement. Ruled out by D3 — in-place upgrade on a live Windows venv hits `.pyd` lock (the 2026-07-13 incident); build-alongside-then-repoint gives zero-downtime upgrades and safe rollback if verification fails.

3. **Extend `daemon/release.py:ensure_venv()` to add uv resolution**: `ensure_venv` already exists and calls bare `["uv", ...]`. It would be natural to add `resolve_uv()` there. Ruled out: `daemon install` installs from GitHub Release artifacts (wheels + requirements.txt), not PyPI. C2-1's builder installs the slim sidecar set from PyPI. Different install paths; different modules.

## Open questions

1. **Daemon versioned dir path**: The spec language `~/.<target>/.agent-core/venvs/<version>/` yields `~/.agent-core/.agent-core/venvs/<version>/` when `<target>` is treated as the CLI argument `"daemon"`. This spec proposes `~/.agent-core/venvs/<version>/` instead (daemon home = `~/.agent-core/` already contains `.agent-core`; nesting it again is confusing). If Jeff's intent is the nested path, the Worker must update `versioned_venv_dir()` to remove the `target == "daemon"` special-case and the corresponding test assertion in `test_daemon_versioned_dir_has_no_nested_agent_core`.

## Out of scope

- C2-2 (`.mcp.json` canonical generator and migration of Wren/Pepper) — depends on C2-1.
- C2-3 (`daemon doctor` / GC of superseded versioned dirs) — depends on C2-1.
- Updating `daemon/release.py:ensure_venv()` to use `resolve_uv()` — the existing `daemon install` path is GitHub-Release-based; this ticket owns the new PyPI-based sidecar install only.
- The `daemon install` deprecation or replacement — Cluster 3 topic.
- Multi-tenant port isolation, hatchery `.mcp.json` wiring — Theme C.
- Voice/GPU packages — explicitly excluded from the slim sidecar set (D4).
