"""Example entry-point plugin for agent_core runtime hooks.

This module intentionally provides no-op implementations. It exists as a
reference for plugin authors and as a fixture for entry-point loading tests.
"""

from __future__ import annotations

from typing import Any, Literal

import pluggy

from agent_core.plugins.specs import RunnerServices

hookimpl = pluggy.HookimplMarker("agent_core")


@hookimpl
def register_endpoint_types() -> dict[str, type[Any]]:
    return {}


@hookimpl
def register_bus_hook_types() -> dict[str, type[Any]]:
    return {}


@hookimpl
def register_hook_tool_types() -> dict[str, type[Any]]:
    return {}


@hookimpl
def configure_endpoint_instance(
    instance: Any,
    endpoint_name: str,
    endpoint_config: dict[str, Any],
    services: RunnerServices,
) -> None:
    return None


@hookimpl
def configure_bus_hook_instance(
    instance: Any,
    stage: Literal["pre_publish", "pre_deliver"],
    hook_config: dict[str, Any],
    services: RunnerServices,
) -> None:
    return None


@hookimpl
def validate_config(raw_config: dict[str, Any]) -> None:
    return None
