"""Translate backend-unreachable failures into a structured, retryable
tool result. Genuine backend tool errors pass through verbatim so the
agent never retry-loops on a real failure.

Two backend-down shapes (spec Amendment 2026-05-16):
  1. Transport error — warm tool cache, per-request connect to the dead
     daemon fails. Unambiguously transient.
  2. NotFoundError("Unknown tool: ...") — the tool-list fetch failed and
     FastMCP's ProxyProvider/AggregateProvider swallowed it into an empty
     registry. AMBIGUOUS: identical shape to a genuine unknown-tool / tool
     exception from a HEALTHY daemon. Disambiguated with a fast TCP
     liveness probe to the daemon: unreachable => transient; reachable =>
     genuine, re-raised unchanged.

     NOTE: fastmcp raises `NotFoundError` server-side from
     `FastMCP.call_tool` when the resolved tool list is empty (backend-down
     → swallowed list → empty registry). ToolError is also classified
     AMBIGUOUS for forward-compatibility with versions that may change this
     behaviour.

Unknown exception types are treated as genuine — a real bug is never
masked as retryable.
"""

from __future__ import annotations

import asyncio
import logging
import re
from enum import Enum
from typing import Any, cast
from urllib.parse import urlparse

import httpx
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult
from mcp.shared.exceptions import McpError

log = logging.getLogger(__name__)

TRANSIENT_ERROR_CODE = "bus_unavailable"
_RETRY_AFTER_SECONDS = 5
_PROBE_TIMEOUT_SECONDS = 2.0

# Reuse the #76 redaction shape: drop the entire query string (signed CDN
# tokens / session ids live there).
_URL_QS_RE = re.compile(r"(https?://[^\s?]+)\?\S*")


def redact(text: str) -> str:
    """Strip query strings from any URLs in a human string."""
    return _URL_QS_RE.sub(r"\1?<redacted>", text)


class Disposition(Enum):
    """How a backend exception should be handled."""

    TRANSIENT = "transient"  # definitely daemon unreachable
    GENUINE = "genuine"  # definitely a real error — re-raise
    AMBIGUOUS = "ambiguous"  # could be either — probe the daemon to decide


# Transport-class: could not reach / handshake the daemon. Definitely
# transient. (NotFoundError/ToolError are NOT in here — ambiguous, handled below.)
_TRANSIENT_TYPES: tuple[type[BaseException], ...] = (
    httpx.HTTPError,
    ConnectionError,
    TimeoutError,
    OSError,
    McpError,
)


def classify_backend_error(exc: BaseException) -> Disposition:
    """Triage a backend exception (see module docstring)."""
    if isinstance(exc, _TRANSIENT_TYPES):
        return Disposition.TRANSIENT
    # NotFoundError: fastmcp raises this (not ToolError) from FastMCP.call_tool
    # when the resolved tool list is empty (backend-down → swallowed list →
    # empty registry). ToolError: kept for forward-compatibility.
    if isinstance(exc, (NotFoundError, ToolError)):
        return Disposition.AMBIGUOUS
    return Disposition.GENUINE


async def daemon_reachable(daemon_url: str) -> bool:
    """Fast TCP liveness probe — can we open a socket to the daemon?

    A bounced/down daemon has nothing listening on its port, so a refused
    or timed-out connect means 'down'. Bounded by _PROBE_TIMEOUT_SECONDS
    so it cannot itself hang a tool call.
    """
    parsed = urlparse(daemon_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    try:
        fut = asyncio.open_connection(host, port)
        _reader, writer = await asyncio.wait_for(
            fut, timeout=_PROBE_TIMEOUT_SECONDS
        )
    except (TimeoutError, OSError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # close best-effort
        pass
    return True


def _transient_result(exc: BaseException) -> ToolResult:
    detail = redact(f"{type(exc).__name__}: {exc}")
    log.warning("busproxy: backend unavailable (transient): %s", detail)
    return ToolResult(
        structured_content={
            "error": TRANSIENT_ERROR_CODE,
            "transient": True,
            "retry_after_seconds": _RETRY_AFTER_SECONDS,
            "detail": detail,
        }
    )


class TransientErrorMiddleware(Middleware):
    """Map daemon-unreachable to a retryable result; pass real errors through.

    ``daemon_url`` is the liveness-probe target. ``None`` (the in-process
    test path) disables probing — an AMBIGUOUS error is then treated as
    genuine so a real error is never masked when we cannot verify.
    """

    def __init__(self, daemon_url: str | None = None) -> None:
        self._daemon_url = daemon_url

    async def on_call_tool(self, context: Any, call_next: Any) -> ToolResult:
        try:
            return cast(ToolResult, await call_next(context))
        except BaseException as exc:  # triage then re-raise (never swallow)
            disposition = classify_backend_error(exc)
            if disposition is Disposition.TRANSIENT:
                return _transient_result(exc)
            if disposition is Disposition.AMBIGUOUS and (
                self._daemon_url is not None
                and not await daemon_reachable(self._daemon_url)
            ):
                return _transient_result(exc)
            raise
