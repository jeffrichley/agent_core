"""Pluggy plugin-manager bootstrap for agent_core."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import pluggy

from agent_core.bus.protocol import (
    AuditWriterAwareEndpoint,
    Endpoint,
    NotificationBrokerAwareEndpoint,
)
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
        if (
            isinstance(instance, AuditWriterAwareEndpoint)
            and services.mcp_audit_writer is not None
        ):
            instance.attach_audit_writer(
                services.mcp_audit_writer, services.mcp_audit_skip_tools
            )

    @hookimpl
    def configure_bus_hook_instance(self, instance, stage, hook_config, services):
        # Reserved for future built-in bus-hook wiring.
        return None

    @hookimpl
    def validate_config(self, raw_config):
        import pydantic

        from agent_core.bus.config import BusBootError, DaemonConfig
        try:
            DaemonConfig.model_validate(raw_config)
        except pydantic.ValidationError as exc:
            raise BusBootError(f"daemon config validation failed:\n{exc}") from exc


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


def get_envelope_renderers(pm: pluggy.PluginManager) -> dict[str, Any]:
    """Aggregate ``{kind: renderer_callable}`` registrations across all plugins.

    Raises ``PluginRegistryError`` on duplicate-kind collisions (same
    invariant as :func:`get_endpoint_types`). Distinct plugins MAY register
    distinct kinds; two plugins registering the same kind is a programming
    error surfaced loudly at startup.

    Per spec §2.4, callers merge this with built-in renderers in
    ``agent-core-channel/rendering.py``; the plugin-registered renderers
    take precedence on collisions with built-ins (operator-aware override).
    """
    merged: dict[str, Any] = {}
    for mapping in pm.hook.register_envelope_renderers():
        if not mapping:
            continue
        for kind, renderer in mapping.items():
            if kind in merged and merged[kind] is not renderer:
                raise PluginRegistryError(
                    f"duplicate envelope-renderer kind {kind!r} registered by multiple plugins"
                )
            merged[kind] = renderer
    return merged


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


def collect_reserved_endpoint_params(pm: pluggy.PluginManager) -> set[str]:
    """Aggregate ``reserved_endpoint_params`` returns across all plugins.

    Each plugin's hookimpl returns a list of param names it consumes
    post-construction (e.g., the briefs plugin returns
    ``["briefs_orchestrator"]``). The runner unions them and pops these
    keys from each endpoint's ``params:`` before calling ``__init__``,
    so endpoint classes don't have to know about plugin-managed keys.

    Defensive about return shape (matches ``_merge_type_maps`` convention):
    real pluggy aggregates as a list-of-lists (one inner list per plugin);
    test stubs may return a single flat list directly. Both forms work.
    """
    raw = pm.hook.reserved_endpoint_params() or []
    reserved: set[str] = set()
    # Heuristic: a flat list of strings is the test-stub shape; a list of
    # lists of strings is the pluggy multi-plugin shape. Distinguish by
    # checking whether the first element is iterable-but-not-a-string.
    if raw and all(isinstance(item, str) for item in raw):
        reserved.update(raw)
        return reserved
    for plugin_result in raw:
        if not plugin_result:
            continue
        reserved.update(plugin_result)
    return reserved


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
