"""Pluggy plugin-manager bootstrap for agent_core."""

from __future__ import annotations

import logging

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
