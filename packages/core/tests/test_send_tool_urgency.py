"""The send MCP tool accepts an urgency parameter and threads it through to Envelope."""

from __future__ import annotations

import pytest
from fastmcp import Client

from agent_core.bus.envelope import Envelope
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _CapturingHandle:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...

    def endpoints(self):
        return []


@pytest.mark.asyncio
async def test_send_tool_default_urgency_is_green():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    handle = _CapturingHandle()
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            await client.call_tool(
                "send",
                {
                    "to": "stub",
                    "kind": "TextMessage",
                    "payload": {"kind": "TextMessage", "text": "hi"},
                },
            )
        assert handle.published[0].urgency == "green"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_tool_red_urgency_propagates():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    handle = _CapturingHandle()
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            await client.call_tool(
                "send",
                {
                    "to": "stub",
                    "kind": "TextMessage",
                    "payload": {"kind": "TextMessage", "text": "alert"},
                    "urgency": "red",
                },
            )
        assert handle.published[0].urgency == "red"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_tool_invalid_urgency_raises():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    handle = _CapturingHandle()
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            with pytest.raises(Exception):
                # The tool surface validates via Pydantic; "blue" is rejected.
                await client.call_tool(
                    "send",
                    {
                        "to": "stub",
                        "kind": "TextMessage",
                        "payload": {"kind": "TextMessage", "text": "x"},
                        "urgency": "blue",
                    },
                )
    finally:
        await ep.stop()
