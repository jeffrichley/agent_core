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


@dataclass(frozen=True)
class RunnerServices:
    """Shared runtime services a plugin may use during wiring."""

    notify_broker: NotificationBroker


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
