"""agent_core entry-point hook surface for the brief framework.

T16 wiring (cutover #09): contributes the briefs subsystem to ``agent_core``
via pluggy.

Three hookimpls:

* ``register_endpoint_types`` — exposes ``builtin.briefs_orchestrator`` so
  the bus runner can construct a :class:`BriefsOrchestratorEndpoint` from
  a yaml entry. The orchestrator's constructor accepts ``fetcher_paths``
  (list of dirs) and ``vars`` (yaml-idiomatic alias for ``vars_map``) so
  ``params:`` in ``agent-core.yaml`` reads naturally — see T16 for the
  backward-compat extensions to ``__init__``.
* ``register_cli_subapps`` — mounts the ``agent-core briefs`` Typer subapp
  on the top-level CLI. Imported lazily inside the hookimpl so
  ``agent_core`` itself does not import ``agent_core_briefs``; the
  layering flows entry-point → plugin → core, never the other direction.
* ``wire_endpoints_after_registration`` — T19 cross-endpoint MCP wiring.
  Pairs every ``ClaudeCodeMCPEndpoint`` whose yaml params name a
  ``briefs_orchestrator`` with the orchestrator instance, so the seven
  briefs agent tools end up mounted on Pepper's MCP session at
  ``bus.start()`` time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pluggy

if TYPE_CHECKING:
    import typer

    from agent_core.bus.handle import BusHandle
    from agent_core.bus.protocol import Endpoint
    from agent_core.plugins.specs import RunnerServices

hookimpl = pluggy.HookimplMarker("agent_core")


@hookimpl
def register_endpoint_types() -> dict[str, type[Any]]:
    """Register ``builtin.briefs_orchestrator`` as a bus endpoint type.

    Imported inside the impl so a ``pluggy`` discovery pass that doesn't
    wire endpoints (e.g. CLI-only contexts) doesn't pay the cost of
    pulling in the orchestrator + its transitive deps.
    """
    from agent_core_briefs.orchestrator import BriefsOrchestratorEndpoint

    return {"builtin.briefs_orchestrator": BriefsOrchestratorEndpoint}


@hookimpl
def register_cli_subapps(app: typer.Typer) -> None:
    """Mount ``agent-core briefs ...`` onto the top-level CLI.

    Lazy import: ``agent_core_briefs.cli`` brings in typer + the
    orchestrator. Doing the import inside the hookimpl keeps it off the
    ``agent_core`` startup path until the CLI is actually constructed.
    """
    from agent_core_briefs.cli import briefs_app

    app.add_typer(briefs_app, name="briefs")


@hookimpl
def reserved_endpoint_params() -> list[str]:
    """Declare the params we consume in :func:`wire_endpoints_after_registration`.

    The runner pops these keys from each endpoint's ``params:`` before
    calling ``cls(name=..., **params)``, so endpoint classes
    (e.g., ``ClaudeCodeMCPEndpoint``) don't have to accept the
    plugin-managed ``briefs_orchestrator`` key in their ``__init__``.
    The original raw config (still containing the key) is what reaches
    :func:`wire_endpoints_after_registration` via ``raw_endpoint_configs``.
    """
    return ["briefs_orchestrator"]


@hookimpl
def wire_endpoints_after_registration(
    endpoints: dict[str, Endpoint],
    raw_endpoint_configs: dict[str, dict[str, Any]],
    services: RunnerServices,
) -> None:
    """Pair ``ClaudeCodeMCPEndpoint`` instances with a named briefs orchestrator.

    For each MCP endpoint whose yaml entry sets
    ``params.briefs_orchestrator: <name>``, this hookimpl looks up the
    orchestrator endpoint by name and appends a deferred mounter to the
    MCP endpoint's ``deferred_tool_mounters`` list. The mounter is
    invoked once during the MCP endpoint's :meth:`start` with the
    bus_handle, at which point it calls
    :func:`agent_core_briefs.mcp.register_briefs_tools` to register the
    seven briefs agent tools on the FastMCP server.

    The orchestrator must already be registered (yaml ordering is the
    operator's responsibility, but pluggy's ``configure_endpoint_instance``
    pass and the construction loop run all endpoints first, so by the
    time this hookimpl fires every endpoint named in the yaml is
    instantiated).

    Imports are lazy inside the hookimpl so plugins that don't trigger
    this seam never pay the cost of importing fastmcp + the briefs MCP
    tool surface.

    Raises:
        ValueError: a yaml entry references an orchestrator name that
            isn't registered, or names an endpoint that exists but
            isn't a ``BriefsOrchestratorEndpoint`` instance.
    """
    del services  # unused — kept for hookspec parity
    from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
    from agent_core_briefs.mcp import register_briefs_tools
    from agent_core_briefs.orchestrator import BriefsOrchestratorEndpoint

    for name, endpoint in endpoints.items():
        if not isinstance(endpoint, ClaudeCodeMCPEndpoint):
            continue
        raw = raw_endpoint_configs.get(name) or {}
        params = raw.get("params") or {}
        orch_name = params.get("briefs_orchestrator")
        if not orch_name:
            continue
        orch = endpoints.get(orch_name)
        if orch is None:
            available = sorted(
                n for n, e in endpoints.items() if isinstance(e, BriefsOrchestratorEndpoint)
            )
            raise ValueError(
                f"endpoint {name!r} names briefs_orchestrator={orch_name!r} but "
                f"no endpoint with that name is registered. Available "
                f"BriefsOrchestratorEndpoint names: {available}"
            )
        if not isinstance(orch, BriefsOrchestratorEndpoint):
            available = sorted(
                n for n, e in endpoints.items() if isinstance(e, BriefsOrchestratorEndpoint)
            )
            raise ValueError(
                f"endpoint {name!r} names briefs_orchestrator={orch_name!r}, but "
                f"that endpoint is a {type(orch).__name__}, not a "
                f"BriefsOrchestratorEndpoint. Available "
                f"BriefsOrchestratorEndpoint names: {available}"
            )

        # Bind orch + mcp_endpoint into the closure default args so the
        # mounter doesn't pick up a late-bound loop variable if the
        # caller iterates more than one MCP endpoint in this pass.
        def _mounter(
            bus_handle: BusHandle,
            *,
            orch: BriefsOrchestratorEndpoint = orch,
            mcp_endpoint: ClaudeCodeMCPEndpoint = endpoint,
        ) -> None:
            register_briefs_tools(
                mcp=mcp_endpoint._mcp,
                orchestrator=orch,
                bus_handle=bus_handle,
                audit_log=orch.audit_log,
                destination_factories=orch.destination_factories,
            )

        endpoint.deferred_tool_mounters.append(_mounter)
