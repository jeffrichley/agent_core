"""Daemon venv install logic — pure functions, no I/O beyond filesystem.

`cli.py` wires these into the `agent-core daemon install` and
`agent-core daemon refresh` typer commands. Keeping the install logic
out of the CLI module makes it directly unit-testable.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tomllib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


class WorkspaceNotFoundError(Exception):
    """Raised when find_workspace_root cannot locate a workspace pyproject.toml."""


def find_workspace_root(start: Path) -> Path:
    """Ascend from `start` until a `pyproject.toml` with `[tool.uv.workspace]` is found.

    The agent-core repo's root `pyproject.toml` declares the workspace; we
    use that as the install target.
    """
    candidate = start.resolve()
    while True:
        pyproject = candidate / "pyproject.toml"
        if pyproject.exists():
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError:
                data = {}
            if "workspace" in data.get("tool", {}).get("uv", {}):
                return candidate
        parent = candidate.parent
        if parent == candidate:
            raise WorkspaceNotFoundError(
                f"couldn't find workspace root above {start} "
                "(no pyproject.toml with [tool.uv.workspace] found). "
                "Run `agent-core daemon install` from within the agent-core repo."
            )
        candidate = parent


STAMP_FILENAME = ".daemon-install-stamp.json"


@dataclass(frozen=True)
class InstallStamp:
    """Captures what was installed into the daemon venv, when, and from where."""

    installed_at: str            # ISO 8601 UTC
    installed_sha: str           # git rev-parse / tag sha at install time
    installed_version: str       # human-readable version (e.g. "0.1.0")
    python_version: str          # e.g. "3.12.5"
    extra: str | None            # uv extra name, or None
    release_tag: str | None      # provenance: GH release tag (e.g. "v0.1.0")


def write_stamp(home: Path, stamp: InstallStamp) -> None:
    """Write the install stamp to <home>/.daemon-install-stamp.json."""
    home.mkdir(parents=True, exist_ok=True)
    (home / STAMP_FILENAME).write_text(
        json.dumps(asdict(stamp), indent=2) + "\n",
        encoding="utf-8",
    )


def read_stamp(home: Path) -> InstallStamp | None:
    """Read the install stamp. None on missing/corrupt.

    Forward-compatible: unknown fields (e.g. Phase-2 `uv_lock_hash`) are
    silently dropped. Missing new fields default to None / "unknown" so
    older stamps from Phase 2 continue to read.
    """
    path = home / STAMP_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    try:
        return InstallStamp(
            installed_at=data["installed_at"],
            installed_sha=data["installed_sha"],
            installed_version=data.get("installed_version", "unknown"),
            python_version=data["python_version"],
            extra=data.get("extra"),
            release_tag=data.get("release_tag"),
        )
    except (KeyError, TypeError):
        return None


def build_uv_sync_command(
    *, venv: Path, extra: str | None
) -> tuple[list[str], dict[str, str]]:
    """Build the `uv sync` command list and env overrides for daemon install.

    --frozen        → install against the workspace's uv.lock verbatim.
    --no-editable   → workspace members go in as wheels; no .pth shims.
                      This is what makes the daemon venv immune to
                      `uv sync` in the workspace tree.
    --no-dev        → skip the workspace's dev dependency group.
    --extra <x>     → optional uv extra (e.g., cu130, cpu).

    UV_PROJECT_ENVIRONMENT redirects uv's install target to the daemon venv
    instead of the workspace's `.venv/`.
    """
    cmd: list[str] = ["uv", "sync", "--frozen", "--no-editable", "--no-dev"]
    if extra is not None:
        cmd += ["--extra", extra]
    env_overrides = {"UV_PROJECT_ENVIRONMENT": str(venv)}
    return cmd, env_overrides


class UvNotFoundError(Exception):
    """Raised when `uv` is not on PATH."""


def _git_head_sha(workspace: Path) -> str:
    """Return the short HEAD sha of the workspace repo, or 'unknown'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def compute_lock_hash(workspace: Path) -> str:
    """SHA-256 of the workspace's uv.lock, prefixed with `sha256:`. Public:
    `cli.py`'s `daemon status` reuses this for the lock-drift check.
    """
    lock = workspace / "uv.lock"
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def run_install(
    *,
    home: Path,
    workspace: Path,
    extra: str | None,
    python_version: str,
) -> InstallStamp:
    """Populate `<home>/.venv/` and write the install stamp. Idempotent."""
    venv = home / ".venv"

    # Step 1: create / refresh the venv with the pinned Python.
    # --clear is required: uv >= 0.10 refuses to reuse an existing venv without it
    venv_cmd = ["uv", "venv", str(venv), "--python", python_version, "--clear"]
    try:
        result = subprocess.run(venv_cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise UvNotFoundError(
            "uv not found on PATH — install uv first: "
            "https://docs.astral.sh/uv/getting-started/installation/"
        ) from exc
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, venv_cmd, output=result.stdout, stderr=result.stderr
        )

    # Step 2: uv sync into that venv.
    sync_cmd, env_overrides = build_uv_sync_command(venv=venv, extra=extra)
    env = {**os.environ, **env_overrides}
    sync_result = subprocess.run(
        sync_cmd, cwd=workspace, env=env, capture_output=True, text=True, check=False
    )
    if sync_result.returncode != 0:
        raise subprocess.CalledProcessError(
            sync_result.returncode,
            sync_cmd,
            output=sync_result.stdout,
            stderr=sync_result.stderr,
        )

    # Step 3: stamp it.
    # (This source-install path is removed in Task 8; for now we keep it
    # writing the new schema so tests stay green.)
    stamp = InstallStamp(
        installed_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        installed_sha=_git_head_sha(workspace),
        installed_version="unknown",  # source installs don't know their version
        python_version=python_version,
        extra=extra,
        release_tag=None,  # source installs are not from a tagged release
    )
    write_stamp(home, stamp)
    return stamp
