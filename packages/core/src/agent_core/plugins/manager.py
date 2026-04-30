"""Pluggy plugin-manager bootstrap for agent_core."""

from __future__ import annotations

import logging

import pluggy

from agent_core.plugins.specs import AgentCoreSpecs

log = logging.getLogger(__name__)


def create_plugin_manager() -> pluggy.PluginManager:
    """Create and populate the agent_core Pluggy manager."""
    pm = pluggy.PluginManager("agent_core")
    pm.add_hookspecs(AgentCoreSpecs)
    try:
        pm.load_setuptools_entrypoints("agent_core")
    except Exception:
        log.warning("failed loading agent_core entry-point plugins", exc_info=True)
    return pm
