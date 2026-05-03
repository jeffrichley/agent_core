"""Bus log pipeline — read, filter, project bus envelope JSONL streams.

Public API used by reflection jobs, the agent-core CLI, and the in-session
``show_my_day`` MCP tool. See docs/superpowers/specs/2026-05-03-bus-log-pipeline-design.md.
"""

from __future__ import annotations

from agent_core.bus_log.projectors import (
    Projector,
    fallback_projector,
    get_projector,
    register_projector,
    reset_registry,
)

__all__ = [
    "Projector",
    "fallback_projector",
    "get_projector",
    "register_projector",
    "reset_registry",
]
