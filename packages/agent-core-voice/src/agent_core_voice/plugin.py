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


def _resolve_claude_code_mcp_cls() -> type[Any]:
    """Lazy import; overridable by tests via monkeypatch."""
    from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint

    return ClaudeCodeMCPEndpoint


@hookimpl
def wire_endpoints_after_registration(
    endpoints: dict[str, Endpoint],
    raw_endpoint_configs: dict[str, dict[str, Any]],
    services: RunnerServices,
) -> None:
    """Mount voice tools on every MCP endpoint that names a voice + voice_id."""
    del services  # unused — kept for hookspec parity

    from agent_core_voice.endpoint import VoiceEndpoint
    from agent_core_voice.mcp import register_voice_tools

    claude_code_mcp_cls = _resolve_claude_code_mcp_cls()

    for name, endpoint in endpoints.items():
        if not isinstance(endpoint, claude_code_mcp_cls):
            continue
        raw = raw_endpoint_configs.get(name) or {}
        params = raw.get("params") or {}
        voice_name = params.get("voice")
        voice_id = params.get("voice_id")

        if not voice_name and not voice_id:
            continue
        if not voice_name or not voice_id:
            raise ValueError(
                f"endpoint {name!r} sets one of 'voice'/'voice_id' but not both; "
                f"got voice={voice_name!r}, voice_id={voice_id!r}"
            )

        voice_ep = endpoints.get(voice_name)
        if voice_ep is None:
            available = sorted(n for n, e in endpoints.items() if isinstance(e, VoiceEndpoint))
            raise ValueError(
                f"endpoint {name!r} names voice={voice_name!r} but no endpoint with that "
                f"name is registered. Available VoiceEndpoint names: {available}"
            )
        if not isinstance(voice_ep, VoiceEndpoint):
            available = sorted(n for n, e in endpoints.items() if isinstance(e, VoiceEndpoint))
            raise ValueError(
                f"endpoint {name!r} names voice={voice_name!r}, but that endpoint is a "
                f"{type(voice_ep).__name__}, not a VoiceEndpoint. "
                f"Available VoiceEndpoint names: {available}"
            )
        if voice_id not in voice_ep.voice_ids():
            available = sorted(voice_ep.voice_ids())
            raise ValueError(
                f"endpoint {name!r} requests voice_id={voice_id!r} but voice endpoint "
                f"{voice_name!r} has no such voice. Available voice ids: {available}"
            )

        def _mounter(
            bus_handle,
            *,
            voice_ep: VoiceEndpoint = voice_ep,
            mcp_endpoint=endpoint,
            voice_id: str = voice_id,
            agent_name: str = name,
        ) -> None:
            del bus_handle  # voice tools don't publish onto the bus
            register_voice_tools(
                mcp=mcp_endpoint._mcp,
                endpoint=voice_ep,
                voice_id=voice_id,
                agent_name=agent_name,
            )

        endpoint.deferred_tool_mounters.append(_mounter)
