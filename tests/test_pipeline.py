"""Tests for the Pipeline class — the core engine of the hook tool system."""

from pathlib import Path

import pytest
import yaml

from agent_core.hooks.pipeline import Pipeline
from agent_core.models import ToolResult


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    config = {
        "pipelines": {
            "SessionStart": [
                {
                    "tool": "agent_core.hooks.tools.time_injector.TimeInjector",
                    "params": {"format": "%Y-%m-%d"},
                }
            ],
            "PreToolUse": [
                {"tool": "agent_core.hooks.tools.time_injector.TimeInjector"},
            ],
        }
    }
    config_path = tmp_path / "agent_core.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")
    return config_path


@pytest.fixture
def empty_config_file(tmp_path: Path) -> Path:
    config = {"pipelines": {}}
    config_path = tmp_path / "agent_core.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")
    return config_path


class TestPipelineLoad:
    def test_load_valid_config(self, config_file: Path):
        pipeline = Pipeline(config_file)
        assert "SessionStart" in pipeline.config.pipelines
        assert len(pipeline.config.pipelines["SessionStart"]) == 1

    def test_load_empty_config(self, empty_config_file: Path):
        pipeline = Pipeline(empty_config_file)
        assert pipeline.config.pipelines == {}

    def test_load_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            Pipeline(tmp_path / "nonexistent.yaml")

    def test_load_invalid_yaml_raises(self, tmp_path: Path):
        bad_config = tmp_path / "agent_core.yaml"
        bad_config.write_text("pipelines:\n  - not: valid: yaml: [[", encoding="utf-8")
        with pytest.raises(Exception):
            Pipeline(bad_config)


class TestPipelineRun:
    def test_run_returns_tool_results(self, config_file: Path):
        pipeline = Pipeline(config_file)
        results = pipeline.run("SessionStart", {})
        assert len(results) == 1
        assert isinstance(results[0], ToolResult)
        assert results[0].heading == "Current Time"

    def test_run_unregistered_event_returns_empty(self, config_file: Path):
        pipeline = Pipeline(config_file)
        results = pipeline.run("PostCompact", {})
        assert results == []

    def test_run_passes_params_to_tool(self, config_file: Path):
        pipeline = Pipeline(config_file)
        results = pipeline.run("SessionStart", {})
        assert len(results[0].content) == 10

    def test_run_multiple_tools_in_order(self, tmp_path: Path):
        config = {
            "pipelines": {
                "SessionStart": [
                    {"tool": "agent_core.hooks.tools.time_injector.TimeInjector", "params": {"format": "%Y"}},
                    {"tool": "agent_core.hooks.tools.time_injector.TimeInjector", "params": {"format": "%m"}},
                ],
            }
        }
        config_path = tmp_path / "agent_core.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")
        pipeline = Pipeline(config_path)
        results = pipeline.run("SessionStart", {})
        assert len(results) == 2
        assert len(results[0].content) == 4
        assert len(results[1].content) == 2

    def test_run_bad_tool_class_skips_gracefully(self, tmp_path: Path):
        config = {
            "pipelines": {
                "SessionStart": [
                    {"tool": "nonexistent.module.FakeTool"},
                    {"tool": "agent_core.hooks.tools.time_injector.TimeInjector"},
                ],
            }
        }
        config_path = tmp_path / "agent_core.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")
        pipeline = Pipeline(config_path)
        results = pipeline.run("SessionStart", {})
        assert len(results) == 1
        assert results[0].heading == "Current Time"


class TestPipelineRender:
    def test_render_single_result(self, config_file: Path):
        pipeline = Pipeline(config_file)
        results = [ToolResult(heading="Test", content="Hello world")]
        markdown = pipeline.render(results)
        assert markdown == "## Test\n\nHello world"

    def test_render_multiple_results_separated_by_dividers(self, config_file: Path):
        pipeline = Pipeline(config_file)
        results = [
            ToolResult(heading="First", content="AAA"),
            ToolResult(heading="Second", content="BBB"),
        ]
        markdown = pipeline.render(results)
        assert markdown == "## First\n\nAAA\n\n---\n\n## Second\n\nBBB"

    def test_render_empty_results(self, config_file: Path):
        pipeline = Pipeline(config_file)
        markdown = pipeline.render([])
        assert markdown == ""
