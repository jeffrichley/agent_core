"""Bus.snapshot_for_agent: returns the current pending summary for an agent."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core.endpoints.stub import StubEndpoint


def _env(eid: str, frm: str = "stub", urgency: str = "green") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_=frm,
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=eid),
        urgency=urgency,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_snapshot_for_agent_returns_summary_when_pending(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    bus.register(EndpointSpec(endpoint=ep))
    ep._pending = [_env("a"), _env("b", urgency="red")]
    summary = bus.snapshot_for_agent("agent")
    assert summary is not None
    assert summary["meta"]["count"] == 2
    assert summary["meta"]["urgency_max"] == "red"
    assert summary["meta"]["endpoint"] == "agent"


@pytest.mark.asyncio
async def test_snapshot_for_agent_returns_zero_count_when_empty(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    bus.register(EndpointSpec(endpoint=ep))
    summary = bus.snapshot_for_agent("agent")
    assert summary is not None
    assert summary["meta"]["count"] == 0


@pytest.mark.asyncio
async def test_snapshot_for_agent_returns_none_for_unknown_agent(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    summary = bus.snapshot_for_agent("ghost")
    assert summary is None


@pytest.mark.asyncio
async def test_snapshot_for_agent_returns_none_for_non_claude_endpoint(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    stub = StubEndpoint(name="probe")
    bus.register(EndpointSpec(endpoint=stub, description="stub"))
    summary = bus.snapshot_for_agent("probe")
    assert summary is None
