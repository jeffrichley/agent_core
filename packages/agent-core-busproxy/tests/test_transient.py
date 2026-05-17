"""TransientErrorMiddleware: backend-down -> structured retryable result;
genuine tool errors pass through verbatim.

Per spec Amendment 2026-05-16: a backend-down failure can surface either
as a transport error (warm cache, per-request connect fails) OR as a
ToolError("Unknown tool: ...") (empty registry because the list fetch
was swallowed). The latter is AMBIGUOUS with a genuine unknown-tool /
tool exception, so it is disambiguated with a fast daemon liveness probe.
"""

from __future__ import annotations

import socket

import httpx
import pytest
from agent_core_busproxy.transient import (
    TRANSIENT_ERROR_CODE,
    Disposition,
    TransientErrorMiddleware,
    classify_backend_error,
    daemon_reachable,
    redact,
)
from fastmcp.exceptions import NotFoundError, ToolError


def test_redact_strips_query_string() -> None:
    # Same discipline as the #76 signed-CDN redaction.
    assert redact("connect to https://cdn.example.com/x?sig=SECRET&t=9") == (
        "connect to https://cdn.example.com/x?<redacted>"
    )


def test_classify_transport_error_is_transient() -> None:
    assert classify_backend_error(httpx.ConnectError("refused")) is Disposition.TRANSIENT
    assert classify_backend_error(ConnectionError("refused")) is Disposition.TRANSIENT
    assert classify_backend_error(TimeoutError()) is Disposition.TRANSIENT


def test_classify_tool_error_is_ambiguous() -> None:
    # Could be 'down -> empty registry' OR a real unknown-tool error.
    assert classify_backend_error(ToolError("Unknown tool: x")) is Disposition.AMBIGUOUS


def test_classify_unknown_is_genuine() -> None:
    # Never mask an unrecognized error as retryable.
    assert classify_backend_error(ValueError("nope")) is Disposition.GENUINE


@pytest.mark.asyncio
async def test_daemon_reachable_true_and_false() -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    host, port = srv.getsockname()
    try:
        assert await daemon_reachable(f"http://{host}:{port}") is True
    finally:
        srv.close()
    # Nothing listening on 65535 => not reachable.
    assert await daemon_reachable("http://127.0.0.1:65535") is False


@pytest.mark.asyncio
async def test_transport_error_becomes_transient_result() -> None:
    mw = TransientErrorMiddleware(daemon_url="http://127.0.0.1:65535")

    async def call_next(_ctx):
        raise httpx.ConnectError("Connection refused to http://h/x?token=ABC")

    result = await mw.on_call_tool(object(), call_next)

    assert result.structured_content["error"] == TRANSIENT_ERROR_CODE
    assert result.structured_content["transient"] is True
    assert isinstance(result.structured_content["retry_after_seconds"], int)
    assert "token=ABC" not in result.structured_content["detail"]
    assert "<redacted>" in result.structured_content["detail"]


@pytest.mark.asyncio
async def test_toolerror_with_daemon_down_becomes_transient() -> None:
    # 65535 unbound => probe says down => AMBIGUOUS resolves to transient.
    mw = TransientErrorMiddleware(daemon_url="http://127.0.0.1:65535")

    async def call_next(_ctx):
        raise ToolError("Unknown tool: 'consume'")

    result = await mw.on_call_tool(object(), call_next)
    assert result.structured_content["transient"] is True


@pytest.mark.asyncio
async def test_toolerror_with_daemon_up_passes_through() -> None:
    # A real listening socket => probe says reachable => genuine passthrough.
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    host, port = srv.getsockname()
    try:
        mw = TransientErrorMiddleware(daemon_url=f"http://{host}:{port}")

        async def call_next(_ctx):
            raise ToolError("envelope_id not found")

        with pytest.raises(ToolError, match="envelope_id not found"):
            await mw.on_call_tool(object(), call_next)
    finally:
        srv.close()


@pytest.mark.asyncio
async def test_no_daemon_url_treats_ambiguous_as_genuine() -> None:
    # In-process/test path (no URL): cannot probe => do not mask as transient.
    mw = TransientErrorMiddleware(daemon_url=None)

    async def call_next(_ctx):
        raise ToolError("envelope_id not found")

    with pytest.raises(ToolError, match="envelope_id not found"):
        await mw.on_call_tool(object(), call_next)


def test_classify_not_found_error_is_ambiguous() -> None:
    # fastmcp 3.2.4 raises NotFoundError (NOT ToolError) server-side for an
    # empty-registry unknown tool — the #91 backend-down path the proxy's
    # middleware actually sees. Must be AMBIGUOUS so the liveness probe can
    # disambiguate down-daemon vs a genuinely missing tool.
    assert (
        classify_backend_error(NotFoundError("Unknown tool: 'x'"))
        is Disposition.AMBIGUOUS
    )


@pytest.mark.asyncio
async def test_not_found_error_with_daemon_down_becomes_transient() -> None:
    mw = TransientErrorMiddleware(daemon_url="http://127.0.0.1:65535")

    async def call_next(_ctx):
        raise NotFoundError("Unknown tool: 'consume'")

    result = await mw.on_call_tool(object(), call_next)
    assert result.structured_content["transient"] is True


@pytest.mark.asyncio
async def test_not_found_error_with_daemon_up_passes_through() -> None:
    # The no-masking guarantee for the exact exception that motivated the
    # Task 5 deviation: a genuinely-missing tool on a HEALTHY daemon must
    # re-raise, never be masked as a retryable transient result.
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    host, port = srv.getsockname()
    try:
        mw = TransientErrorMiddleware(daemon_url=f"http://{host}:{port}")

        async def call_next(_ctx):
            raise NotFoundError("Unknown tool: 'genuinely_missing'")

        with pytest.raises(NotFoundError, match="genuinely_missing"):
            await mw.on_call_tool(object(), call_next)
    finally:
        srv.close()
