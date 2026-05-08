"""list_pending optionally batches consecutive same-sender envelopes within window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


def _env(eid: str, frm: str, age_seconds: int, urgency: str = "green") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_=frm,
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=eid),
        urgency=urgency,
        created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )


@pytest.mark.asyncio
async def test_list_pending_batch_window_zero_returns_flat_list():
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    ep._pending = [
        _env("a", "alice", age_seconds=10),
        _env("b", "alice", age_seconds=8),
        _env("c", "alice", age_seconds=6),
    ]
    result = await ep._call_list_pending(batch_window_seconds=0)
    rows = result["items"]
    # Default 0: no batching — flat list of envelope dicts.
    assert all(row.get("type") in (None, "single") or "envelope" not in row for row in rows)
    assert len(rows) == 3
    assert result["meta"]["count"] == 3


@pytest.mark.asyncio
async def test_list_pending_batches_three_same_sender_within_window():
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    ep._pending = [
        _env("a", "alice", age_seconds=20),
        _env("b", "alice", age_seconds=10),
        _env("c", "alice", age_seconds=5),
    ]
    result = await ep._call_list_pending(batch_window_seconds=30)
    rows = result["items"]
    assert len(rows) == 1
    group = rows[0]
    assert group["type"] == "batch"
    assert group["from"] == "alice"
    assert group["kind"] == "TextMessage"
    assert [e["id"] for e in group["envelopes"]] == ["a", "b", "c"]
    assert group["total_age_seconds"] >= 15  # spans a → c


@pytest.mark.asyncio
async def test_list_pending_does_not_batch_different_senders():
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    ep._pending = [
        _env("a", "alice", age_seconds=10),
        _env("b", "bob", age_seconds=8),
        _env("c", "alice", age_seconds=6),
    ]
    result = await ep._call_list_pending(batch_window_seconds=30)
    rows = result["items"]
    assert len(rows) == 3
    assert all(r["type"] == "single" for r in rows)
    assert [r["envelope"]["id"] for r in rows] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_list_pending_batches_only_within_window():
    """Two old, one fresh — old two batch together; fresh one does not."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    ep._pending = [
        _env("a", "alice", age_seconds=120),
        _env("b", "alice", age_seconds=110),
        _env("c", "alice", age_seconds=5),
    ]
    result = await ep._call_list_pending(batch_window_seconds=30)
    rows = result["items"]
    assert len(rows) == 2
    assert rows[0]["type"] == "batch"
    assert [e["id"] for e in rows[0]["envelopes"]] == ["a", "b"]
    assert rows[1]["type"] == "single"
    assert rows[1]["envelope"]["id"] == "c"


@pytest.mark.asyncio
async def test_list_pending_batches_respect_urgency_grouping():
    """Different urgency tiers do NOT batch together even from same sender."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    ep._pending = [
        _env("r1", "alice", age_seconds=10, urgency="red"),
        _env("g1", "alice", age_seconds=5, urgency="green"),
    ]
    result = await ep._call_list_pending(batch_window_seconds=30)
    rows = result["items"]
    # Red comes first (tier sort), green second; they're not in the same group
    # because urgency differs.
    assert len(rows) == 2
    assert rows[0]["type"] == "single"
    assert rows[0]["envelope"]["id"] == "r1"
    assert rows[1]["type"] == "single"
    assert rows[1]["envelope"]["id"] == "g1"
