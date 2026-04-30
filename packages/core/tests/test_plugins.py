import importlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_core.bus.runner import build_bus_from_config
from agent_core.hooks.pipeline import Pipeline
from agent_core.models import ToolResult
from agent_core.plugins.manager import create_plugin_manager


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
        def resolve_class(*, class_path: str):
            module_path, _, class_name = class_path.rpartition(".")
            if not module_path:
                return None
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                return None
            resolved = getattr(module, class_name, None)
            return resolved if isinstance(resolved, type) else None

        @staticmethod
        def validate_config(*, raw_config):
            return None

        @staticmethod
        def resolve_endpoint_class(*, endpoint_class: str):
            return None

        @staticmethod
        def resolve_bus_hook_class(*, hook_class: str):
            return None

        @staticmethod
        def resolve_hook_tool_class(*, tool_class: str):
            return None

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
            def resolve_endpoint_class(*, endpoint_class: str):
                if endpoint_class == "plugin.stub.Endpoint":
                    return _PluginEndpoint
                return None

        class _PluginManager:
            hook = _Hook()

        monkeypatch.setattr("agent_core.bus.runner.create_plugin_manager", lambda: _PluginManager())

        config = {"endpoints": [{"class": "plugin.stub.Endpoint", "name": "plug", "params": {}}]}
        p = tmp_path / "plugin-endpoint.yaml"
        p.write_text(yaml.dump(config), encoding="utf-8")
        bus, _ = await build_bus_from_config(p)
        assert "plug" in bus._endpoints_by_name

    async def test_endpoint_specific_resolver_precedes_generic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        Hook = _base_hook()

        class _GenericEndpoint:
            def __init__(self, *, name: str, **_: Any):
                self.name = name

            async def start(self, bus): ...
            async def deliver(self, envelope): ...
            async def stop(self): ...

        class _SpecificEndpoint:
            def __init__(self, *, name: str, **_: Any):
                self.name = name

            async def start(self, bus): ...
            async def deliver(self, envelope): ...
            async def stop(self): ...

        class _Hook(Hook):
            @staticmethod
            def resolve_endpoint_class(*, endpoint_class: str):
                if endpoint_class == "plugin.stub.Endpoint":
                    return _SpecificEndpoint
                return None

            @staticmethod
            def resolve_class(*, class_path: str):
                if class_path == "plugin.stub.Endpoint":
                    return _GenericEndpoint
                return None

        class _PluginManager:
            hook = _Hook()

        monkeypatch.setattr("agent_core.bus.runner.create_plugin_manager", lambda: _PluginManager())

        config = {"endpoints": [{"class": "plugin.stub.Endpoint", "name": "plug", "params": {}}]}
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
            def resolve_bus_hook_class(*, hook_class: str):
                if hook_class == "plugin.stub.BusHook":
                    return _PluginBusHook
                return None

            @staticmethod
            def configure_bus_hook_instance(*, instance, stage, hook_config, services):
                seen.append(stage)

        class _PluginManager:
            hook = _Hook()

        monkeypatch.setattr("agent_core.bus.runner.create_plugin_manager", lambda: _PluginManager())

        config = {
            "endpoints": [{"class": "agent_core.endpoints.stub.StubEndpoint", "name": "stub", "params": {}}],
            "bus_hooks": {"pre_publish": [{"class": "plugin.stub.BusHook", "params": {}}], "pre_deliver": []},
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
            def resolve_hook_tool_class(*, tool_class: str):
                if tool_class == "plugin.stub.Tool":
                    return _PluginTool
                return None

        class _PluginManager:
            hook = _Hook()

        monkeypatch.setattr("agent_core.hooks.pipeline.create_plugin_manager", lambda: _PluginManager())

        config = {"pipelines": {"SessionStart": [{"tool": "plugin.stub.Tool"}]}}
        config_path = tmp_path / "pipeline-plugin.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")
        pipeline = Pipeline(config_path)
        results = pipeline.run("SessionStart", {})
        assert len(results) == 1
        assert results[0].heading == "Plugin Tool"


class TestPluginManagerResolution:
    def test_builtin_manager_resolves_dotted_import(self):
        pm = create_plugin_manager()
        resolved = pm.hook.resolve_class(class_path="datetime.datetime")
        assert resolved is not None
        assert resolved.__name__ == "datetime"
