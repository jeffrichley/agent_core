"""Tests for the shared Starlette+Uvicorn HTTP host."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from agent_core.bus.http_host import HTTPHost, MCPHostable


class _StubMountable:
    """Minimal MCPHostable for tests — owns a small ASGI app."""

    def __init__(self, mount: str, body: bytes = b"hello"):
        self.mount = mount
        self._body = body

    def asgi_app(self):
        body = self._body

        async def app(scope, receive, send):
            if scope["type"] == "lifespan":
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            else:
                assert scope["type"] == "http"
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": body})

        return app


def test_stub_satisfies_mcp_hostable_protocol():
    s = _StubMountable("/mcp/x")
    assert isinstance(s, MCPHostable)


@pytest.mark.asyncio
async def test_http_host_serves_mounted_apps():
    host = HTTPHost(bind_host="127.0.0.1", bind_port=0)
    host.mount(_StubMountable("/mcp/foo", body=b"foo-resp"))
    host.mount(_StubMountable("/mcp/bar", body=b"bar-resp"))
    await host.start()
    try:
        port = host.port
        assert port > 0
        async with httpx.AsyncClient() as client:
            r1 = await client.get(f"http://127.0.0.1:{port}/mcp/foo")
            r2 = await client.get(f"http://127.0.0.1:{port}/mcp/bar")
        assert r1.status_code == 200 and r1.content == b"foo-resp"
        assert r2.status_code == 200 and r2.content == b"bar-resp"
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_http_host_refuses_double_start():
    host = HTTPHost(bind_host="127.0.0.1", bind_port=0)
    host.mount(_StubMountable("/mcp/x"))
    await host.start()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            await host.start()
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_http_host_stop_is_idempotent():
    host = HTTPHost(bind_host="127.0.0.1", bind_port=0)
    host.mount(_StubMountable("/mcp/x"))
    await host.start()
    await host.stop()
    await host.stop()  # Should not raise.


@pytest.mark.asyncio
async def test_http_host_port_already_in_use_raises():
    h1 = HTTPHost(bind_host="127.0.0.1", bind_port=0)
    h1.mount(_StubMountable("/mcp/x"))
    await h1.start()
    try:
        h2 = HTTPHost(bind_host="127.0.0.1", bind_port=h1.port)
        h2.mount(_StubMountable("/mcp/x"))
        with pytest.raises(OSError):
            await h2.start()
    finally:
        await h1.stop()


@pytest.mark.asyncio
async def test_http_host_propagates_lifespan_to_mounts():
    """Mounted ASGI apps must receive lifespan.startup and lifespan.shutdown."""
    started = asyncio.Event()
    stopped = asyncio.Event()

    class _LifespanAwareMount:
        mount = "/mcp/lifespan"

        def asgi_app(self):
            async def app(scope, receive, send):
                if scope["type"] == "lifespan":
                    while True:
                        message = await receive()
                        if message["type"] == "lifespan.startup":
                            started.set()
                            await send({"type": "lifespan.startup.complete"})
                        elif message["type"] == "lifespan.shutdown":
                            stopped.set()
                            await send({"type": "lifespan.shutdown.complete"})
                            return
                elif scope["type"] == "http":
                    await send({"type": "http.response.start", "status": 200, "headers": []})
                    await send({"type": "http.response.body", "body": b"ok"})

            return app

    host = HTTPHost(bind_host="127.0.0.1", bind_port=0)
    host.mount(_LifespanAwareMount())
    await host.start()
    assert started.is_set(), "mounted app did not receive lifespan.startup"
    await host.stop()
    # Allow the lifespan shutdown event to propagate.
    for _ in range(40):
        if stopped.is_set():
            break
        await asyncio.sleep(0.05)
    assert stopped.is_set(), "mounted app did not receive lifespan.shutdown"
