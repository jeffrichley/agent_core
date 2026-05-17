"""build_busproxy assembles a proxy that mirrors the daemon tool surface."""

from __future__ import annotations

import pytest
from agent_core_busproxy.proxy import build_busproxy
from fastmcp import Client

from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _StubHandle:
    async def ack(self, envelope_id: str) -> None: ...
    async def publish(self, envelope, to=None) -> None: ...
    async def nack(self, envelope_id, requeue=True) -> None: ...
    def endpoints(self) -> list:
        return []


# The bus tool surface ClaudeCodeMCPEndpoint guarantees (see
# claude_code_mcp.py _register_tools). Asserting these by name avoids
# depending on any FastMCP-internal tool-enumeration API.
_EXPECTED_BUS_TOOLS = {
    "send",
    "list_endpoints",
    "describe_endpoint",
    "list_pending",
    "handle",
    "ack",
    "nack",
    "consume",
    "reply",
    "peek",
    "show_my_day",
}


@pytest.mark.asyncio
async def test_proxy_mirrors_backend_tool_surface() -> None:
    """tools/list through the proxy exposes the daemon endpoint's tools."""
    backend = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    await backend.start(_StubHandle())  # type: ignore[arg-type]
    try:
        # Proxy pointed directly at the in-process backend server.
        proxy = build_busproxy(agent="agent", daemon_url=None, _backend=backend._mcp)
        async with Client(proxy) as c:
            proxied = {t.name for t in await c.list_tools()}

        missing = _EXPECTED_BUS_TOOLS - proxied
        assert not missing, f"proxy missing bus tools: {missing}"
    finally:
        await backend.stop()
