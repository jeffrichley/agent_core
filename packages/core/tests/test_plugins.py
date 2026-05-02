from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_core.bus.runner import build_bus_from_config
from agent_core.hooks.pipeline import Pipeline
from agent_core.models import ToolResult


class _PluginEndpoint:
    def __init__(self, *, name: str, **_: Any):
        self.name = name

    async def start(self, bus): ...
    async def deliver(self, envelope): ...
    async def stop(self): ...


class _PluginBusHook:
    async def execute(self, stage, envelope, params):
        return envelope


class _PluginTool:
    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        return ToolResult(heading="Plugin Tool", content="resolved via pluggy")


def _base_hook():
    class _Hook:
        @staticmethod
        def validate_config(*, raw_config):
            return None

        @staticmethod
        def register_endpoint_types():
            return {}

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

    return _Hook


class TestRunnerPluginHooks:
    async def test_resolve_endpoint_class_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        Hook = _base_hook()

        class _Hook(Hook):
            @staticmethod
            def register_endpoint_types():
                return {"plugin.stub.Endpoint": _PluginEndpoint}

        class _PluginManager:
            hook = _Hook()

        monkeypatch.setattr("agent_core.bus.runner.create_plugin_manager", lambda: _PluginManager())

        config = {"endpoints": [{"type": "plugin.stub.Endpoint", "name": "plug", "params": {}}]}
        p = tmp_path / "plugin-endpoint.yaml"
        p.write_text(yaml.dump(config), encoding="utf-8")
        bus, _ = await build_bus_from_config(p)
        assert "plug" in bus._endpoints_by_name

    async def test_endpoint_requires_specific_resolver(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        Hook = _base_hook()

        class _SpecificEndpoint:
            def __init__(self, *, name: str, **_: Any):
                self.name = name

            async def start(self, bus): ...
            async def deliver(self, envelope): ...
            async def stop(self): ...

        class _Hook(Hook):
            @staticmethod
            def register_endpoint_types():
                return {"plugin.stub.Endpoint": _SpecificEndpoint}

        class _PluginManager:
            hook = _Hook()

        monkeypatch.setattr("agent_core.bus.runner.create_plugin_manager", lambda: _PluginManager())

        config = {"endpoints": [{"type": "plugin.stub.Endpoint", "name": "plug", "params": {}}]}
        p = tmp_path / "plugin-precedence.yaml"
        p.write_text(yaml.dump(config), encoding="utf-8")
        bus, _ = await build_bus_from_config(p)
        spec = bus._endpoints_by_name["plug"]
        assert isinstance(spec.endpoint, _SpecificEndpoint)

    async def test_resolve_bus_hook_class_and_configure_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        seen: list[str] = []
        Hook = _base_hook()

        class _Hook(Hook):
            @staticmethod
            def register_endpoint_types():
                return {"plugin.stub.Endpoint": _PluginEndpoint}

            @staticmethod
            def register_bus_hook_types():
                return {"plugin.stub.BusHook": _PluginBusHook}

            @staticmethod
            def configure_bus_hook_instance(*, instance, stage, hook_config, services):
                seen.append(stage)

        class _PluginManager:
            hook = _Hook()

        monkeypatch.setattr("agent_core.bus.runner.create_plugin_manager", lambda: _PluginManager())

        config = {
            "endpoints": [{"type": "plugin.stub.Endpoint", "name": "stub", "params": {}}],
            "bus_hooks": {"pre_publish": [{"type": "plugin.stub.BusHook", "params": {}}], "pre_deliver": []},
        }
        p = tmp_path / "plugin-bus-hook.yaml"
        p.write_text(yaml.dump(config), encoding="utf-8")
        await build_bus_from_config(p)
        assert seen == ["pre_publish"]

    async def test_validate_config_can_reject(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        Hook = _base_hook()

        class _Hook(Hook):
            @staticmethod
            def validate_config(*, raw_config):
                raise ValueError("invalid plugin config")

        class _PluginManager:
            hook = _Hook()

        monkeypatch.setattr("agent_core.bus.runner.create_plugin_manager", lambda: _PluginManager())

        p = tmp_path / "reject.yaml"
        p.write_text(yaml.dump({"endpoints": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="invalid plugin config"):
            await build_bus_from_config(p)


class TestPipelinePluginHooks:
    def test_resolve_hook_tool_class_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        Hook = _base_hook()

        class _Hook(Hook):
            @staticmethod
            def register_hook_tool_types():
                return {"plugin.stub.Tool": _PluginTool}

        class _PluginManager:
            hook = _Hook()

        monkeypatch.setattr("agent_core.hooks.pipeline.create_plugin_manager", lambda: _PluginManager())

        config = {"pipelines": {"SessionStart": [{"type": "plugin.stub.Tool"}]}}
        config_path = tmp_path / "pipeline-plugin.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")
        pipeline = Pipeline(config_path)
        results = pipeline.run("SessionStart", {})
        assert len(results) == 1
        assert results[0].heading == "Plugin Tool"

class TestEntrypointPluginIntegration:
    async def test_alias_endpoint_resolves_without_monkeypatch(self, tmp_path: Path):
        config = {
            "endpoints": [
                {
                    "type": "builtin.stub",
                    "name": "stub-alias",
                    "params": {"auto_ack": True},
                }
            ]
        }
        p = tmp_path / "alias-endpoint.yaml"
        p.write_text(yaml.dump(config), encoding="utf-8")
        bus, _ = await build_bus_from_config(p)
        assert "stub-alias" in bus._endpoints_by_name

    def test_alias_hook_tool_resolves_without_monkeypatch(self, tmp_path: Path):
        config = {
            "pipelines": {
                "SessionStart": [
                    {
                        "type": "builtin.time_injector",
                        "params": {"format": "%Y"},
                    }
                ]
            }
        }
        config_path = tmp_path / "alias-pipeline.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")
        pipeline = Pipeline(config_path)
        results = pipeline.run("SessionStart", {})
        assert len(results) == 1
        assert results[0].heading == "Current Time"
        assert len(results[0].content) == 4
