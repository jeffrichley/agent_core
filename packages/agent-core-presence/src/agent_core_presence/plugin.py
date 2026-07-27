"""Agent_core entry-point surface for the presence framework.

One hookimpl: ``register_hook_tool_types`` exposes ``builtin.presence_injector``
so a yaml pipeline entry can construct a :class:`PresenceInjector`. Wired via
the ``[project.entry-points."agent_core"]`` table in ``pyproject.toml``.
"""

from __future__ import annotations

from typing import Any

import pluggy

hookimpl = pluggy.HookimplMarker("agent_core")


@hookimpl
def register_hook_tool_types() -> dict[str, type[Any]]:
    """Register ``builtin.presence_injector`` as an agent_core hook-tool type."""
    from agent_core_presence.injector import PresenceInjector

    return {"builtin.presence_injector": PresenceInjector}
