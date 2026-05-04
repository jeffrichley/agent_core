"""Pluggy plugin-manager bootstrap for agent_core."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import pluggy

from agent_core.bus.protocol import Endpoint, NotificationBrokerAwareEndpoint
from agent_core.plugins.specs import AgentCoreSpecs, RunnerServices

if TYPE_CHECKING:
    import typer

log = logging.getLogger(__name__)
hookimpl = pluggy.HookimplMarker("agent_core")


class PluginRegistryError(Exception):
    """Raised when plugin type registrations are invalid."""


class BuiltinRuntimePlugin:
    """Built-in runtime wiring that should always be present."""

    @hookimpl
    def configure_endpoint_instance(self, instance, endpoint_name, endpoint_config, services):
        if isinstance(instance, NotificationBrokerAwareEndpoint):
            instance.attach_notify_broker(services.notify_broker)

    @hookimpl
    def configure_bus_hook_instance(self, instance, stage, hook_config, services):
        # Reserved for future built-in bus-hook wiring.
        return None

    @hookimpl
    def validate_config(self, raw_config):
        # Reserved for future built-in config validation.
        return None


def create_plugin_manager() -> pluggy.PluginManager:
    """Create and populate the agent_core Pluggy manager."""
    pm = pluggy.PluginManager("agent_core")
    pm.add_hookspecs(AgentCoreSpecs)
    pm.register(BuiltinRuntimePlugin(), name="agent_core_builtin_runtime")
    try:
        pm.load_setuptools_entrypoints("agent_core")
    except Exception:
        log.warning("failed loading agent_core entry-point plugins", exc_info=True)
    return pm


def _merge_type_maps(
    groups: Iterable[dict[str, type[Any]]] | dict[str, type[Any]],
    *,
    kind: str,
) -> dict[str, type[Any]]:
    merged: dict[str, type[Any]] = {}
    if isinstance(groups, dict):
        groups_iterable: Iterable[dict[str, type[Any]]] = [groups]
    else:
        groups_iterable = groups
    for mapping in groups_iterable:
        for type_id, cls in mapping.items():
            if type_id in merged and merged[type_id] is not cls:
                raise PluginRegistryError(
                    f"duplicate {kind} type id {type_id!r} registered by multiple plugins"
                )
            merged[type_id] = cls
    return merged


def get_endpoint_types(pm: pluggy.PluginManager) -> dict[str, type[Any]]:
    return _merge_type_maps(pm.hook.register_endpoint_types(), kind="endpoint")


def get_bus_hook_types(pm: pluggy.PluginManager) -> dict[str, type[Any]]:
    return _merge_type_maps(pm.hook.register_bus_hook_types(), kind="bus-hook")


def get_hook_tool_types(pm: pluggy.PluginManager) -> dict[str, type[Any]]:
    return _merge_type_maps(pm.hook.register_hook_tool_types(), kind="hook-tool")


def get_bus_log_projectors(pm: pluggy.PluginManager) -> dict[str, Any]:
    """Discover projector registrations from all loaded plugins.

    Last-write-wins on duplicate keys (intentional override semantics —
    contrast with ``_merge_type_maps`` which raises on conflict because
    type-id duplicates are programming errors, not configurations).
    """
    merged: dict[str, Any] = {}
    for mapping in pm.hook.register_bus_log_projectors():
        merged.update(mapping)
    return merged


def apply_cli_subapps(pm: pluggy.PluginManager, app: typer.Typer) -> None:
    """Invoke ``register_cli_subapps`` across every plugin.

    Each hookimpl receives the same top-level Typer ``app`` and is expected
    to call ``app.add_typer(subapp, name=...)`` on it. Used at CLI module
    load to discover satellite-package subapps without ``agent_core``
    importing them directly (the layering inversion T15 review flagged).
    """
    pm.hook.register_cli_subapps(app=app)


def apply_endpoint_wiring(
    pm: pluggy.PluginManager,
    endpoints: dict[str, Endpoint],
    raw_endpoint_configs: dict[str, dict[str, Any]],
    services: RunnerServices,
) -> None:
    """Invoke ``wire_endpoints_after_registration`` across every plugin.

    Called by the runner after every endpoint has been constructed +
    registered on the bus but before ``bus.start()``. Plugins use this
    seam to install cross-endpoint wiring — e.g., the briefs plugin
    pairs a briefs orchestrator with a ``ClaudeCodeMCPEndpoint`` that
    names it as ``params.briefs_orchestrator``.
    """
    pm.hook.wire_endpoints_after_registration(
        endpoints=endpoints,
        raw_endpoint_configs=raw_endpoint_configs,
        services=services,
    )
