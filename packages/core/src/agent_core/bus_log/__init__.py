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
    "fallback_projector",
    "get_projector",
    "iter_envelopes",
    "iter_for_agent",
    "register_projector",
]
