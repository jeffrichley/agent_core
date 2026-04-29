"""Stdio MCP server for the channel relay.

Uses the low-level mcp.server.Server API so we can declare the
experimental ``claude/channel`` capability — FastMCP doesn't expose
that yet. The server exposes zero tools, zero resources, zero prompts;
its only job is to keep the stdio MCP handshake alive and provide a
write stream that the SSE pump can use to emit
``notifications/claude/channel`` events.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import anyio
from mcp.server.lowlevel.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.shared.session import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification

from agent_core_channel.sse_client import iter_notify_events

if TYPE_CHECKING:
    from mcp.server.models import InitializationOptions

log = logging.getLogger(__name__)


def build_initialization_options(
    server_name: str = "agent-core-channel",
) -> InitializationOptions:
    """Construct InitializationOptions that declare the claude/channel capability.

    No tools, resources, or prompts are advertised. Notification options are
    default (no list-changed tracking).
    """
    server = Server(server_name)
    notification_options = NotificationOptions()
    experimental: dict[str, dict[str, Any]] = {"claude/channel": {}}
    return server.create_initialization_options(
        notification_options=notification_options,
        experimental_capabilities=experimental,
    )


async def emit_channel_notification(
    write_stream: anyio.abc.ObjectSendStream[SessionMessage],
    summary: dict,
) -> None:
    """Write a notifications/claude/channel SessionMessage to the MCP stream."""
    notification = JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/claude/channel",
        params=summary,
    )
    msg = SessionMessage(message=JSONRPCMessage(notification))
    await write_stream.send(msg)


async def _sse_pump(
    agent: str,
    daemon_url: str,
    write_stream: anyio.abc.ObjectSendStream[SessionMessage],
) -> None:
    """Read events from /notify/<agent> and emit them as MCP notifications."""
    async for summary in iter_notify_events(agent=agent, daemon_url=daemon_url):
        try:
            await emit_channel_notification(write_stream, summary)
        except Exception:
            log.warning("sse pump: emit failed; continuing", exc_info=True)


async def run_relay(agent: str, daemon_url: str) -> None:
    """Run the channel relay until stdin closes or a fatal error.

    Two concurrent tasks under one task group:
    - The MCP stdio server loop (Server.run reading from stdin, writing to stdout).
    - The SSE pump (consume daemon /notify/<agent>, write notifications onto the
      same MCP write stream).

    When stdin closes (Claude Code shut down), Server.run() returns and the
    task group cancels the SSE pump. When the SSE pump dies (which it shouldn't
    — it has its own retry loop), the task group cancels Server.run().
    """
    server = Server("agent-core-channel")
    init_options = build_initialization_options()

    async with stdio_server() as (read_stream, write_stream):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_sse_pump, agent, daemon_url, write_stream)
            await server.run(read_stream, write_stream, init_options)
            # Server.run returned (stdin closed). Cancel the SSE pump.
            tg.cancel_scope.cancel()
