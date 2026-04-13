"""Tests for agent_core Pydantic models."""

from agent_core.models import ToolResult, ToolConfig, PipelineConfig
import pytest


class TestToolResult:
    def test_create_tool_result(self):
        result = ToolResult(heading="Test Heading", content="Test content here")
        assert result.heading == "Test Heading"
        assert result.content == "Test content here"

    def test_tool_result_requires_heading(self):
        with pytest.raises(Exception):
            ToolResult(content="no heading")

    def test_tool_result_requires_content(self):
        with pytest.raises(Exception):
            ToolResult(heading="no content")


class TestToolConfig:
    def test_create_tool_config(self):
        config = ToolConfig(tool="agent_core.hooks.tools.time_injector.TimeInjector")
        assert config.tool == "agent_core.hooks.tools.time_injector.TimeInjector"
        assert config.params == {}

    def test_tool_config_with_params(self):
        config = ToolConfig(
            tool="agent_core.hooks.tools.time_injector.TimeInjector",
            params={"format": "%Y-%m-%d"},
        )
        assert config.params == {"format": "%Y-%m-%d"}

    def test_tool_config_requires_tool(self):
        with pytest.raises(Exception):
            ToolConfig(params={"format": "%Y-%m-%d"})


class TestPipelineConfig:
    def test_create_pipeline_config(self):
        config = PipelineConfig(
            pipelines={
                "SessionStart": [
                    ToolConfig(tool="agent_core.hooks.tools.time_injector.TimeInjector"),
                ],
            }
        )
        assert "SessionStart" in config.pipelines
        assert len(config.pipelines["SessionStart"]) == 1

    def test_empty_pipelines(self):
        config = PipelineConfig(pipelines={})
        assert config.pipelines == {}

    def test_multiple_events(self):
        tool = ToolConfig(tool="some.tool.Class")
        config = PipelineConfig(
            pipelines={
                "SessionStart": [tool, tool],
                "PreToolUse": [tool],
            }
        )
        assert len(config.pipelines["SessionStart"]) == 2
        assert len(config.pipelines["PreToolUse"]) == 1
