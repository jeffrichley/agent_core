"""Pluggy hook specifications for agent_core runtime plugins."""

from __future__ import annotations

from typing import Any

import pluggy

hookspec = pluggy.HookspecMarker("agent_core")


class AgentCoreSpecs:
    """Hook contract for resolving runtime classes."""

    @hookspec(firstresult=True)
    def resolve_class(self, class_path: str) -> type[Any] | None:
        """Return a class for `class_path`, or None to defer."""
