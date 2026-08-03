"""Agent_core entry-point hook surface for the webcam framework.

Three hookimpls:

* ``register_endpoint_types`` — exposes ``builtin.webcam`` so the bus
  runner can construct a ``WebcamEndpoint`` from a yaml entry.
* ``reserved_endpoint_params`` — declares ``webcam`` so the runner pops
  it from claude_code_mcp's params before constructing.
* ``wire_endpoints_after_registration`` — pairs every
  ``ClaudeCodeMCPEndpoint`` whose yaml params name a webcam endpoint
  with that endpoint instance, appending a deferred mounter that
  registers the two webcam tools on the FastMCP server at
  ``bus.start()`` time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pluggy

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle
    from agent_core.bus.protocol import Endpoint
    from agent_core.plugins.specs import RunnerServices

hookimpl = pluggy.HookimplMarker("agent_core")


@hookimpl
def register_endpoint_types() -> dict[str, type[Any]]:
    """Register ``builtin.webcam`` as a bus endpoint type."""
    from agent_core_webcam.endpoint import WebcamEndpoint

    return {"builtin.webcam": WebcamEndpoint}


@hookimpl
def register_hook_tool_types() -> dict[str, type[Any]]:
    """Register ``builtin.presence_injector`` as an agent_core hook-tool type."""
    from agent_core_webcam.presence.injector import PresenceInjector

    return {"builtin.presence_injector": PresenceInjector}


@hookimpl
def reserved_endpoint_params() -> list[str]:
    """The runner pops these keys from each endpoint's params before constructing."""
    return ["webcam"]


@hookimpl
def wire_endpoints_after_registration(
    endpoints: dict[str, Endpoint],
    raw_endpoint_configs: dict[str, dict[str, Any]],
    services: RunnerServices,
) -> None:
    """Mount webcam tools on every MCP endpoint that names a webcam endpoint."""
    del services  # unused — kept for hookspec parity
    from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
    from agent_core_webcam.endpoint import WebcamEndpoint
    from agent_core_webcam.mcp import register_webcam_tools

    for name, endpoint in endpoints.items():
        if not isinstance(endpoint, ClaudeCodeMCPEndpoint):
            continue
        raw = raw_endpoint_configs.get(name) or {}
        params = raw.get("params") or {}
        webcam_name = params.get("webcam")
        if not webcam_name:
            continue
        webcam = endpoints.get(webcam_name)
        if webcam is None:
            available = sorted(n for n, e in endpoints.items() if isinstance(e, WebcamEndpoint))
            raise ValueError(
                f"endpoint {name!r} names webcam={webcam_name!r} but no endpoint with "
                f"that name is registered. Available WebcamEndpoint names: {available}"
            )
        if not isinstance(webcam, WebcamEndpoint):
            available = sorted(n for n, e in endpoints.items() if isinstance(e, WebcamEndpoint))
            raise ValueError(
                f"endpoint {name!r} names webcam={webcam_name!r}, but that endpoint is "
                f"a {type(webcam).__name__}, not a WebcamEndpoint. "
                f"Available WebcamEndpoint names: {available}"
            )

        def _mounter(
            bus_handle: BusHandle,
            *,
            webcam: WebcamEndpoint = webcam,
            mcp_endpoint: ClaudeCodeMCPEndpoint = endpoint,
        ) -> None:
            del bus_handle  # webcam tools don't publish onto the bus
            register_webcam_tools(mcp=mcp_endpoint._mcp, endpoint=webcam)

        endpoint.deferred_tool_mounters.append(_mounter)
