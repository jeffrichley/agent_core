"""Register the four read-only bus-tail MCP tools on a FastMCP server.

Each tool wraps one PersistenceReader method, formats results as JSON,
and returns a single TextContent block. tail() returns summaries (with
value-free payload previews + state/delivery_count/last_attempted from
the row); get_envelope() and trace_correlation() return full envelope
shapes including payload and metadata.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from mcp.types import TextContent

from agent_core.bus.envelope import Envelope
from agent_core.bus.persistence import _row_to_envelope
from agent_core.bus_tail.summaries import summarize_payload

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

    from agent_core.bus_tail.reader import PersistenceReader


def _row_to_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Build an EnvelopeSummary dict from a raw envelope row.

    Includes state/delivery_count/last_attempted, which live on the row
    but not on the Envelope model.
    """
    env = _row_to_envelope(row)
    return {
        "id": env.id,
        "correlation_id": env.correlation_id,
        "in_reply_to": env.in_reply_to,
        "from": env.from_,
        "to": env.to,
        "kind": env.kind,
        "urgency": env.urgency,
        "state": row["state"],
        "created_at": env.created_at.isoformat(),
        "expires_at": env.expires_at.isoformat() if env.expires_at else None,
        "delivery_count": int(row["delivery_count"]),
        "last_attempted": row["last_attempted"],
        "payload_summary": summarize_payload(env.payload),
        "metadata_keys": sorted(env.metadata.keys()),
    }


def _envelope_to_full(env: Envelope) -> dict[str, Any]:
    """Build an EnvelopeFull dict from an Envelope (no state info)."""
    return {
        "id": env.id,
        "correlation_id": env.correlation_id,
        "in_reply_to": env.in_reply_to,
        "from": env.from_,
        "to": env.to,
        "kind": env.kind,
        "urgency": env.urgency,
        "created_at": env.created_at.isoformat(),
        "expires_at": env.expires_at.isoformat() if env.expires_at else None,
        "payload_summary": summarize_payload(env.payload),
        "metadata_keys": sorted(env.metadata.keys()),
        "payload": env.payload.model_dump(),
        "metadata": env.metadata,
    }


def register_bus_tail_tools(
    *, mcp: FastMCP, get_reader: Callable[[], PersistenceReader]
) -> None:
    """Register the four read-only tools.

    ``get_reader`` is a zero-arg callable returning the live
    ``PersistenceReader``. The endpoint passes a closure so tools resolve
    the reader at call time (not at registration time, which happens in
    __init__ before start()).
    """

    @mcp.tool(
        name="tail",
        description=(
            "Recent envelope listing with metadata + value-free payload "
            "summaries. Filterable by from/to/kind/urgency/state and bounded "
            "by since/before timestamps. Newest first. limit clamps to [1, 1000]."
        ),
    )
    async def _tail(
        limit: int = 50,
        since: str | None = None,
        before: str | None = None,
        from_endpoint: str | None = None,
        to_endpoint: str | None = None,
        kind: str | None = None,
        urgency: Literal["green", "yellow", "red"] | None = None,
        state: Literal["pending", "in_flight", "acked", "dead_letter", "expired"]
        | None = None,
    ) -> list[Any]:
        reader = get_reader()
        rows = await reader.tail_rows(
            limit=limit,
            since=datetime.fromisoformat(since) if since else None,
            before=datetime.fromisoformat(before) if before else None,
            from_endpoint=from_endpoint,
            to_endpoint=to_endpoint,
            kind=kind,
            urgency=urgency,
            state=state,
        )
        summaries = [_row_to_summary(r) for r in rows]
        return [TextContent(type="text", text=json.dumps(summaries, default=str))]

    @mcp.tool(
        name="get_envelope",
        description=(
            "Return one envelope's full payload + metadata by id. Returns null "
            "if the id is not found. Use this after tail() to drill into a "
            "specific envelope's contents."
        ),
    )
    async def _get_envelope(id: str) -> list[Any]:
        reader = get_reader()
        env = await reader.get_envelope(id)
        body = _envelope_to_full(env) if env is not None else None
        return [TextContent(type="text", text=json.dumps(body, default=str))]

    @mcp.tool(
        name="trace_correlation",
        description=(
            "Return all envelopes sharing a correlation_id, oldest first, with "
            "full payloads. Use this to follow a conversation chain (request "
            "→ reply → ack) across endpoints."
        ),
    )
    async def _trace_correlation(correlation_id: str) -> list[Any]:
        reader = get_reader()
        envs = await reader.list_by_correlation(correlation_id)
        chain = [_envelope_to_full(e) for e in envs]
        return [TextContent(type="text", text=json.dumps(chain, default=str))]

    @mcp.tool(
        name="metrics",
        description=(
            "Bus aggregates over the last 24h: counts by kind, counts by state, "
            "current queue depth per endpoint (pending only, all-time), and "
            "ack-latency percentiles (null below 10 acked samples)."
        ),
    )
    async def _metrics() -> list[Any]:
        reader = get_reader()
        snap = await reader.metrics_snapshot()
        return [TextContent(type="text", text=json.dumps(snap, default=str))]


__all__ = ["register_bus_tail_tools"]
