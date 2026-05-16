"""Assemble the stdio bus proxy.

The proxy forwards the daemon's per-agent MCP tool surface. Each tool
call mints a FRESH backend session (ProxyClient.new()), so a restarted
daemon is never presented a stale mcp-session-id — issue #91 is removed
by construction, not by recovery logic.

Cache hardening (spec Amendment 2026-05-16): FastMCP's ProxyProvider
caches the backend tool list and swallows a backend-down list into an
EMPTY registry. We build the proxy via the documented lower-level
pattern — FastMCP + add_provider(ProxyProvider(client_factory,
cache_ttl=<long>)) — with a 24h cache_ttl so the tool palette survives
any realistic daemon bounce. (FastMCPProxy hardcodes ProxyProvider with
no cache_ttl passthrough, so it cannot be used here.) No active
keepalive: a keepalive cannot help the cold-start-while-down edge, and a
long TTL covers expiry (YAGNI).

`init_timeout` keeps a down/bouncing daemon from hanging the call: the
backend connect fails fast and the TransientErrorMiddleware (attached in
Task 5) turns that into a structured retryable tool result.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider

from agent_core_busproxy.transient import TransientErrorMiddleware

# Fail-fast: a down daemon must not hang a tool call. Small connect/init
# budget; the agent owns the retry decision (spec: fail-fast retryable).
_BACKEND_INIT_TIMEOUT_SECONDS = 5.0
# Per-call request budget once connected (a healthy daemon answers in ms;
# this only bounds a half-open connection).
_BACKEND_REQUEST_TIMEOUT_SECONDS = 60.0
# Tool-list cache lifetime. Long enough that the palette survives any
# realistic daemon bounce/refresh within a session (spec Amendment).
_TOOL_CACHE_TTL_SECONDS = 86400.0


def build_busproxy(
    *,
    agent: str,
    daemon_url: str | None,
    _backend: Any | None = None,
) -> FastMCP:
    """Return a FastMCP proxy over the daemon's per-agent endpoint.

    Args:
        agent: bus agent name (URL path segment).
        daemon_url: e.g. ``http://127.0.0.1:8789``. Ignored when
            ``_backend`` is supplied.
        _backend: test seam — an in-process FastMCP server to proxy
            instead of an HTTP URL. Production always passes a URL.
    """
    if _backend is not None:
        base_client = ProxyClient(_backend)
    else:
        if not daemon_url:
            raise ValueError("daemon_url is required when no _backend is given")
        url = f"{daemon_url.rstrip('/')}/mcp/{agent}"
        base_client = ProxyClient(
            url,
            init_timeout=_BACKEND_INIT_TIMEOUT_SECONDS,
            timeout=_BACKEND_REQUEST_TIMEOUT_SECONDS,
        )

    # -> Any: ProxyClient.new() returns an httpx-backed client (URL path)
    # or an in-process FastMCP client (_backend path) — undifferentiated.
    def client_factory() -> Any:
        # Fresh session per request — the #91 fix.
        return base_client.new()

    proxy = FastMCP(name=f"agent-core[{agent}]")
    proxy.add_provider(
        ProxyProvider(client_factory, cache_ttl=_TOOL_CACHE_TTL_SECONDS)
    )
    # daemon_url is the middleware's liveness-probe target. None on the
    # in-process test path => probing disabled (ambiguous => genuine).
    proxy.add_middleware(TransientErrorMiddleware(daemon_url=daemon_url))
    return proxy
