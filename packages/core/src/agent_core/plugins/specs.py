"""Pluggy hook specifications for agent_core runtime plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import pluggy

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

    @hookspec(firstresult=True)
    def resolve_class(self, class_path: str) -> type[Any] | None:
        """Return a class for `class_path`, or None to defer."""

    @hookspec(firstresult=True)
    def resolve_endpoint_class(self, endpoint_class: str) -> type[Endpoint] | None:
        """Return an endpoint class for `endpoint_class`, or None to defer."""

    @hookspec(firstresult=True)
    def resolve_bus_hook_class(self, hook_class: str) -> type[BusHook] | None:
        """Return a bus-hook class for `hook_class`, or None to defer."""

    @hookspec(firstresult=True)
    def resolve_hook_tool_class(self, tool_class: str) -> type[HookTool] | None:
        """Return a hook-tool class for `tool_class`, or None to defer."""

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
