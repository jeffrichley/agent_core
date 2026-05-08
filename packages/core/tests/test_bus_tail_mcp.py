"""End-to-end tests for the four bus_tail MCP tools via FastMCP in-memory client."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastmcp import Client

from agent_core.bus.core import Bus, BusConfig
from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
from agent_core.bus.handle import BusHandle
from agent_core.bus_tail.endpoint import BusTailMCPEndpoint


async def _setup(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    await bus.start()
    ep = BusTailMCPEndpoint(name="bus-tail", mount="/mcp/bus-tail")
    handle = BusHandle(bus, "bus-tail")
    await ep.start(handle)
    return bus, ep


async def _insert(bus: Bus, **kwargs) -> Envelope:
    defaults = dict(
        id="e1",
        correlation_id=kwargs.get("id", "e1"),
        from_="alice",
        to="bob",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi there"),
        created_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    env = Envelope(**defaults)
    await bus._store.insert(env)
    return env


def _parse_text_content(call_result) -> object:
    """Extract JSON from FastMCP tool call result's TextContent block."""
    content = call_result.content[0]
    return json.loads(content.text)


@pytest.mark.asyncio
async def test_tail_tool_returns_envelope_summaries(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        await _insert(bus, id="e1")
        async with Client(ep._mcp) as client:
            result = await client.call_tool("tail", {"limit": 10})
        rows = _parse_text_content(result)
        assert isinstance(rows, list)
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == "e1"
        assert row["from"] == "alice"
        assert row["to"] == "bob"
        assert row["kind"] == "TextMessage"
        assert row["state"] == "pending"
        # Summary present, value-free.
        assert row["payload_summary"]["text_length"] == len("hi there")
        assert "hi there" not in json.dumps(row)
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_tail_tool_filters_by_kind(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        await _insert(bus, id="t1", kind="TextMessage")
        # Insert an Event-kind envelope.
        evt = Envelope(
            id="ev1",
            correlation_id="ev1",
            from_="alice",
            to="bob",
            kind="Event",
            payload=EventPayload(type="HandoffReady", data={"k": 1}),
            created_at=datetime.now(UTC),
        )
        await bus._store.insert(evt)
        async with Client(ep._mcp) as client:
            result = await client.call_tool("tail", {"kind": "Event"})
        rows = _parse_text_content(result)
        assert [r["id"] for r in rows] == ["ev1"]
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_get_envelope_tool_returns_full_payload(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        await _insert(bus, id="e1", payload=TextMessagePayload(text="full content"))
        async with Client(ep._mcp) as client:
            result = await client.call_tool("get_envelope", {"id": "e1"})
        env = _parse_text_content(result)
        assert env is not None
        assert env["id"] == "e1"
        # Full payload present, including the text.
        assert env["payload"]["text"] == "full content"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_get_envelope_tool_returns_null_for_missing(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        async with Client(ep._mcp) as client:
            result = await client.call_tool("get_envelope", {"id": "nope"})
        env = _parse_text_content(result)
        assert env is None
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_trace_correlation_orders_oldest_first(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
        cid = "corr-1"
        await _insert(bus, id="root", correlation_id=cid, created_at=base)
        await _insert(
            bus,
            id="reply",
            correlation_id=cid,
            in_reply_to="root",
            created_at=base + timedelta(seconds=1),
        )
        async with Client(ep._mcp) as client:
            result = await client.call_tool(
                "trace_correlation", {"correlation_id": cid}
            )
        chain = _parse_text_content(result)
        assert [e["id"] for e in chain] == ["root", "reply"]
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_trace_correlation_unknown_returns_empty_list(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        async with Client(ep._mcp) as client:
            result = await client.call_tool(
                "trace_correlation", {"correlation_id": "missing"}
            )
        chain = _parse_text_content(result)
        assert chain == []
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_metrics_tool_shape(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        await _insert(bus, id="e1")
        async with Client(ep._mcp) as client:
            result = await client.call_tool("metrics", {})
        snap = _parse_text_content(result)
        assert snap["window"] == "last_24h"
        assert "counts_by_kind" in snap
        assert "counts_by_state" in snap
        assert "queue_depth_by_endpoint" in snap
        assert "ack_latency_ms" in snap  # may be None
        assert snap["total_envelopes"] >= 1
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_tail_summary_value_free_for_text_message(tmp_path: Path):
    bus, ep = await _setup(tmp_path)
    try:
        await _insert(bus, id="e1", payload=TextMessagePayload(text="SECRET-PHRASE"))
        async with Client(ep._mcp) as client:
            result = await client.call_tool("tail", {})
        text = result.content[0].text
        assert "SECRET-PHRASE" not in text
    finally:
        await bus.stop()
