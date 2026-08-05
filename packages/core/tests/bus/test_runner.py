"""Tests for the Bus runner — load YAML, instantiate, start."""

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_core.bus.runner import BusBootError, build_bus_from_config


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
                "type": "builtin.stub",
                "name": "stub-a",
                "description": "First stub.",
                "params": {"auto_ack": True},
            },
            {
                "type": "builtin.stub",
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
    async def test_loads_endpoints(self, cfg_path: Path, build_bus):
        bus, _ = await build_bus(cfg_path)
        try:
            await bus.start()
            names = {info.name for info in bus._endpoints()}
            assert names == {"stub-a", "stub-b"}
            descs = {info.name: info.description for info in bus._endpoints()}
            assert descs["stub-a"] == "First stub."
        finally:
            await bus.stop()

    async def test_unknown_class_skipped_boot_continues(
        self, tmp_path: Path, build_bus, caplog
    ):
        config = {
            "endpoints": [
                {
                    "type": "does.not_exist.Foo",
                    "name": "x",
                    "params": {},
                }
            ]
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
            bus, _http = await build_bus(p)
        try:
            assert any(
                "does.not_exist.Foo" in r.message or "x" in r.message
                for r in caplog.records
            )
        finally:
            await bus.stop()

    async def test_class_not_endpoint_protocol_skipped_boot_continues(
        self, tmp_path: Path, build_bus, caplog
    ):
        # Unknown class aliases are rejected in strict plugin-resolution mode.
        config = {"endpoints": [{"type": "datetime.datetime", "name": "x", "params": {}}]}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
            bus, _http = await build_bus(p)
        try:
            assert any(
                "unknown endpoint type" in r.message or "datetime.datetime" in r.message
                for r in caplog.records
            )
        finally:
            await bus.stop()

    async def test_non_loopback_bind_refused(self, tmp_path: Path):
        config = {
            "http": {"bind_host": "0.0.0.0", "bind_port": 8788},
            "endpoints": [],
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(BusBootError, match="loopback"):
            await build_bus_from_config(p)

    async def test_non_loopback_bind_warn_mode_permitted(self, tmp_path: Path, build_bus):
        config = {
            "http": {"bind_host": "0.0.0.0", "bind_port": 8788},
            "bus_auth_mode": "warn",
            "endpoints": [],
        }
        p = tmp_path / "warn_cfg.yaml"
        p.write_text(yaml.dump(config))
        bus, http = await build_bus(p)  # must not raise BusBootError
        assert http is None  # no MCPHostable endpoints configured

    async def test_non_loopback_bind_enforce_mode_permitted(self, tmp_path: Path, build_bus):
        config = {
            "http": {"bind_host": "0.0.0.0", "bind_port": 8788},
            "bus_auth_mode": "enforce",
            "endpoints": [],
        }
        p = tmp_path / "enforce_cfg.yaml"
        p.write_text(yaml.dump(config))
        bus, http = await build_bus(p)  # must not raise BusBootError
        assert http is None  # no MCPHostable endpoints configured

    async def test_endpoint_missing_name_raises_bus_boot_error(
        self, tmp_path: Path
    ):
        config = {"endpoints": [{"type": "builtin.stub", "params": {}}]}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(BusBootError, match="Field required"):
            await build_bus_from_config(p)

    async def test_endpoint_missing_type_raises_bus_boot_error(
        self, tmp_path: Path
    ):
        config = {"endpoints": [{"name": "x", "params": {}}]}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(BusBootError, match="Field required"):
            await build_bus_from_config(p)

    async def test_unknown_top_level_key_raises_bus_boot_error(
        self, tmp_path: Path
    ):
        config = {"buus": {}}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(BusBootError):
            await build_bus_from_config(p)

    async def test_plugin_can_resolve_endpoint_class(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_bus
    ):
        class _PluginEndpoint:
            def __init__(self, *, name: str, **_: Any):
                self.name = name

            async def start(self, bus): ...
            async def deliver(self, envelope): ...
            async def stop(self): ...

        class _Hook:
            @staticmethod
            def register_endpoint_types():
                return {"plugin.stub.Endpoint": _PluginEndpoint}

            @staticmethod
            def validate_config(*, raw_config):
                return None

            @staticmethod
            def register_bus_hook_types():
                return {}

            @staticmethod
            def register_hook_tool_types():
                return {}

            @staticmethod
            def configure_endpoint_instance(*, instance, endpoint_name, endpoint_config, services):
                return None

            @staticmethod
            def configure_bus_hook_instance(*, instance, stage, hook_config, services):
                return None

            @staticmethod
            def wire_endpoints_after_registration(*, endpoints, raw_endpoint_configs, services):
                return None

            @staticmethod
            def reserved_endpoint_params():
                return []

        class _PluginManager:
            hook = _Hook()

        monkeypatch.setattr("agent_core.bus.runner.create_plugin_manager", lambda: _PluginManager())

        config = {"endpoints": [{"type": "plugin.stub.Endpoint", "name": "plug", "params": {}}]}
        p = tmp_path / "plugin.yaml"
        p.write_text(yaml.dump(config))
        bus, _ = await build_bus(p)
        assert "plug" in bus._endpoints_by_name
