"""Stdio MCP server: handshake declares claude/channel capability, no tools.

The end-to-end test (Layer 3) lives in test_end_to_end_relay.py and exercises
the full pipeline. These tests focus on the MCP handshake itself.
"""

from __future__ import annotations

import anyio
import pytest

from agent_core_channel.stdio_server import build_initialization_options


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
    """emit_channel_notification serializes a SessionMessage to the write stream."""
    from agent_core_channel.stdio_server import emit_channel_notification

    send_stream, receive_stream = anyio.create_memory_object_stream(max_buffer_size=8)

    summary = {
        "content": "INBOX: 1 pending",
        "meta": {"count": 1, "endpoint": "agent-a"},
    }
    await emit_channel_notification(send_stream, summary)

    # Pull the SessionMessage and inspect it.
    msg = await receive_stream.receive()
    root = msg.message.root
    assert root.method == "notifications/claude/channel"
    assert root.params == summary
