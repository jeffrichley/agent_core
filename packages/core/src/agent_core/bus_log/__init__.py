"""Bus log pipeline — read, filter, project bus envelope JSONL streams.

Public API used by reflection jobs, the agent-core CLI, and the in-session
``show_my_day`` MCP tool. See docs/superpowers/specs/2026-05-03-bus-log-pipeline-design.md.
"""

from __future__ import annotations

from agent_core.bus_log.projectors import (
    AcknowledgmentSkipProjector,
    HandoffFailedProjector,
    HandoffReadyProjector,
    Projector,
    SchedulerHeartbeatSkipProjector,
    TextMessageProjector,
    fallback_projector,
    get_projector,
    register_projector,
)
from agent_core.bus_log.reader import iter_envelopes, iter_for_agent

__all__ = [
    "AcknowledgmentSkipProjector",
    "HandoffFailedProjector",
    "HandoffReadyProjector",
    "Projector",
    "SchedulerHeartbeatSkipProjector",
    "TextMessageProjector",
    "bootstrap_default_projectors",
    "fallback_projector",
    "get_projector",
    "iter_envelopes",
    "iter_for_agent",
    "register_projector",
]


def bootstrap_default_projectors() -> None:
    """Discover projectors via pluggy and load them into the registry.

    Called once at runtime startup (CLI entry, daemon boot, MCP endpoint
    init). Safe to call multiple times — re-registering replaces.
    """
    from agent_core.plugins.manager import (
        create_plugin_manager,
        get_bus_log_projectors,
    )

    pm = create_plugin_manager()
    for key, projector in get_bus_log_projectors(pm).items():
        register_projector(key, projector)
