"""Bus log pipeline — read, filter, project bus envelope JSONL streams.

Public API used by reflection jobs, the agent-core CLI, and the in-session
``show_my_day`` MCP tool. See docs/superpowers/specs/2026-05-03-bus-log-pipeline-design.md.
"""

from __future__ import annotations

from agent_core.bus_log.projectors import (
    Projector,
    TextMessageProjector,
    fallback_projector,
    get_projector,
    register_projector,
)
from agent_core.bus_log.reader import iter_envelopes

__all__ = [
    "Projector",
    "TextMessageProjector",
    "fallback_projector",
    "get_projector",
    "register_projector",
    "iter_envelopes",
]
