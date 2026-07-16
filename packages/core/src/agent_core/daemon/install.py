"""Install stamp read/write + workspace-root discovery utility.

`cli.py` writes a stamp after each successful `daemon install --release`.
The stamp tells subsequent `daemon status` invocations what version is
currently deployed.

Source-based install was removed in Phase 2.5: the daemon is now updated
exclusively from GitHub Release artifacts (see daemon/release.py).
`find_workspace_root` remains as a utility used by guard tests and other
workspace-aware tooling.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

STAMP_FILENAME = ".daemon-install-stamp.json"


class WorkspaceNotFoundError(Exception):
    """Raised when find_workspace_root cannot locate a workspace pyproject.toml."""


def find_workspace_root(start: Path) -> Path:
    """Ascend from `start` until a `pyproject.toml` with `[tool.uv.workspace]` is found.

    Utility used by guard tests and other workspace-aware tooling. Source-based
    daemon install (which previously used this) was removed in Phase 2.5.
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
                "(no pyproject.toml with [tool.uv.workspace] found)."
            )
        candidate = parent


@dataclass(frozen=True)
class InstallStamp:
    """Captures what was installed into the daemon venv, when, and from where."""

    installed_at: str            # ISO 8601 UTC
    installed_sha: str           # git rev-parse / tag sha at install time
    installed_version: str       # human-readable version (e.g. "0.1.0")
    python_version: str          # e.g. "3.12.5"
    extra: str | None            # uv extra name, or None
    release_tag: str | None      # provenance: GH release tag (e.g. "v0.1.0")
    venv_path: str | None = None  # absolute path of the installed venv (Cα-3, #321)


def write_stamp(home: Path, stamp: InstallStamp) -> None:
    """Write the install stamp to <home>/.daemon-install-stamp.json."""
    home.mkdir(parents=True, exist_ok=True)
    (home / STAMP_FILENAME).write_text(
        json.dumps(asdict(stamp), indent=2) + "\n",
        encoding="utf-8",
    )


def read_stamp(home: Path) -> InstallStamp | None:
    """Read the install stamp. Forward-compatible: unknown fields dropped,
    missing new fields default to None / 'unknown'."""
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
            venv_path=data.get("venv_path"),
        )
    except (KeyError, TypeError):
        return None
