"""Stdio MCP server: handshake declares claude/channel capability, no tools.

The end-to-end test (Layer 3) lives in test_end_to_end_relay.py and exercises
the full pipeline. These tests focus on the MCP handshake itself.
"""

from __future__ import annotations

import anyio
import pytest
from anyio import BrokenResourceError

import agent_core_channel.stdio_server as stdio_server
from agent_core_channel.stdio_server import (
    _InitializationGateWriteStream,
    _sse_pump,
    build_initialization_options,
)


def test_initialization_options_declare_claude_channel_capability():
    """init_options.capabilities.experimental must contain claude/channel."""
    init_options = build_initialization_options(server_name="agent-core-channel")
    caps = init_options.capabilities
    # Pydantic / BaseModel — accept either dict-style or attribute-style.
    experimental = getattr(caps, "experimental", None) or {}
    assert "claude/channel" in experimental


def test_initialization_options_declare_no_tools_resources_prompts():
    """The relay exposes none of the standard MCP server features."""
    init_options = build_initialization_options(server_name="agent-core-channel")
    caps = init_options.capabilities
    # Tools/resources/prompts capabilities should be absent or default-empty.
    # We don't enforce a specific representation; just verify nothing is advertised.
    tools = getattr(caps, "tools", None)
    resources = getattr(caps, "resources", None)
    prompts = getattr(caps, "prompts", None)
    # If any are set, they must indicate empty/no listChanged tracking — but
    # the cleanest assertion is "all None / falsy".
    assert not tools
    assert not resources
    assert not prompts


@pytest.mark.asyncio
async def test_emit_channel_notification_writes_jsonrpc_to_stream():
    """emit_channel_notification serializes a SessionMessage to the write stream
    and coerces meta values to strings per Claude Code's channel spec
    (Record<string, string>)."""
    from agent_core_channel.stdio_server import emit_channel_notification

    send_stream, receive_stream = anyio.create_memory_object_stream(max_buffer_size=8)

    summary = {
        "content": "INBOX: 1 pending",
        "meta": {
            "count": 1,  # int -> "1"
            "endpoint": "agent-a",  # str passes through
            "by_sender": [{"from": "x"}],  # complex -> json
            "bad-key": "dropped",  # hyphen key dropped
        },
    }
    await emit_channel_notification(send_stream, summary)

    msg = await receive_stream.receive()
    root = msg.message.root
    assert root.method == "notifications/claude/channel"
    assert root.params["content"] == "INBOX: 1 pending"
    meta = root.params["meta"]
    assert meta["count"] == "1"
    assert meta["endpoint"] == "agent-a"
    assert meta["by_sender"] == '[{"from": "x"}]'
    assert "bad-key" not in meta
    # All values are strings.
    assert all(isinstance(v, str) for v in meta.values())


@pytest.mark.asyncio
async def test_initialization_gate_opens_after_initialize_response_is_sent():
    from mcp.shared.session import SessionMessage
    from mcp.types import JSONRPCMessage, JSONRPCResponse

    send_stream, receive_stream = anyio.create_memory_object_stream(max_buffer_size=8)
    initialized = anyio.Event()
    gated = _InitializationGateWriteStream(send_stream, initialized)

    msg = SessionMessage(
        message=JSONRPCMessage(
            JSONRPCResponse(
                jsonrpc="2.0",
                id=1,
                result={
                    "serverInfo": {"name": "agent-core-channel", "version": "0.1.0"},
                    "capabilities": {"experimental": {"claude/channel": {}}},
                },
            )
        )
    )
    await gated.send(msg)

    assert initialized.is_set()
    assert await receive_stream.receive() is msg


@pytest.mark.asyncio
async def test_initialization_gate_supports_async_context_manager():
    """MCP session enters the write stream with ``async with``; wrapper must delegate."""

    class _InnerWithAcm:
        def __init__(self) -> None:
            self.entered = False
            self.exited = False

        async def __aenter__(self) -> _InnerWithAcm:
            self.entered = True
            return self

        async def __aexit__(self, *_args: object) -> None:
            self.exited = True

        async def send(self, _msg: object) -> None:
            pass

    inner = _InnerWithAcm()
    initialized = anyio.Event()
    gated = _InitializationGateWriteStream(inner, initialized)  # type: ignore[arg-type]

    async with gated:
        assert inner.entered is True
        assert inner.exited is False
    assert inner.exited is True


@pytest.mark.asyncio
async def test_sse_pump_waits_for_initialization_before_consuming_events(monkeypatch):
    async def fake_iter_notify_events(*, agent: str, daemon_url: str):
        yield {"content": "INBOX: 1 pending", "meta": {"count": 1}}

    monkeypatch.setattr(stdio_server, "iter_notify_events", fake_iter_notify_events)

    initialized = anyio.Event()
    send_stream, receive_stream = anyio.create_memory_object_stream(max_buffer_size=8)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_sse_pump, "agent", "http://daemon", send_stream, initialized)
        await anyio.sleep(0.01)
        with pytest.raises(anyio.WouldBlock):
            receive_stream.receive_nowait()

        initialized.set()
        msg = await receive_stream.receive()
        assert msg.message.root.method == "notifications/claude/channel"
        tg.cancel_scope.cancel()


@pytest.mark.asyncio
async def test_sse_pump_treats_write_failures_as_fatal(monkeypatch):
    async def fake_iter_notify_events(*, agent: str, daemon_url: str):
        yield {"content": "INBOX: 1 pending", "meta": {"count": 1}}

    class _BrokenWriteStream:
        async def send(self, _msg):
            raise BrokenResourceError

    monkeypatch.setattr(stdio_server, "iter_notify_events", fake_iter_notify_events)

    initialized = anyio.Event()
    initialized.set()

    with pytest.raises(BrokenResourceError):
        await _sse_pump("agent", "http://daemon", _BrokenWriteStream(), initialized)
