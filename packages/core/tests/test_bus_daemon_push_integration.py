"""End-to-end push delivery: real bus + real FastMCP HTTP host + real MCP client.

Spins up a `Bus` with a `ClaudeCodeMCPEndpoint` (mounted on the runner's
`HTTPHost`) plus a `StubEndpoint`. Connects a real `mcp.client.streamable_http`
client, performs the MCP `initialize` handshake (so `SessionRegistry`
captures the active session), then publishes an envelope from the stub
addressed to the agent. Asserts that a `notifications/claude/channel`
JSON-RPC notification arrives on the SSE stream within ~2s with the
expected summary shape.

This is the only test that exercises the full push pipeline through a
real ASGI host and a real MCP client. Together with the unit tests for
`SessionRegistry` (Task 5) and `_notify_mail_arrived` (Task 6), it pins
down the contract that delivery → debounce → channel notification →
client SSE actually works on the wire.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_push_notification_arrives_on_real_mcp_session(tmp_path):
    """Realistic flow: real bus, real FastMCP HTTP host, real mcp.client connection."""

    pytest.importorskip("uvicorn")
    streamable_client_mod = pytest.importorskip("mcp.client.streamable_http")
    pytest.importorskip("mcp.client.session")

    from mcp.client.session import ClientSession
    from pydantic import BaseModel

    from agent_core.bus.envelope import TextMessagePayload
    from agent_core.bus.runner import build_bus_from_config

    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent
    description: "agent under test"
    params:
      mount: /mcp/agent
  - class: agent_core.endpoints.stub.StubEndpoint
    name: stub
    description: "test publisher"
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None

    # Permissive notification model so the client doesn't drop the
    # custom `notifications/claude/channel` method during validation.
    # (The real `ServerNotification` is a discriminated union of known
    # methods; unknown methods get logged-and-discarded by ClientSession.)
    class _PermissiveServerNotification(BaseModel):
        method: str
        params: dict[str, Any] | None = None
        jsonrpc: str | None = None

        @property
        def root(self) -> _PermissiveServerNotification:
            # Match ServerNotification's `.root` accessor used elsewhere.
            return self

    captured_notifications: list[Any] = []

    class _CapturingClientSession(ClientSession):
        """ClientSession that funnels every server notification (including
        unknown methods) into `captured_notifications`."""

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            # Replace the strict ServerNotification union with a permissive
            # validator so `_received_notification` actually fires for our
            # custom method.
            self._receive_notification_type = _PermissiveServerNotification  # type: ignore[assignment]

        async def _received_notification(self, notification) -> None:  # type: ignore[override]
            captured_notifications.append(notification)
            await super()._received_notification(notification)

    await http_host.start()
    try:
        await bus.start()
        try:
            url = f"http://127.0.0.1:{http_host.port}/mcp/agent/"

            # Open a real Streamable HTTP MCP client and perform the
            # initialize handshake so the server's SessionRegistry
            # middleware captures the active ServerSession.
            async with streamable_client_mod.streamablehttp_client(url) as (
                read,
                write,
                _get_session_id,
            ):
                async with _CapturingClientSession(read, write) as session:
                    await session.initialize()

                    # Drive a tool call so the SessionRegistry middleware's
                    # spawned `_claim_session` coroutine actually gets a
                    # scheduling opportunity. (The same trick the unit test
                    # `test_session_active_flag_set_after_mcp_message` uses.)
                    await session.call_tool("list_pending", {})

                    # Wait briefly for SessionRegistry to claim the session.
                    agent_ep = bus._endpoints_by_name["agent"].endpoint
                    for _ in range(40):
                        if agent_ep._active_session is not None:
                            break
                        await asyncio.sleep(0.05)
                    assert agent_ep._active_session is not None, (
                        "SessionRegistry middleware did not capture the active session"
                    )

                    # Publish an envelope to the agent via the stub's BusHandle,
                    # so `from_` is stamped to "stub" by the bus.
                    stub_ep = bus._endpoints_by_name["stub"].endpoint
                    await stub_ep.send(
                        to="agent",
                        kind="TextMessage",
                        payload=TextMessagePayload(text="hello-push"),
                    )

                    # Wait up to 2s for the channel notification to land.
                    deadline = asyncio.get_event_loop().time() + 2.0
                    matched = None
                    while asyncio.get_event_loop().time() < deadline:
                        for n in captured_notifications:
                            method = getattr(n, "method", None)
                            if method == "notifications/claude/channel":
                                matched = n
                                break
                        if matched is not None:
                            break
                        await asyncio.sleep(0.05)

                    assert matched is not None, (
                        "no notifications/claude/channel arrived within 2s; "
                        f"saw methods: {[getattr(n, 'method', None) for n in captured_notifications]}"
                    )

                    params = matched.params or {}
                    assert int(params.get("meta", {}).get("count", "0")) >= 1
                    assert params["meta"]["endpoint"] == "agent"
                    # Sanity-check the summary shape Task 6 ships.
                    assert "urgency_max" in params["meta"]
                    assert "urgency_counts" in params["meta"]
                    assert "by_sender" in params["meta"]
                    assert all(isinstance(v, str) for v in params["meta"].values())
        finally:
            await bus.stop()
    finally:
        with contextlib.suppress(Exception):
            await http_host.stop()
