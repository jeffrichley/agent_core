"""Tests for the TimeInjector hook tool."""

from agent_core.hooks.protocol import HookTool
from agent_core.hooks.tools.time_injector import TimeInjector
from agent_core.models import ToolResult


class TestTimeInjector:
    def test_implements_hook_tool_protocol(self):
        assert isinstance(TimeInjector(), HookTool)

    def test_returns_tool_result(self):
        tool = TimeInjector()
        result = tool.execute(event="SessionStart", hook_input={}, params={})
        assert isinstance(result, ToolResult)

    def test_heading_is_current_time(self):
        tool = TimeInjector()
        result = tool.execute(event="SessionStart", hook_input={}, params={})
        assert result.heading == "Current Time"

    def test_content_is_nonempty(self):
        tool = TimeInjector()
        result = tool.execute(event="SessionStart", hook_input={}, params={})
        assert len(result.content) > 0

    def test_custom_format_param(self):
        tool = TimeInjector()
        result = tool.execute(event="SessionStart", hook_input={}, params={"format": "%Y-%m-%d"})
        assert len(result.content) == 10
        assert result.content[4] == "-"
        assert result.content[7] == "-"

    def test_default_format_includes_day_name(self):
        tool = TimeInjector()
        result = tool.execute(event="SessionStart", hook_input={}, params={})
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        assert any(result.content.startswith(day) for day in days)

    def test_works_for_any_event(self):
        tool = TimeInjector()
        result_start = tool.execute(event="SessionStart", hook_input={}, params={})
        result_pre = tool.execute(event="PreToolUse", hook_input={}, params={})
        assert result_start.heading == result_pre.heading
