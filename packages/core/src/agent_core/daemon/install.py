"""Daemon venv install logic — pure functions, no I/O beyond filesystem.

`cli.py` wires these into the `agent-core daemon install` and
`agent-core daemon refresh` typer commands. Keeping the install logic
out of the CLI module makes it directly unit-testable.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


class WorkspaceNotFoundError(RuntimeError):
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
