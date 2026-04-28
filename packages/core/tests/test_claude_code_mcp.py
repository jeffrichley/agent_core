"""Tests for the ClaudeCodeMCPEndpoint adapter."""

from __future__ import annotations

import pytest

from agent_core.bus.http_host import MCPHostable
from agent_core.bus.protocol import Endpoint
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


def test_endpoint_satisfies_endpoint_protocol():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    assert isinstance(ep, Endpoint)


def test_endpoint_satisfies_mcp_hostable_protocol():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    assert isinstance(ep, MCPHostable)


def test_endpoint_exposes_name_and_mount():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    assert ep.name == "agent-test"
    assert ep.mount == "/mcp/agent-test"


def test_endpoint_asgi_app_returns_callable():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    app = ep.asgi_app()
    assert callable(app)


@pytest.mark.asyncio
async def test_start_stop_lifecycle_no_session():
    """Endpoint can start and stop cleanly with no MCP session attached."""
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")

    class _FakeHandle:
        async def publish(self, *a, **kw): ...
        async def ack(self, *a, **kw): ...
        async def nack(self, *a, **kw): ...
        def endpoints(self): return []

    await ep.start(_FakeHandle())
    await ep.stop()


import uuid
from datetime import datetime, timezone

from fastmcp import Client

from agent_core.bus.envelope import EndpointInfo, Envelope, TextMessagePayload


class _RecordingHandle:
    """Test-double BusHandle that records publishes and exposes a fake directory."""

    def __init__(self, *, endpoints: list[EndpointInfo] | None = None):
        self._endpoints = endpoints or []
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        if to is not None:
            envelope = envelope.model_copy(update={"to": to if isinstance(to, str) else to[0]})
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]:
        return list(self._endpoints)


@pytest.mark.asyncio
async def test_send_tool_publishes_envelope():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandle()
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
        assert len(handle.published) == 1
        env = handle.published[0]
        assert env.to == "stub"
        assert env.kind == "TextMessage"
        assert isinstance(env.payload, TextMessagePayload)
        assert env.payload.text == "hi"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_tool_accepts_optional_correlation_metadata():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandle()
    await ep.start(handle)
    try:
        cid = uuid.uuid4().hex
        async with Client(ep._mcp) as client:
            await client.call_tool(
                "send",
                {
                    "to": "stub",
                    "kind": "TextMessage",
                    "payload": {"kind": "TextMessage", "text": "ping"},
                    "correlation_id": cid,
                    "metadata": {"trace": "x"},
                },
            )
        env = handle.published[0]
        assert env.correlation_id == cid
        assert env.metadata == {"trace": "x"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_list_endpoints_tool_returns_directory():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandle(
        endpoints=[
            EndpointInfo(name="stub", description="echo for tests"),
            EndpointInfo(name="agent-test", description="the test agent"),
        ]
    )
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            res = await client.call_tool("list_endpoints", {})
        names = {item["name"] for item in res.data}
        assert names == {"stub", "agent-test"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_describe_endpoint_tool_finds_match():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandle(
        endpoints=[EndpointInfo(name="stub", description="echo")]
    )
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            res = await client.call_tool("describe_endpoint", {"name": "stub"})
        assert res.data == {"name": "stub", "description": "echo"}
        miss = await Client(ep._mcp).__aenter__()
        try:
            res2 = await miss.call_tool("describe_endpoint", {"name": "nope"})
            assert res2.data is None
        finally:
            await miss.__aexit__(None, None, None)
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_tool_errors_when_endpoint_not_started():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    async with Client(ep._mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool(
                "send",
                {
                    "to": "stub",
                    "kind": "TextMessage",
                    "payload": {"kind": "TextMessage", "text": "x"},
                },
            )
