"""Every top-level package this code imports must be a declared dependency.

Regression guard for the defect found 2026-08-05: ``__main__.py`` does an
unconditional ``from agent_core.plugins.manager import ...`` while
``pyproject.toml`` declared only mcp/anyio/httpx/typer/pyyaml. A clean
``pip install agent-core-channel`` therefore produced a package that died at
startup with ``ModuleNotFoundError: No module named 'agent_core'``, which left
a freshly hatched being with no MCP session and silently dead-lettered every
message addressed to her.

The import is late-bound (inside ``main()``), so nothing at collection time
catches it -- the module imports fine; only *running* it fails.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC = PACKAGE_ROOT / "src"

# Distribution name -> top-level module it provides, for deps whose names differ.
_DIST_TO_MODULE = {
    "agent-core-bus": "agent_core",
    "pyyaml": "yaml",
}

# Provided by the standard library or by this package itself.
_EXEMPT = {"agent_core_channel"}


def _declared_modules() -> set[str]:
    data = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    modules: set[str] = set()
    for spec in data["project"]["dependencies"]:
        dist = re.split(r"[><=!~\[;\s]", spec, maxsplit=1)[0].strip().lower()
        modules.add(_DIST_TO_MODULE.get(dist, dist.replace("-", "_")))
    return modules


def _imported_modules() -> dict[str, set[str]]:
    """Top-level module -> set of files importing it, across all of src/."""
    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # level > 0 is a relative import; it has no top-level name.
                names = [node.module.split(".")[0]] if node.module and not node.level else []
            else:
                continue
            for name in names:
                found.setdefault(name, set()).add(path.name)
    return found


def test_every_imported_third_party_module_is_declared() -> None:
    declared = _declared_modules()
    stdlib = sys.stdlib_module_names
    undeclared = {
        mod: sorted(files)
        for mod, files in _imported_modules().items()
        if mod not in declared and mod not in stdlib and mod not in _EXEMPT
    }
    assert not undeclared, (
        "these modules are imported but not declared in pyproject.toml "
        f"dependencies: {undeclared}. A package that imports a module it does "
        "not declare installs cleanly and fails at runtime."
    )


def test_agent_core_bus_is_declared() -> None:
    """Explicit guard on the specific dependency that was missing.

    Kept separate from the general check so that removing it fails with a
    message naming the incident rather than a generic diff.
    """
    assert "agent_core" in _declared_modules(), (
        "agent-core-bus must stay in dependencies: __main__.py imports "
        "agent_core.plugins.manager unconditionally at startup. Removing it "
        "reproduces the 2026-08-05 outage where a hatched being's channel "
        "relay died and her messages dead-lettered."
    )
