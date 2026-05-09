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
import uuid
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol, Self, cast

import anyio
from mcp.server.lowlevel.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.shared.session import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCResponse

from agent_core_channel.rendering import (
    FallbackToBare,
    InlineAll,
    RedeliveryTracker,
    apply_circuit_breaker,
)
from agent_core_channel.sse_client import iter_notify_events
from agent_core_channel.wake_audit import WakeAuditWriter

if TYPE_CHECKING:
    from mcp.server.models import InitializationOptions

log = logging.getLogger(__name__)


_RELAY_INSTRUCTIONS = (
    "Inbox wake notifications for an agent-core agent. "
    'Messages arrive as JSON-RPC notifications with method "notifications/claude/channel". '
    "When inline mode is enabled (default), params.content carries the inbound "
    "envelope(s) directly as one or more <inbox kind='X' from='Y' urgency='Z' "
    "envelope_id='abc'>...body...</inbox> blocks. Read the body inline; reply via "
    "mcp__agent-core__reply(in_reply_to=envelope_id, payload=...) for atomic "
    "publish+ack. Dismiss without reply via mcp__agent-core__handle(envelope_id). "
    "If a body shows '[content elided; envelope_id=X; call peek(X) for full payload]', "
    "call mcp__agent-core__peek(envelope_id=X) to fetch the full envelope. "
    "If params.content is the bare 'INBOX: pending (<endpoint>)' string (circuit "
    "breaker tripped, transient failure, or inline mode disabled), call "
    "mcp__agent-core__consume() for the authoritative queue snapshot and proceed "
    "as before. Higher urgency tiers (red > yellow > green) are presented first. "
    "Do not wait for user input — the notification IS the prompt."
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


class BusClientProtocol(Protocol):
    """Subset of BusClient that process_wake_event needs (so test fakes work)."""

    async def consume_no_ack(self, *, batch_window_seconds: int = 30) -> dict: ...


async def process_wake_event(
    *,
    wake_summary: dict,
    write_stream: anyio.abc.ObjectSendStream[SessionMessage],
    bus: BusClientProtocol,
    audit: WakeAuditWriter,
    agent: str,
    config: Any,  # RelayConfig
    redelivery: RedeliveryTracker,
) -> None:
    """Apply the inline-content render pipeline to a single wake event.

    On any failure or circuit-breaker trip, falls back to today's bare-wake
    shape — inline is the fast path, the slow path always works.

    Issue #70.
    """
    wake_id = uuid.uuid4().hex

    if config.inline_mode == "disabled":
        await emit_channel_notification(write_stream, wake_summary)
        audit.write_fallback(
            wake_id=wake_id, agent=agent, mode=config.inline_mode,
            reason="disabled_mode",
        )
        return

    try:
        snapshot = await bus.consume_no_ack(batch_window_seconds=30)
    except Exception as exc:
        log.warning("relay: bus client raised; falling back to bare wake: %s", exc)
        await emit_channel_notification(write_stream, wake_summary)
        audit.write_fallback(
            wake_id=wake_id, agent=agent, mode=config.inline_mode,
            reason="render_error", error_message=str(exc),
        )
        return

    items = snapshot.get("items", [])
    if not items:
        # Phantom wake — suppress agent-facing notification but log it.
        audit.write_fallback(
            wake_id=wake_id, agent=agent, mode=config.inline_mode,
            reason="empty_queue",
        )
        return

    try:
        result = apply_circuit_breaker(
            items,
            max_envelopes=config.max_envelopes,
            max_total_bytes=config.max_bytes,
            max_per_envelope_bytes=config.per_envelope_bytes,
            mode="full" if config.inline_mode == "full" else "preview",
        )
    except Exception as exc:
        log.warning("relay: render pipeline raised; falling back to bare wake: %s", exc)
        await emit_channel_notification(write_stream, wake_summary)
        audit.write_fallback(
            wake_id=wake_id, agent=agent, mode=config.inline_mode,
            reason="render_error", error_message=str(exc),
        )
        return

    if isinstance(result, FallbackToBare):
        await emit_channel_notification(write_stream, wake_summary)
        audit.write_fallback(
            wake_id=wake_id, agent=agent, mode=config.inline_mode,
            reason=result.reason,
        )
        return

    assert isinstance(result, InlineAll)
    # Apply RESEND markers.
    final_blocks: list[str] = []
    for env_id, block in zip(result.inlined_ids, result.rendered, strict=True):
        marker = redelivery.note_and_get_marker(env_id)
        if marker is None:
            final_blocks.append(block)
        else:
            final_blocks.append(block.replace("<inbox ", f"<inbox resend='{marker.strip()}' ", 1))

    rich_summary = {
        "content": "\n\n".join(final_blocks),
        "meta": {
            **wake_summary.get("meta", {}),
            "wake_id": wake_id,
            "queue_total_count": str(snapshot.get("meta", {}).get("count", 0)),
            "envelopes_inlined": ",".join(result.inlined_ids),
        },
    }
    await emit_channel_notification(write_stream, rich_summary)
    audit.write_inlined(
        wake_id=wake_id, agent=agent, mode=config.inline_mode,
        envelopes_inlined=result.inlined_envelopes_summary,
        queue_total_count=int(snapshot.get("meta", {}).get("count", 0)),
    )


async def _sse_pump(
    agent: str,
    daemon_url: str,
    write_stream: anyio.abc.ObjectSendStream[SessionMessage],
    initialized: anyio.Event | None = None,
    *,
    bus: BusClientProtocol,
    audit: WakeAuditWriter,
    config: Any,  # RelayConfig
    redelivery: RedeliveryTracker,
) -> None:
    """Read events from /notify/<agent> and route through process_wake_event."""
    if initialized is not None:
        await initialized.wait()
    async for wake_summary in iter_notify_events(agent=agent, daemon_url=daemon_url):
        await process_wake_event(
            wake_summary=wake_summary,
            write_stream=write_stream,
            bus=bus,
            audit=audit,
            agent=agent,
            config=config,
            redelivery=redelivery,
        )


async def run_relay(agent: str, daemon_url: str) -> None:
    """Run the channel relay until stdin closes or a fatal error.

    Three concurrent tasks under one task group:
    - The MCP stdio server loop (Server.run reading from stdin, writing to stdout).
    - The persistent BusClient connection to /mcp/<agent>.
    - The SSE pump (consume daemon /notify/<agent>, route through
      process_wake_event for inline rendering).

    Stdin closes → Server.run returns → task group cancels SSE pump and
    BusClient. SSE pump dies (it shouldn't — it has its own retry loop) →
    cancels Server.run.
    """
    from agent_core_channel.bus_client import BusClient
    from agent_core_channel.config import RelayConfig

    server = _build_server()
    init_options = server.create_initialization_options(
        notification_options=NotificationOptions(),
        experimental_capabilities={"claude/channel": {}},
    )

    config = RelayConfig()  # Phase 9 will wire CLI/env/YAML overrides.
    audit_dir = Path.home() / ".agent-core" / "wake_audit"
    audit = WakeAuditWriter(audit_dir / f"{agent}.jsonl")
    redelivery = RedeliveryTracker()

    async with stdio_server() as (read_stream, write_stream):
        initialized = anyio.Event()
        gated_write_stream = _InitializationGateWriteStream(write_stream, initialized)
        async with BusClient(daemon_url, agent) as bus:

            async def _pump() -> None:
                await _sse_pump(
                    agent, daemon_url, cast(Any, gated_write_stream), initialized,
                    bus=bus, audit=audit, config=config, redelivery=redelivery,
                )

            async with anyio.create_task_group() as tg:
                tg.start_soon(_pump)
                await server.run(read_stream, cast(Any, gated_write_stream), init_options)
                tg.cancel_scope.cancel()
