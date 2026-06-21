"""Dotted-path resolver for the GitHubConnector matching engine.

Walks a nested dict by splitting a dotted path into keys. Returns the
sentinel ``MISSING`` for any path that doesn't resolve cleanly —
including paths through non-dict cursor values, missing keys, or the
empty path. Falsy values (False, 0, "", None, [], {}) are NOT treated
as missing; they're real values the resolver returns directly.
"""
from __future__ import annotations

from typing import Any, Final


class _MissingSentinel:
    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Final = _MissingSentinel()


def resolve_path(payload: dict[str, Any], path: str) -> Any:
    """Resolve a dotted path against a nested dict.

    Returns ``MISSING`` for any path that doesn't fully resolve.
    """
    if not path:
        return MISSING
    cursor: Any = payload
    for key in path.split("."):
        if not isinstance(cursor, dict) or key not in cursor:
            return MISSING
        cursor = cursor[key]
    return cursor


__all__ = ["MISSING", "resolve_path"]
