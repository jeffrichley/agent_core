"""Daemon venv install logic — pure functions, no I/O beyond filesystem.

`cli.py` wires these into the `agent-core daemon install` and
`agent-core daemon refresh` typer commands. Keeping the install logic
out of the CLI module makes it directly unit-testable.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import asdict, dataclass
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

    installed_at: str  # ISO 8601 UTC
    installed_sha: str  # git rev-parse HEAD at install time
    python_version: str  # e.g., "3.12.5"
    extra: str | None  # uv extra name, or None
    uv_lock_hash: str  # sha256 of uv.lock at install time


def write_stamp(home: Path, stamp: InstallStamp) -> None:
    """Write the install stamp to <home>/.daemon-install-stamp.json."""
    home.mkdir(parents=True, exist_ok=True)
    (home / STAMP_FILENAME).write_text(
        json.dumps(asdict(stamp), indent=2) + "\n",
        encoding="utf-8",
    )


def read_stamp(home: Path) -> InstallStamp | None:
    """Read the install stamp. None on missing, corrupt, or schema-incomplete."""
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
            python_version=data["python_version"],
            extra=data.get("extra"),
            uv_lock_hash=data["uv_lock_hash"],
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
