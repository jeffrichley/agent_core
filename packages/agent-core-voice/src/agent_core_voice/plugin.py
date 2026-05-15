"""Agent_core entry-point hook surface for the voice service.

Three hookimpls:

* ``register_endpoint_types`` — exposes ``builtin.voice`` so the bus
  runner can construct a ``VoiceEndpoint`` from a yaml entry.
* ``reserved_endpoint_params`` — declares ``voice`` and ``voice_id`` so
  the runner pops them from claude_code_mcp's params before constructing.
* ``wire_endpoints_after_registration`` — added in Task 10. For each
  ``ClaudeCodeMCPEndpoint`` whose yaml params name a voice, validate
  and append a deferred mounter that registers the voice tools on the
  MCP server with ``voice_id`` closed in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pluggy

if TYPE_CHECKING:
    from agent_core.bus.protocol import Endpoint
    from agent_core.plugins.specs import RunnerServices

hookimpl = pluggy.HookimplMarker("agent_core")


@hookimpl
def register_endpoint_types() -> dict[str, type[Any]]:
    """Register ``builtin.voice`` as a bus endpoint type."""
    from agent_core_voice.endpoint import VoiceEndpoint

    return {"builtin.voice": VoiceEndpoint}


@hookimpl
def reserved_endpoint_params() -> list[str]:
    """The runner pops these keys from each endpoint's params before constructing."""
    return ["voice", "voice_id"]
