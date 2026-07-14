"""Runner integration tests for the bus_tail endpoint type."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.bus_tail.endpoint import BusTailMCPEndpoint


def _write_yaml(tmp_path: Path, contents: str) -> Path:
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(contents, encoding="utf-8")
    return cfg


@pytest.mark.asyncio
async def test_runner_registers_bus_tail_mcp_from_yaml(tmp_path: Path, build_bus):
    storage = (tmp_path / "bus.sqlite").as_posix()
    cfg = _write_yaml(
        tmp_path,
        f"""
bus:
  storage_path: {storage}
endpoints:
  - name: bus-tail
    type: builtin.bus_tail_mcp
    params:
      mount: /mcp/bus-tail
""",
    )
    bus, http_host = await build_bus(cfg)
    try:
        ep = bus._endpoints_by_name["bus-tail"].endpoint
        assert isinstance(ep, BusTailMCPEndpoint)
        assert ep.mount == "/mcp/bus-tail"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_runner_mounts_bus_tail_on_http_host(tmp_path: Path, build_bus):
    storage = (tmp_path / "bus.sqlite").as_posix()
    cfg = _write_yaml(
        tmp_path,
        f"""
bus:
  storage_path: {storage}
endpoints:
  - name: bus-tail
    type: builtin.bus_tail_mcp
""",
    )
    bus, http_host = await build_bus(cfg)
    try:
        assert http_host is not None
        mount_paths = {m.mount for m in http_host._mounts}
        assert "/mcp/bus-tail" in mount_paths
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_runner_default_yaml_omits_bus_tail(tmp_path: Path, build_bus):
    storage = (tmp_path / "bus.sqlite").as_posix()
    cfg = _write_yaml(
        tmp_path,
        f"""
bus:
  storage_path: {storage}
endpoints: []
""",
    )
    bus, http_host = await build_bus(cfg)
    try:
        names = list(bus._endpoints_by_name.keys())
        assert "bus-tail" not in names
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_runner_uses_default_mount_when_omitted(tmp_path: Path, build_bus):
    storage = (tmp_path / "bus.sqlite").as_posix()
    cfg = _write_yaml(
        tmp_path,
        f"""
bus:
  storage_path: {storage}
endpoints:
  - name: bus-tail
    type: builtin.bus_tail_mcp
""",
    )
    bus, http_host = await build_bus(cfg)
    try:
        ep = bus._endpoints_by_name["bus-tail"].endpoint
        assert ep.mount == "/mcp/bus-tail"
    finally:
        await bus.stop()
