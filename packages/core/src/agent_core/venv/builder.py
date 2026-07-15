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
