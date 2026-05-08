"""Integration: bus + ClaudeCodeMCPEndpoint + StubEndpoint, full round trip.

Runs the runner in-process (no separate daemon subprocess), boots the
HTTP host on an ephemeral port, opens a real MCP HTTP client against
the agent-test mount, and verifies:

1. The client sees both endpoints via list_endpoints.
2. send(to=stub, ...) routes through the bus to the stub's inbox.
3. When the stub publishes addressed to agent-test, the envelope reaches
   the agent-test endpoint's pickup queue.
"""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client

from agent_core.bus.envelope import TextMessagePayload
from agent_core.bus.runner import build_bus_from_config


@pytest.mark.asyncio
async def test_round_trip_via_real_http(tmp_path):
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - type: builtin.claude_code_mcp
    name: agent-test
    description: "test agent for integration"
    params:
      mount: /mcp/agent-test
  - type: builtin.stub
    name: stub
    description: "echo for tests"
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    try:
        await bus.start()
        try:
            url = f"http://127.0.0.1:{http_host.port}/mcp/agent-test"

            async with Client(url) as client:
                # 1. list_endpoints
                eps = (await client.call_tool("list_endpoints", {})).data
                names = {e["name"] for e in eps}
                assert names == {"agent-test", "stub"}

                # 2. send to stub — verify stub receives via its inbox
                await client.call_tool(
                    "send",
                    {
                        "to": "stub",
                        "kind": "TextMessage",
                        "payload": {"kind": "TextMessage", "text": "hello-stub"},
                    },
                )
                # Bus dispatches synchronously inside _enqueue, so by the time
                # the call returns the stub has had deliver() called.
                stub_ep = bus._endpoints_by_name["stub"].endpoint
                assert any(
                    isinstance(env.payload, TextMessagePayload) and env.payload.text == "hello-stub"
                    for env in stub_ep.inbox
                )

                # 3. stub publishes to agent-test → reaches pickup queue
                await stub_ep.send(
                    to="agent-test",
                    kind="TextMessage",
                    payload=TextMessagePayload(text="hello-agent"),
                )
                # If the FastMCP push notification path is wired, the agent
                # would be notified. Either way, list_pending should surface it.
                # Allow a short window for async dispatch to settle.
                for _ in range(40):
                    pending = (await client.call_tool("list_pending", {})).data["items"]
                    if pending:
                        break
                    await asyncio.sleep(0.05)
                pending = (await client.call_tool("list_pending", {})).data["items"]
                texts = [p["payload"]["text"] for p in pending]
                assert "hello-agent" in texts

                # Ack it
                env_id = next(p["id"] for p in pending if p["payload"]["text"] == "hello-agent")
                await client.call_tool("handle", {"envelope_id": env_id})

                pending2 = (await client.call_tool("list_pending", {})).data["items"]
                assert all(p["id"] != env_id for p in pending2)
        finally:
            await bus.stop()
    finally:
        await http_host.stop()
