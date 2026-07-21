"""Built-in alias plugin for common endpoint/tool class paths.

This plugin provides short, stable aliases that resolve to concrete runtime
classes. It is loaded through the ``agent_core`` entrypoint group.
"""

from __future__ import annotations

from typing import Any

import pluggy

from agent_core.bus_hooks.daily_raw_jsonl import DailyRawJsonlHook
from agent_core.bus_log.projectors import (
    AcknowledgmentSkipProjector,
    HandoffFailedProjector,
    HandoffReadyProjector,
    SchedulerHeartbeatSkipProjector,
)
from agent_core.bus_tail.endpoint import BusTailMCPEndpoint
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core.endpoints.handoff_jobs import HandoffJobsEndpoint
from agent_core.endpoints.scheduler import SchedulerEndpoint
from agent_core.endpoints.stub import StubEndpoint
from agent_core.hooks.tools.handoff_injector import HandoffInjector
from agent_core.hooks.tools.handoff_writer import HandoffWriter
from agent_core.hooks.tools.identity_injector import IdentityInjector
from agent_core.hooks.tools.session_end_writer import SessionEndWriter
from agent_core.hooks.tools.time_injector import TimeInjector

hookimpl = pluggy.HookimplMarker("agent_core")


_ENDPOINT_TYPES: dict[str, type[Any]] = {
    "builtin.bus_tail_mcp": BusTailMCPEndpoint,
    "builtin.claude_code_mcp": ClaudeCodeMCPEndpoint,
    "builtin.handoff_jobs": HandoffJobsEndpoint,
    "builtin.scheduler": SchedulerEndpoint,
    "builtin.stub": StubEndpoint,
}

_HOOK_TOOL_TYPES: dict[str, type[Any]] = {
    "builtin.handoff_injector": HandoffInjector,
    "builtin.handoff_writer": HandoffWriter,
    "builtin.identity_injector": IdentityInjector,
    "builtin.session_end_writer": SessionEndWriter,
    "builtin.time_injector": TimeInjector,
}

_BUS_HOOK_TYPES: dict[str, type[Any]] = {
    "builtin.daily_raw_jsonl": DailyRawJsonlHook,
}

_BUS_LOG_PROJECTORS: dict[str, Any] = {
    # SchedulerHeartbeatSkipProjector replaces the plain TextMessage
    # projector: it filters scheduler heartbeats to None and delegates
    # everything else to TextMessageProjector.
    "TextMessage": SchedulerHeartbeatSkipProjector(),
    "Acknowledgment": AcknowledgmentSkipProjector(),
    "HandoffReady": HandoffReadyProjector(),
    "HandoffFailed": HandoffFailedProjector(),
}


@hookimpl
def register_bus_hook_types() -> dict[str, type[Any]]:
    return dict(_BUS_HOOK_TYPES)


@hookimpl
def register_endpoint_types() -> dict[str, type[Any]]:
    endpoint_types = dict(_ENDPOINT_TYPES)
    if "builtin.discord" not in endpoint_types:
        try:
            from agent_core_discord.endpoint import DiscordEndpoint
        except ImportError:
            # Justified: agent-core-discord is an optional extra; when it is
            # not installed the builtin.discord endpoint is simply unavailable.
            pass
        else:
            endpoint_types["builtin.discord"] = DiscordEndpoint
    return endpoint_types


@hookimpl
def register_hook_tool_types() -> dict[str, type[Any]]:
    return dict(_HOOK_TOOL_TYPES)


@hookimpl
def register_bus_log_projectors() -> dict[str, Any]:
    """Default projector registrations for built-in envelope shapes."""
    return dict(_BUS_LOG_PROJECTORS)
