"""FastMCP middleware that emits one ``AuditLine`` per ``tools/call``.

Attached to each ``ClaudeCodeMCPEndpoint._mcp`` server (post-construction
via ``attach_audit_writer``). Uses FastMCP's ``on_call_tool`` hook so it
sees only tool invocations - list/read/get traffic does not generate
audit lines.

Defaults
--------
``args_summary`` is structural-only: ``{"arg_keys": sorted(keys), "arg_count": n}``.
No argument values are ever written. Per-tool bespoke summarizers are
out of scope for v1; see the spec's "Out of scope" section.

Error handling
--------------
* Tool exceptions are caught for accounting only, then re-raised. The
  audit line is written in ``finally`` either way.
* Audit-writer failures are already swallowed inside
  ``MCPAuditWriter.write``; this middleware never breaks a tool call.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

from agent_core.mcp_audit.writer import AuditLine, MCPAuditWriter

log = logging.getLogger(__name__)


class MCPAuditMiddleware(Middleware):
    """Emit one ``AuditLine`` per ``tools/call`` for the host endpoint."""

    def __init__(
        self,
        *,
        endpoint_name: str,
        writer: MCPAuditWriter,
        skip_tools: frozenset[str] | set[str] | None = None,
    ) -> None:
        self._endpoint_name = endpoint_name
        self._writer = writer
        self._skip_tools: frozenset[str] = (
            frozenset(skip_tools) if skip_tools else frozenset()
        )

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool = getattr(context.message, "name", "<unknown>")
        if tool in self._skip_tools:
            return await call_next(context)

        args = getattr(context.message, "arguments", None) or {}
        session_id, request_id = self._extract_ids(context)

        start = perf_counter()
        result_status: str = "ok"
        error: dict[str, str] | None = None
        try:
            return await call_next(context)
        except Exception as exc:
            result_status = "error"
            error = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            duration_ms = int((perf_counter() - start) * 1000)
            line = AuditLine(
                timestamp=datetime.now(UTC).astimezone(ZoneInfo(self._writer.timezone)),
                endpoint=self._endpoint_name,
                session_id=session_id,
                request_id=request_id,
                tool=tool,
                args_summary=self._summarize_args(args),
                duration_ms=duration_ms,
                result=result_status,
                error=error,
            )
            try:
                await self._writer.write(line)
            except Exception:
                # Defense in depth - writer.write already swallows, but
                # don't let any future change to the writer break a tool call.
                log.warning("mcp_audit: middleware swallowed writer error", exc_info=True)

    @staticmethod
    def _summarize_args(args: dict[str, Any]) -> dict[str, Any]:
        return {"arg_keys": sorted(args.keys()), "arg_count": len(args)}

    @staticmethod
    def _extract_ids(
        context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> tuple[str | None, str | None]:
        ctx = context.fastmcp_context
        if ctx is None:
            return (None, None)
        session_id_raw = getattr(ctx, "session_id", None)
        session_id = str(session_id_raw) if session_id_raw is not None else None
        request_id: str | None = None
        rc = getattr(ctx, "request_context", None)
        if rc is not None:
            rid = getattr(rc, "request_id", None)
            if rid is not None:
                request_id = str(rid)
        return (session_id, request_id)


__all__ = ["MCPAuditMiddleware"]
