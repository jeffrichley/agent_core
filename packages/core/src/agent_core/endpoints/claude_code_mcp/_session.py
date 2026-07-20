"""Session-registry middleware for ClaudeCodeMCPEndpoint.

Captures the active FastMCP ServerSession on the first MCP message,
enabling server-push of notifications to the connected agent.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import anyio
from fastmcp.server.middleware import Middleware, MiddlewareContext

if TYPE_CHECKING:
    from ._endpoint import ClaudeCodeMCPEndpoint

log = logging.getLogger(__name__)


class SessionRegistry(Middleware):
    """Middleware that captures the connected ServerSession on first message.

    Mirrors FastMCP's official PingMiddleware pattern: spawn a long-lived
    coroutine into session._subscription_task_group; the coroutine registers
    the session with the endpoint, awaits forever, and runs cleanup in
    finally: when the session task group is cancelled (which fires when the
    SSE stream closes).

    Dedup key is `mcp-session-id` (string), not `id(session)`. The header is
    the spec-defined stable identifier across HTTP requests in a streamable-
    HTTP session; `id(session)` happens to also be stable in stateful mode
    but isn't load-bearing — and using the string makes us robust to clients
    that genuinely open new logical sessions.
    """

    def __init__(self, endpoint: ClaudeCodeMCPEndpoint) -> None:
        self._endpoint = endpoint
        self._spawned_for: set[str] = set()
        self._lock = anyio.Lock()

    async def on_message(self, context: MiddlewareContext, call_next) -> Any:
        if context.fastmcp_context is None or context.fastmcp_context.request_context is None:
            return await call_next(context)

        ctx = context.fastmcp_context
        session = ctx.session
        # `session_id` is the mcp-session-id header (stable across the SSE
        # stream's lifetime in stateful mode). Fall back to `id(session)` for
        # in-memory transports that don't have a session id.
        sid = getattr(ctx, "session_id", None) or f"obj:{id(session)}"
        log.debug(
            "endpoint '%s': on_message session_id=%s id(session)=%d",
            self._endpoint.name,
            sid,
            id(session),
        )

        async with self._lock:
            if sid not in self._spawned_for:
                tg = getattr(session, "_subscription_task_group", None)
                if tg is not None:
                    self._spawned_for.add(sid)
                    tg.start_soon(self._claim_session, session, sid)

        return await call_next(context)

    async def _claim_session(self, session: Any, sid: str) -> None:
        try:
            self._endpoint._register_session(session)
            await anyio.sleep_forever()
        finally:
            self._endpoint._unregister_session(session)
            self._spawned_for.discard(sid)
