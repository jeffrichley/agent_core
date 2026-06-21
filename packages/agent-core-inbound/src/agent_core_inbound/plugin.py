"""Pluggy hookimpl surface for agent-core-inbound.

Registers ``inbound.github`` as a bus endpoint type so the runner can
construct InboundEndpoint from a yaml entry.
"""
from __future__ import annotations

from typing import Any

import pluggy

hookimpl = pluggy.HookimplMarker("agent_core")


@hookimpl
def register_endpoint_types() -> dict[str, type[Any]]:
    """Register ``inbound.github`` as a bus endpoint type."""
    from agent_core_inbound.endpoint import InboundEndpoint

    return {"inbound.github": InboundEndpoint}
