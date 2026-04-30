"""list_pending returns envelopes sorted by urgency tier, FIFO within tier."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


def _env(eid: str, urgency: str, age_seconds: int) -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="src",
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text="x"),
        urgency=urgency,
        created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )


def _ids(rows: list[dict]) -> list[str]:
    return [row["id"] for row in rows]


def _make_endpoint() -> ClaudeCodeMCPEndpoint:
    return ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")


@pytest.mark.asyncio
async def test_list_pending_red_first_then_yellow_then_green():
    ep = _make_endpoint()
    ep._pending = [
        _env("g1", "green", age_seconds=10),
        _env("y1", "yellow", age_seconds=8),
        _env("r1", "red", age_seconds=6),
        _env("g2", "green", age_seconds=4),
    ]
    rows = await ep._call_list_pending()
    assert _ids(rows) == ["r1", "y1", "g1", "g2"]


@pytest.mark.asyncio
async def test_list_pending_fifo_within_same_tier():
    ep = _make_endpoint()
    ep._pending = [
        _env("r1", "red", age_seconds=30),  # oldest
        _env("r2", "red", age_seconds=20),
        _env("r3", "red", age_seconds=10),  # newest
    ]
    rows = await ep._call_list_pending()
    assert _ids(rows) == ["r1", "r2", "r3"]


@pytest.mark.asyncio
async def test_list_pending_tier_wins_over_arrival_time():
    """A newer red beats an older green."""
    ep = _make_endpoint()
    ep._pending = [
        _env("g_old", "green", age_seconds=100),
        _env("r_new", "red", age_seconds=1),
    ]
    rows = await ep._call_list_pending()
    assert _ids(rows) == ["r_new", "g_old"]


@pytest.mark.asyncio
async def test_list_pending_empty_returns_empty():
    ep = _make_endpoint()
    rows = await ep._call_list_pending()
    assert rows == []
