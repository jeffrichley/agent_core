"""Lifecycle tests for BusTailMCPEndpoint (no tool calls yet)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig
from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.handle import BusHandle
from agent_core.bus_tail.endpoint import BusTailMCPEndpoint


def test_endpoint_has_required_attributes():
    ep = BusTailMCPEndpoint(name="bus-tail", mount="/mcp/bus-tail")
    assert ep.name == "bus-tail"
    assert ep.mount == "/mcp/bus-tail"


def test_endpoint_default_mount_when_omitted():
    # Constructor allows omitting mount; defaults applied at runner level only.
    # If the runner passes mount=None we want a sane default.
    ep = BusTailMCPEndpoint(name="bus-tail")
    assert ep.mount == "/mcp/bus-tail"


def test_endpoint_asgi_app_returns_object():
    ep = BusTailMCPEndpoint(name="bus-tail", mount="/mcp/bus-tail")
    app = ep.asgi_app()
    assert app is not None
    assert callable(app)


@pytest.mark.asyncio
async def test_start_resolves_reader_via_bus_handle(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    await bus.start()
    try:
        ep = BusTailMCPEndpoint(name="bus-tail", mount="/mcp/bus-tail")
        handle = BusHandle(bus, "bus-tail")
        await ep.start(handle)
        assert ep._reader is not None
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_deliver_acks_immediately(tmp_path: Path):
    """Nothing should address bus-tail, but if it does, ack and don't crash."""
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    await bus.start()
    try:
        ep = BusTailMCPEndpoint(name="bus-tail", mount="/mcp/bus-tail")
        handle = BusHandle(bus, "bus-tail")
        await ep.start(handle)
        # Insert a fake envelope addressed to bus-tail.
        env = Envelope(
            id="env-1",
            correlation_id="corr-1",
            from_="someone",
            to="bus-tail",
            kind="TextMessage",
            payload=TextMessagePayload(text="hello"),
            created_at=datetime.now(UTC),
        )
        await bus._store.insert(env)
        # deliver should not raise; should ack the envelope.
        await ep.deliver(env)
        row = await bus._store.row("env-1")
        assert row is not None and row["state"] == "acked"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_stop_clears_reader():
    ep = BusTailMCPEndpoint(name="bus-tail", mount="/mcp/bus-tail")
    # No start, just construct + stop. Should be idempotent.
    await ep.stop()
    assert ep._reader is None
