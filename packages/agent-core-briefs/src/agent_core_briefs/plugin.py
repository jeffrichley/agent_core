"""agent_core entry-point hook surface for the brief framework.

T16 wiring (cutover #09): contributes the briefs subsystem to ``agent_core``
via pluggy.

Two hookimpls:

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

Cross-endpoint coordination (mounting briefs MCP tools onto a
``ClaudeCodeMCPEndpoint`` instance with the orchestrator's bus handle) is
intentionally out of scope for T16 — that's a post-cutover deliverable.
The orchestrator becomes the BriefRequest subscriber by virtue of being
a registered bus endpoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pluggy

if TYPE_CHECKING:
    import typer

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
