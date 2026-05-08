"""Read-only query layer over the bus's existing Persistence.

The reader wraps a Persistence handle and adds tail-specific queries
(broad filters, aggregations) without polluting the bus's hot-path API.
No second connection, no parallel cache, no writes — just SELECT/COUNT
queries against the existing envelopes table.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import aiosqlite

from agent_core.bus.envelope import Envelope
from agent_core.bus.persistence import Persistence, _row_to_envelope

DEFAULT_LIMIT = 50
MAX_LIMIT = 1000
MIN_LIMIT = 1
ACK_LATENCY_MIN_SAMPLES = 10
DEFAULT_METRICS_WINDOW_HOURS = 24


class PersistenceReader:
    """Read-only query layer for the audit/tail surface."""

    def __init__(self, persistence: Persistence) -> None:
        self._persistence = persistence

    def _conn(self) -> aiosqlite.Connection:
        # Reuse the persistence's existing connection. Assumes connect() ran.
        return self._persistence._require_conn()

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        return max(MIN_LIMIT, min(MAX_LIMIT, int(limit)))

    async def tail(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        since: datetime | None = None,
        before: datetime | None = None,
        from_endpoint: str | None = None,
        to_endpoint: str | None = None,
        kind: str | None = None,
        urgency: Literal["green", "yellow", "red"] | None = None,
        state: Literal["pending", "in_flight", "acked", "dead_letter", "expired"]
        | None = None,
    ) -> list[Envelope]:
        rows = await self.tail_rows(
            limit=limit,
            since=since,
            before=before,
            from_endpoint=from_endpoint,
            to_endpoint=to_endpoint,
            kind=kind,
            urgency=urgency,
            state=state,
        )
        return [_row_to_envelope(r) for r in rows]

    async def tail_rows(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        since: datetime | None = None,
        before: datetime | None = None,
        from_endpoint: str | None = None,
        to_endpoint: str | None = None,
        kind: str | None = None,
        urgency: Literal["green", "yellow", "red"] | None = None,
        state: Literal["pending", "in_flight", "acked", "dead_letter", "expired"]
        | None = None,
    ) -> list[dict[str, Any]]:
        """Same filter shape as ``tail()`` but returns raw row dicts.

        Carries state/delivery_count/last_attempted, which the Envelope
        model does not expose. Used by the MCP ``tail`` tool to build
        EnvelopeSummary dicts that match the design spec.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since.isoformat())
        if before is not None:
            clauses.append("created_at < ?")
            params.append(before.isoformat())
        if from_endpoint is not None:
            clauses.append("from_endpoint = ?")
            params.append(from_endpoint)
        if to_endpoint is not None:
            clauses.append("to_endpoint = ?")
            params.append(to_endpoint)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if urgency is not None:
            clauses.append("urgency = ?")
            params.append(urgency)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # No user-controlled SQL here — `where` is built from fixed clause strings
        # and all values are bound via parameters.
        sql = (
            f"SELECT * FROM envelopes {where} "
            "ORDER BY created_at DESC LIMIT ?"
        )
        params.append(self._clamp_limit(limit))

        conn = self._conn()
        conn.row_factory = aiosqlite.Row
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_envelope(self, id_: str) -> Envelope | None:
        return await self._persistence.get(id_)

    async def list_by_correlation(self, correlation_id: str) -> list[Envelope]:
        return await self._persistence.list_by_correlation(correlation_id)

    async def metrics_snapshot(
        self, *, window_hours: int = DEFAULT_METRICS_WINDOW_HOURS
    ) -> dict[str, Any]:
        cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
        cutoff_iso = cutoff.isoformat()
        conn = self._conn()
        conn.row_factory = aiosqlite.Row

        # total_envelopes
        async with conn.execute(
            "SELECT COUNT(*) AS c FROM envelopes WHERE created_at >= ?", (cutoff_iso,)
        ) as cur:
            total_row = await cur.fetchone()
        total_envelopes = int(total_row["c"]) if total_row else 0

        # counts_by_kind
        async with conn.execute(
            "SELECT kind, COUNT(*) AS c FROM envelopes WHERE created_at >= ? GROUP BY kind",
            (cutoff_iso,),
        ) as cur:
            kind_rows = await cur.fetchall()
        counts_by_kind = {r["kind"]: int(r["c"]) for r in kind_rows}

        # counts_by_state
        async with conn.execute(
            "SELECT state, COUNT(*) AS c FROM envelopes WHERE created_at >= ? GROUP BY state",
            (cutoff_iso,),
        ) as cur:
            state_rows = await cur.fetchall()
        counts_by_state = {r["state"]: int(r["c"]) for r in state_rows}

        # queue_depth_by_endpoint (pending only, all-time — pending isn't time-bounded)
        async with conn.execute(
            "SELECT to_endpoint, COUNT(*) AS c FROM envelopes "
            "WHERE state = 'pending' GROUP BY to_endpoint"
        ) as cur:
            depth_rows = await cur.fetchall()
        queue_depth_by_endpoint = {r["to_endpoint"]: int(r["c"]) for r in depth_rows}

        # ack_latency: only count acked envelopes whose created_at is in window
        # AND last_attempted is non-null. Latency = (last_attempted - created_at) ms.
        async with conn.execute(
            "SELECT created_at, last_attempted FROM envelopes "
            "WHERE state = 'acked' AND last_attempted IS NOT NULL AND created_at >= ?",
            (cutoff_iso,),
        ) as cur:
            ack_rows = await cur.fetchall()

        latencies_ms: list[float] = []
        for r in ack_rows:
            try:
                created = datetime.fromisoformat(r["created_at"])
                attempted = datetime.fromisoformat(r["last_attempted"])
            except (TypeError, ValueError):
                continue
            latencies_ms.append((attempted - created).total_seconds() * 1000.0)

        if len(latencies_ms) < ACK_LATENCY_MIN_SAMPLES:
            ack_latency_ms = None
        else:
            ack_latency_ms = _percentiles(latencies_ms, (50, 95, 99))

        return {
            "window": f"last_{window_hours}h",
            "total_envelopes": total_envelopes,
            "counts_by_kind": counts_by_kind,
            "counts_by_state": counts_by_state,
            "queue_depth_by_endpoint": queue_depth_by_endpoint,
            "ack_latency_ms": ack_latency_ms,
        }


def _percentiles(values: list[float], pcts: tuple[int, ...]) -> dict[str, int]:
    """Linear-interpolation percentiles, rounded to nearest int ms."""
    sorted_values = sorted(values)
    n = len(sorted_values)
    out: dict[str, int] = {}
    for p in pcts:
        if n == 1:
            out[f"p{p}"] = int(round(sorted_values[0]))
            continue
        rank = (p / 100.0) * (n - 1)
        lo = int(rank)
        hi = min(lo + 1, n - 1)
        frac = rank - lo
        v = sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac
        out[f"p{p}"] = int(round(v))
    return out


__all__ = ["PersistenceReader"]
