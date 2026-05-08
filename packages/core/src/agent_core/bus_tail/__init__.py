"""Read-only audit/tail surface for the bus.

See docs/superpowers/specs/2026-05-07-issue-16-bus-tail-audit-design.md.
"""

from agent_core.bus_tail.endpoint import DEFAULT_MOUNT, BusTailMCPEndpoint
from agent_core.bus_tail.mcp import register_bus_tail_tools
from agent_core.bus_tail.reader import PersistenceReader
from agent_core.bus_tail.summaries import SUMMARIZERS, summarize_payload

__all__ = [
    "DEFAULT_MOUNT",
    "BusTailMCPEndpoint",
    "PersistenceReader",
    "SUMMARIZERS",
    "register_bus_tail_tools",
    "summarize_payload",
]
