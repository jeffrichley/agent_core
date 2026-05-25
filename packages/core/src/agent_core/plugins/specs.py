"""Pluggy hook specifications for agent_core runtime plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import pluggy
import typer

hookspec = pluggy.HookspecMarker("agent_core")

if TYPE_CHECKING:
    from agent_core.bus.notify_broker import NotificationBroker
    from agent_core.bus.protocol import BusHook, Endpoint
    from agent_core.hooks.protocol import HookTool
    from agent_core.mcp_audit.writer import MCPAuditWriter


@dataclass(frozen=True)
class RunnerServices:
    """Shared runtime services a plugin may use during wiring."""

    notify_broker: NotificationBroker
    mcp_audit_writer: MCPAuditWriter | None = None
    mcp_audit_skip_tools: frozenset[str] = frozenset()


class AgentCoreSpecs:
    """Hook contracts for discovery and runtime wiring."""

    @hookspec
    def register_endpoint_types(self) -> dict[str, type[Endpoint]]:
        """Return endpoint type-id -> class registrations."""
        raise NotImplementedError

    @hookspec
    def register_bus_hook_types(self) -> dict[str, type[BusHook]]:
        """Return bus-hook type-id -> class registrations."""
        raise NotImplementedError

    @hookspec
    def register_hook_tool_types(self) -> dict[str, type[HookTool]]:
        """Return hook-tool type-id -> class registrations."""
        raise NotImplementedError

    @hookspec
    def configure_endpoint_instance(
        self,
        instance: Endpoint,
        endpoint_name: str,
        endpoint_config: dict[str, Any],
        services: RunnerServices,
    ) -> None:
        """Configure an endpoint instance after construction."""

    @hookspec
    def configure_bus_hook_instance(
        self,
        instance: BusHook,
        stage: Literal["pre_publish", "pre_deliver"],
        hook_config: dict[str, Any],
        services: RunnerServices,
    ) -> None:
        """Configure a bus-hook instance after construction."""

    @hookspec
    def validate_config(self, raw_config: dict[str, Any]) -> None:
        """Validate or normalize top-level runner config."""

    @hookspec
    def register_bus_log_projectors(self) -> dict[str, Any]:
        """Return projector key -> instance registrations.

        Keys are either ``EventPayload.type`` strings (e.g., "HandoffReady")
        or envelope ``kind`` strings (e.g., "TextMessage"). Last-write-wins
        on duplicate keys across plugins (intentional override semantics).
        """
        raise NotImplementedError

    @hookspec
    def register_cli_subapps(self, app: typer.Typer) -> None:
        """Mount Typer subapps onto the top-level ``agent-core`` CLI.

        Plugins import their subapp lazily inside the hookimpl and call
        ``app.add_typer(subapp, name="...")``. Used by satellite packages
        (e.g., agent-core-briefs) to extend the CLI without forcing the
        core package to import them — preserves the layering where
        ``agent_core`` does not depend on its plugins.
        """
        raise NotImplementedError

    @hookspec
    def reserved_endpoint_params(self) -> list[str]:
        """Param names plugins consume post-construction; runner filters them out.

        Endpoint yaml entries can carry ``params:`` keys that target a
        plugin's post-construction wiring (e.g., the briefs plugin
        consumes ``params.briefs_orchestrator`` via
        :meth:`wire_endpoints_after_registration`) rather than the
        endpoint class's ``__init__``. Without filtering, the runner
        would pass these as kwargs to ``__init__`` and crash on
        endpoint classes that don't accept them.

        Plugins implementing post-construction wiring return the param
        names they consume. The runner pops these from ``params:``
        before calling ``cls(name=..., **params)``; the original raw
        config (still containing the popped keys) is what gets passed
        to :meth:`wire_endpoints_after_registration` later.

        Names are global, not type-id-scoped — collisions across
        plugins on the same name would already collide on intent.
        """
        raise NotImplementedError

    @hookspec
    def wire_endpoints_after_registration(
        self,
        endpoints: dict[str, Endpoint],
        raw_endpoint_configs: dict[str, dict[str, Any]],
        services: RunnerServices,
    ) -> None:
        """Cross-endpoint wiring once all endpoints are constructed and registered.

        Called by the runner after every endpoint in the yaml has been
        instantiated and registered on the bus, but before ``bus.start()``.
        Plugins implementing this hook can inspect sibling endpoints and
        install deferred wiring (e.g., the briefs plugin pairs a briefs
        orchestrator with a ``ClaudeCodeMCPEndpoint`` that names it).

        Argument shapes:
          - ``endpoints``: name → constructed Endpoint instance.
          - ``raw_endpoint_configs``: name → the raw yaml entry's full dict
            (so plugins can read params not visible on the instance).
          - ``services``: same RunnerServices passed to
            ``configure_endpoint_instance``.
        """

    @hookspec
    def register_envelope_renderers(self) -> dict[str, Any]:
        """Return ``{kind: renderer_callable}`` for plugin-registered envelope kinds.

        Each renderer takes an envelope dict and returns the rendered body
        string for inclusion inside an ``<inbox>`` tag at inline-wake
        rendering. The ``kind`` value becomes a first-class envelope kind
        once registered — produced envelopes set ``Envelope.kind`` to the
        registered string and carry their own dict payload that the plugin
        is responsible for validating (no bus-side payload-validator
        hookspec in v0; see
        ``docs/superpowers/specs/2026-05-25-envelope-extension-hookspec-design.md``
        §1.5 + §6.1).

        Duplicate kinds across plugins raise ``PluginRegistryError`` at
        startup, mirroring the ``register_endpoint_types`` collision
        policy: kind-id duplicates are programming errors, not
        configurations. A plugin may override a built-in renderer by
        registering the same kind explicitly (operator-aware decision).

        Renderer signature: ``Callable[[dict], str]``. The dict mirrors
        the wire-format envelope shape (id, kind, from, payload, metadata,
        urgency, etc.). The returned string is the body text inside the
        ``<inbox>`` tag; the framework attrs are added separately.
        """
        raise NotImplementedError
