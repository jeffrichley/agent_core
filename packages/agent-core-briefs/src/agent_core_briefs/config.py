"""Config-load-time substitution + path expansion.

``${var}`` references resolve from a vars map; missing vars raise loudly
rather than silently passing through. Distinct from ``{{when.date}}``-style
delivery-time templating in destination paths (handled separately).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


class ConfigSubstitutionError(ValueError):
    """Raised when a ${var} reference cannot be resolved."""


def substitute_vars(value: Any, vars_map: dict[str, str]) -> Any:
    """Recursively substitute ``${var}`` references in ``value`` against
    ``vars_map``. Walks dicts, lists, tuples; passes non-strings through.

    Raises ``ConfigSubstitutionError`` for any reference whose name isn't
    in ``vars_map`` — fail-loud is the contract, silent passthrough breeds
    bugs that look like "the path is wrong" three weeks later.
    """
    if isinstance(value, str):
        return _substitute_string(value, vars_map)
    if isinstance(value, dict):
        return {k: substitute_vars(v, vars_map) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_vars(item, vars_map) for item in value]
    if isinstance(value, tuple):
        return tuple(substitute_vars(item, vars_map) for item in value)
    return value


def _substitute_string(s: str, vars_map: dict[str, str]) -> str:
    def _replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in vars_map:
            raise ConfigSubstitutionError(
                f"undefined config var ${{{name}}} (missing_var={name!r})"
            )
        return vars_map[name]

    return _VAR_PATTERN.sub(_replace, s)


def expand_path(path: str | Path) -> Path:
    """Convert a string path to a ``Path``, with ``~/`` user-home expansion.

    Idempotent; passes ``Path`` instances through ``.expanduser()`` too so
    callers don't need to track which form they're holding.
    """
    return Path(path).expanduser()
