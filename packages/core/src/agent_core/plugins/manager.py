"""Pluggy plugin-manager bootstrap for agent_core."""

from __future__ import annotations

import importlib
import logging
from typing import Any

import pluggy

from agent_core.bus.protocol import NotificationBrokerAwareEndpoint
from agent_core.plugins.specs import AgentCoreSpecs

log = logging.getLogger(__name__)
hookimpl = pluggy.HookimplMarker("agent_core")


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


class BuiltinImportResolverPlugin:
    """Built-in fallback resolver for dotted import class paths."""

    @hookimpl(trylast=True)
    def resolve_class(self, class_path: str) -> type[Any] | None:
        module_path, _, class_name = class_path.rpartition(".")
        if not module_path:
            return None
        try:
            module = importlib.import_module(module_path)
        except ImportError:
            return None
        resolved = getattr(module, class_name, None)
        return resolved if isinstance(resolved, type) else None


def create_plugin_manager() -> pluggy.PluginManager:
    """Create and populate the agent_core Pluggy manager."""
    pm = pluggy.PluginManager("agent_core")
    pm.add_hookspecs(AgentCoreSpecs)
    pm.register(BuiltinRuntimePlugin(), name="agent_core_builtin_runtime")
    pm.register(BuiltinImportResolverPlugin(), name="agent_core_builtin_import_resolver")
    try:
        pm.load_setuptools_entrypoints("agent_core")
    except Exception:
        log.warning("failed loading agent_core entry-point plugins", exc_info=True)
    return pm
