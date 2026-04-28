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
