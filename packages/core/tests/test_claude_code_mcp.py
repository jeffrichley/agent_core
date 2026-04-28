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


# ---------------------------------------------------------------------------
# Task 5 — inbound surface: list_pending, ack, nack, handle, deliver()
# ---------------------------------------------------------------------------


class _RecordingHandleWithPending(_RecordingHandle):
    def __init__(self, pending: list[Envelope]):
        super().__init__()
        self.pending = pending
        self.acked: list[str] = []
        self.nacked: list[tuple[str, bool]] = []

    async def ack(self, envelope_id: str) -> None:
        self.acked.append(envelope_id)

    async def nack(self, envelope_id: str, requeue: bool = True) -> None:
        self.nacked.append((envelope_id, requeue))


def _make_envelope(env_id: str, frm: str = "stub", to: str = "agent-test") -> Envelope:
    return Envelope(
        id=env_id,
        correlation_id=uuid.uuid4().hex,
        from_=frm,
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text="hello"),
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_list_pending_returns_queued_envelopes():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    env = _make_envelope("env-1")
    handle = _RecordingHandleWithPending(pending=[])
    await ep.start(handle)
    try:
        # Endpoint queues envelopes via deliver() when no session is connected.
        # Force a queued envelope by calling _queue (test seam) or by raising
        # EndpointUnavailable from deliver(). For this test, push directly into
        # the endpoint's pending list via its public API.
        ep.queue_for_pickup(env)

        async with Client(ep._mcp) as client:
            res = await client.call_tool("list_pending", {})
        assert len(res.data) == 1
        assert res.data[0]["id"] == "env-1"
        assert res.data[0]["from"] == "stub"
        assert res.data[0]["kind"] == "TextMessage"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_ack_tool_calls_handle_ack():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandleWithPending(pending=[])
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            await client.call_tool("ack", {"envelope_id": "env-9"})
        assert handle.acked == ["env-9"]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_nack_tool_passes_requeue_flag():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandleWithPending(pending=[])
    await ep.start(handle)
    try:
        async with Client(ep._mcp) as client:
            await client.call_tool("nack", {"envelope_id": "env-3", "requeue": False})
        assert handle.nacked == [("env-3", False)]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_handle_tool_acks_and_removes_from_pending():
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    env = _make_envelope("env-h")
    handle = _RecordingHandleWithPending(pending=[])
    await ep.start(handle)
    try:
        ep.queue_for_pickup(env)
        async with Client(ep._mcp) as client:
            await client.call_tool("handle", {"envelope_id": "env-h"})
        # handle is a convenience for ack — verify the ack happened.
        assert handle.acked == ["env-h"]
        # And the pending entry is gone.
        async with Client(ep._mcp) as client:
            res = await client.call_tool("list_pending", {})
        assert res.data == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_without_session_raises_endpoint_unavailable_and_queues():
    from agent_core.bus.protocol import EndpointUnavailable

    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandleWithPending(pending=[])
    await ep.start(handle)
    try:
        env = _make_envelope("env-q")
        with pytest.raises(EndpointUnavailable):
            await ep.deliver(env)
        # When the agent reconnects, list_pending must surface this envelope
        # (or it must be redelivered by the bus). The endpoint queues it
        # internally so a freshly-attached client can pick it up.
        async with Client(ep._mcp) as client:
            res = await client.call_tool("list_pending", {})
        ids = {item["id"] for item in res.data}
        assert "env-q" in ids
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_session_active_flag_set_after_mcp_initialize():
    """_SessionTracker middleware sets _session_active=True on MCP initialize.

    The in-memory Client fires the initialize request just like a real HTTP
    session, so this test exercises the session-lifecycle hook without needing
    a live HTTP server."""
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    handle = _RecordingHandleWithPending(pending=[])
    await ep.start(handle)
    try:
        assert ep._session_active is False
        async with Client(ep._mcp) as client:
            # After entering the context manager, initialize has fired.
            assert ep._session_active is True
            # deliver() must NOT raise while session is active.
            env = _make_envelope("env-active")
            await ep.deliver(env)
            # Envelope should be in pending (no EU raised).
            res = await client.call_tool("list_pending", {})
            ids = {item["id"] for item in res.data}
            assert "env-active" in ids
    finally:
        await ep.stop()
        assert ep._session_active is False
