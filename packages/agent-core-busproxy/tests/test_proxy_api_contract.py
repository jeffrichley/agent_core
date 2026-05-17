"""Pin the installed FastMCP proxy API surface this package depends on.

If FastMCP changes these, this test fails first and points at exactly
what to update in proxy.py / transient.py.
"""

from __future__ import annotations

import inspect

import pytest


def test_fastmcpproxy_and_proxyclient_importable() -> None:
    from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient

    # FastMCPProxy takes a client_factory kwarg.
    sig = inspect.signature(FastMCPProxy.__init__)
    assert "client_factory" in sig.parameters

    # ProxyClient accepts a URL string target and supports .new().
    pc = ProxyClient("http://127.0.0.1:65535/mcp/nobody")
    assert hasattr(pc, "new")


def test_client_supports_init_timeout() -> None:
    from fastmcp import Client

    sig = inspect.signature(Client.__init__)
    assert "init_timeout" in sig.parameters


def test_middleware_has_on_call_tool() -> None:
    from fastmcp.server.middleware import Middleware

    assert hasattr(Middleware, "on_call_tool")


def test_toolresult_accepts_structured_content() -> None:
    from fastmcp.tools.base import ToolResult

    r = ToolResult(structured_content={"error": "x", "transient": True})
    assert r.structured_content == {"error": "x", "transient": True}


@pytest.mark.asyncio
async def test_client_surfaces_structured_content_attribute() -> None:
    """Pin the CLIENT-side accessor: a tool returning ToolResult(
    structured_content=...) is readable as result.structured_content.
    Every later test asserts on this attribute.

    NOTE: The inner tool function deliberately omits a return-type annotation.
    Using ``-> ToolResult`` with ``from __future__ import annotations`` (PEP 563)
    causes pydantic to evaluate it as a forward-reference against the *module*
    globalns, where the name ``ToolResult`` is absent (the import lives only in
    the test-function locals).  Omitting the annotation is equivalent for the
    purpose of this characterization test.
    """
    from fastmcp import Client, FastMCP
    from fastmcp.tools.base import ToolResult

    srv = FastMCP("pin")

    @srv.tool()
    async def t():  # no return annotation — see docstring
        return ToolResult(structured_content={"transient": True})

    async with Client(srv) as c:
        res = await c.call_tool("t", {})

    assert res.structured_content == {"transient": True}


@pytest.mark.asyncio
async def test_dead_backend_raises_on_tool_call() -> None:
    """Calling a tool through a proxy whose backend is unreachable raises.

    Records the concrete exception type so transient.py can catch it.
    Port 65535 with nothing listening => connect failure.
    """
    from fastmcp import Client
    from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient

    base = ProxyClient("http://127.0.0.1:65535/mcp/nobody", init_timeout=2.0)
    proxy = FastMCPProxy(client_factory=lambda: base.new(), name="probe")

    with pytest.raises(BaseException) as excinfo:  # broad: characterizing
        async with Client(proxy) as c:
            await c.call_tool("list_endpoints", {})

    # Concrete type pinned for transient.classify_backend_error.
    # Assert it is NOT a clean MCP tool-result (i.e. the failure surfaces
    # as an exception, which is what the middleware will intercept).
    assert excinfo.value is not None
    chain = []
    e: BaseException | None = excinfo.value
    while e is not None:
        chain.append(type(e).__name__)
        e = e.__cause__ or e.__context__
    # Connection-class failure somewhere in the chain.
    #
    # OBSERVED BEHAVIOUR (fastmcp 3.2.2): AggregateProvider._collect_list_results
    # absorbs the actual connect error during list_tools() (logs a WARNING) and
    # returns an empty tool list.  A subsequent call_tool() therefore fails with
    # ToolError("Unknown tool: '...'") — never a raw connection exception.
    # transient.classify_backend_error must therefore match "ToolError" (in
    # addition to raw transport errors that could surface in other code-paths).
    assert any(
        n in chain
        for n in (
            "ToolError",
            "ConnectError",
            "ConnectionError",
            "ConnectTimeout",
            "McpError",
            "HTTPError",
            "OSError",
            "TimeoutError",
        )
    ), f"unexpected exception chain: {chain}"
