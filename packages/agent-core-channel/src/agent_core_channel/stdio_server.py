"""Stdio MCP server for the channel relay.

Uses the low-level mcp.server.Server API so we can declare the
experimental ``claude/channel`` capability — FastMCP doesn't expose
that yet. The server exposes zero tools, zero resources, zero prompts;
its only job is to keep the stdio MCP handshake alive and provide a
write stream that the SSE pump can use to emit
``notifications/claude/channel`` events.
"""

from __future__ import annotations

import json
import logging
import re
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self, cast

import anyio
from mcp.server.lowlevel.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.shared.session import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCResponse

from agent_core_channel.sse_client import iter_notify_events

if TYPE_CHECKING:
    from mcp.server.models import InitializationOptions

log = logging.getLogger(__name__)


_RELAY_INSTRUCTIONS = (
    "Inbox wake notifications for an agent-core agent. "
    'Messages arrive as JSON-RPC notifications with method "notifications/claude/channel". '
    'The params object has "content" (a brief inbox summary string, '
    'e.g. "INBOX: 3 pending - 2 from discord (TextMessage), 1 from email") '
    'and "meta" (count, by_sender, urgency_counts, urgency_max, endpoint, fired_at). '
    "When such a notification arrives, treat it as a wake signal: "
    "call mcp__agent-core__list_pending to fetch the actual envelopes, "
    "process each, and respond via mcp__agent-core__send when appropriate. "
    "Higher urgency tiers (red > yellow > green) should be addressed first. "
    "Do not wait for user input - the notification IS the prompt."
)


def _build_server(server_name: str = "agent-core-channel") -> Server:
    """Construct the channel relay's MCP Server with name, version, instructions."""
    return Server(
        name=server_name,
        version="0.1.0",
        instructions=_RELAY_INSTRUCTIONS,
    )


def build_initialization_options(
    server_name: str = "agent-core-channel",
) -> InitializationOptions:
    """Construct InitializationOptions that declare the claude/channel capability.

    No tools, resources, or prompts are advertised. Notification options are
    default (no list-changed tracking).
    """
    server = _build_server(server_name)
    notification_options = NotificationOptions()
    experimental: dict[str, dict[str, Any]] = {"claude/channel": {}}
    return server.create_initialization_options(
        notification_options=notification_options,
        experimental_capabilities=experimental,
    )


_META_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _coerce_meta(meta: dict[str, Any]) -> dict[str, str]:
    """Claude Code's channel spec requires meta to be Record<string, string>;
    keys must be [A-Za-z0-9_]+ (others are silently dropped) and each value
    becomes an XML attribute on the <channel> tag. Coerce non-string values
    to JSON, drop bad keys."""
    out: dict[str, str] = {}
    for k, v in meta.items():
        if not isinstance(k, str) or not _META_KEY_RE.match(k):
            continue
        if isinstance(v, str):
            out[k] = v
        elif isinstance(v, (int, float, bool)):
            out[k] = str(v)
        else:
            out[k] = json.dumps(v, default=str)
    return out


async def emit_channel_notification(
    write_stream: anyio.abc.ObjectSendStream[SessionMessage],
    summary: dict,
) -> None:
    """Write a notifications/claude/channel SessionMessage to the MCP stream.

    Per Claude Code's channels spec, params must have content (str) and
    meta (Record<string, string>). We coerce meta values here so callers
    can pass richer dicts to the broker; the channel wire format is the
    constraint, not the broker's snapshot shape.
    """
    params: dict[str, Any] = {
        "content": str(summary.get("content", "")),
        "meta": _coerce_meta(summary.get("meta", {}) or {}),
    }
    notification = JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/claude/channel",
        params=params,
    )
    msg = SessionMessage(message=JSONRPCMessage(notification))
    await write_stream.send(msg)


def _is_initialize_response(message: SessionMessage) -> bool:
    root = message.message.root
    return (
        isinstance(root, JSONRPCResponse)
        and isinstance(root.result, dict)
        and "serverInfo" in root.result
        and "capabilities" in root.result
    )


class _InitializationGateWriteStream:
    """Forward writes and open a gate after the initialize response is sent."""

    def __init__(
        self,
        inner: anyio.abc.ObjectSendStream[SessionMessage],
        initialized: anyio.Event,
    ) -> None:
        self._inner = inner
        self._initialized = initialized

    async def __aenter__(self) -> Self:
        """MCP ``ServerSession`` uses ``async with read_stream, write_stream``; delegate."""
        inner_enter = getattr(self._inner, "__aenter__", None)
        if inner_enter is not None:
            await inner_enter()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        inner_exit = getattr(self._inner, "__aexit__", None)
        if inner_exit is not None:
            return await inner_exit(exc_type, exc_val, exc_tb)
        return None

    async def send(self, message: SessionMessage) -> None:
        await self._inner.send(message)
        if _is_initialize_response(message):
            self._initialized.set()


async def _sse_pump(
    agent: str,
    daemon_url: str,
    write_stream: anyio.abc.ObjectSendStream[SessionMessage],
    initialized: anyio.Event | None = None,
) -> None:
    """Read events from /notify/<agent> and emit them as MCP notifications."""
    if initialized is not None:
        await initialized.wait()
    async for summary in iter_notify_events(agent=agent, daemon_url=daemon_url):
        await emit_channel_notification(write_stream, summary)


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
    server = _build_server()
    init_options = server.create_initialization_options(
        notification_options=NotificationOptions(),
        experimental_capabilities={"claude/channel": {}},
    )

    async with stdio_server() as (read_stream, write_stream):
        initialized = anyio.Event()
        gated_write_stream = _InitializationGateWriteStream(write_stream, initialized)
        async with anyio.create_task_group() as tg:
            tg.start_soon(_sse_pump, agent, daemon_url, cast(Any, gated_write_stream), initialized)
            await server.run(read_stream, cast(Any, gated_write_stream), init_options)
            tg.cancel_scope.cancel()
