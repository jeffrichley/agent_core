"""Daemon-down window: a tool call returns the structured transient
result PROMPTLY (fail-fast, no long hang)."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client

from agent_core_busproxy.proxy import build_busproxy
from agent_core_busproxy.transient import TRANSIENT_ERROR_CODE


@pytest.mark.asyncio
async def test_dead_backend_returns_transient_result_fast() -> None:
    # Nothing listening on 65535 => connect refused/timeout.
    proxy = build_busproxy(agent="agent", daemon_url="http://127.0.0.1:65535")

    async with Client(proxy) as c:
        # Whole call must finish well under the 5s init budget * slack.
        result = await asyncio.wait_for(
            c.call_tool("list_endpoints", {}), timeout=15.0
        )

    assert result.structured_content["error"] == TRANSIENT_ERROR_CODE
    assert result.structured_content["transient"] is True
