"""Tests for the Bus runner — load YAML, instantiate, start."""

from pathlib import Path

import pytest
import yaml

from agent_core.bus.runner import build_bus_from_config, BusBootError


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    config = {
        "bus": {
            "storage_path": str(tmp_path / "bus.sqlite"),
            "redelivery_timeout_seconds": 60,
            "max_delivery_attempts": 3,
            "max_pending_per_endpoint": 100,
        },
        "http": {"bind_host": "127.0.0.1", "bind_port": 18788},
        "endpoints": [
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "stub-a",
                "description": "First stub.",
                "params": {"auto_ack": True},
            },
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "stub-b",
                "description": "Second stub.",
                "params": {},
            },
        ],
        "bus_hooks": {"pre_publish": [], "pre_deliver": []},
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


class TestRunner:
    async def test_loads_endpoints(self, cfg_path: Path):
        bus = await build_bus_from_config(cfg_path)
        try:
            await bus.start()
            names = {info.name for info in bus._endpoints()}
            assert names == {"stub-a", "stub-b"}
            descs = {info.name: info.description for info in bus._endpoints()}
            assert descs["stub-a"] == "First stub."
        finally:
            await bus.stop()

    async def test_unknown_class_raises(self, tmp_path: Path):
        config = {
            "endpoints": [
                {
                    "class": "agent_core.endpoints.does_not_exist.Foo",
                    "name": "x",
                    "params": {},
                }
            ]
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(BusBootError):
            await build_bus_from_config(p)

    async def test_class_not_endpoint_protocol(self, tmp_path: Path):
        # Pick something that's importable but doesn't satisfy Endpoint.
        config = {"endpoints": [{"class": "datetime.datetime", "name": "x", "params": {}}]}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(BusBootError, match="does not satisfy Endpoint"):
            await build_bus_from_config(p)

    async def test_non_loopback_bind_refused(self, tmp_path: Path):
        config = {
            "http": {"bind_host": "0.0.0.0", "bind_port": 8788},
            "endpoints": [],
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(BusBootError, match="loopback"):
            await build_bus_from_config(p)

    async def test_endpoint_missing_name_raises(self, tmp_path: Path):
        config = {"endpoints": [{"class": "agent_core.endpoints.stub.StubEndpoint", "params": {}}]}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(BusBootError, match="missing required 'name'"):
            await build_bus_from_config(p)

    async def test_endpoint_missing_class_raises(self, tmp_path: Path):
        config = {"endpoints": [{"name": "x", "params": {}}]}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(BusBootError, match="missing required 'class'"):
            await build_bus_from_config(p)
