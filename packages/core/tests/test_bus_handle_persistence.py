"""BusHandle.persistence() exposes Bus._store post-start."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig
from agent_core.bus.handle import BusHandle
from agent_core.bus.persistence import Persistence


@pytest.mark.asyncio
async def test_persistence_returns_store_after_bus_start(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    await bus.start()
    try:
        handle = BusHandle(bus, "any-name")
        store = handle.persistence()
        assert isinstance(store, Persistence)
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_persistence_raises_before_bus_start(tmp_path: Path):
    bus = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    handle = BusHandle(bus, "any-name")
    with pytest.raises(RuntimeError, match="not initialized"):
        handle.persistence()
