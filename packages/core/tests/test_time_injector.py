"""Tests for the TimeInjector hook tool."""

import json
from pathlib import Path

import pytest

from agent_core.hooks.protocol import HookTool
from agent_core.hooks.tools import time_injector as ti_mod
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


# --- Cutover #07: per-turn re-anchoring on UserPromptSubmit ----------------------
#
# track_session=true is the configuration that makes TimeInjector useful on
# UserPromptSubmit (the canonical wiring in docs/examples/pepper-agent-core.yaml).
# It persists per-session start/last-seen timestamps and emits relative deltas
# alongside the absolute time so the agent re-anchors each turn.
#
# Failure mode being locked in: someone refactors and the per-turn deltas
# silently disappear. Pepper documented this as feedback_day_labels — drift
# without these markers shows up as wrong day names within hours.


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch):
    """Redirect the time-state file into tmp_path so tests don't touch ~/."""
    state_file = tmp_path / "time-state.json"
    monkeypatch.setattr(ti_mod, "_STATE_FILE", state_file)
    return state_file


class TestTimeInjectorTrackSession:
    def test_track_session_first_turn_writes_state_and_emits_absolute_only(
        self, isolated_state: Path
    ):
        """First sighting of a session: absolute time, no relative delta yet,
        and the state file gains an entry with started_at/last_seen."""
        tool = TimeInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={"session_id": "s-1"},
            params={"track_session": True},
        )
        assert result.heading == "Current Time"
        # No delta lines on first sighting — only the absolute timestamp.
        assert "Session started" not in result.content
        assert "Last user turn" not in result.content

        state = json.loads(isolated_state.read_text(encoding="utf-8"))
        assert "s-1" in state
        assert "started_at" in state["s-1"]
        assert "last_seen" in state["s-1"]

    def test_user_prompt_submit_emits_last_turn_delta(self, isolated_state: Path):
        """On UserPromptSubmit, after a SessionStart has registered the session,
        the result must include 'Last user turn Xm ago' so the model re-anchors."""
        tool = TimeInjector()
        # Seed state as if SessionStart fired ~120 seconds ago.
        seeded_started = 1_000_000.0
        isolated_state.write_text(
            json.dumps({"s-1": {"started_at": seeded_started, "last_seen": seeded_started}}),
            encoding="utf-8",
        )

        # Force the clock forward so deltas are deterministic.
        class _FakeDateTime:
            @staticmethod
            def now(tz=None):
                from datetime import datetime as _real

                return _real.fromtimestamp(seeded_started + 120, tz=tz)

        import agent_core.hooks.tools.time_injector as mod

        original_dt = mod.datetime
        mod.datetime = _FakeDateTime  # type: ignore[assignment]
        try:
            result = tool.execute(
                event="UserPromptSubmit",
                hook_input={"session_id": "s-1"},
                params={"track_session": True},
            )
        finally:
            mod.datetime = original_dt  # type: ignore[assignment]

        assert "Session started 2m ago" in result.content
        assert "Last user turn 2m ago" in result.content

    def test_session_start_does_not_emit_last_turn_marker(self, isolated_state: Path):
        """The 'Last user turn' marker must only appear on UserPromptSubmit —
        SessionStart should show 'Session started' but not 'Last user turn',
        because there is no prior user turn at session start."""
        tool = TimeInjector()
        seeded = 1_000_000.0
        isolated_state.write_text(
            json.dumps({"s-1": {"started_at": seeded, "last_seen": seeded}}),
            encoding="utf-8",
        )

        result = tool.execute(
            event="SessionStart",
            hook_input={"session_id": "s-1"},
            params={"track_session": True},
        )
        assert "Last user turn" not in result.content

    def test_track_session_disabled_default_emits_only_absolute(self, isolated_state: Path):
        """Without track_session=true, TimeInjector must not write state and
        must not emit any relative deltas — preserves backwards-compat for
        consumers registering it on SessionStart only."""
        tool = TimeInjector()
        result = tool.execute(
            event="UserPromptSubmit",
            hook_input={"session_id": "s-1"},
            params={},
        )
        assert "Session started" not in result.content
        assert "Last user turn" not in result.content
        # No state written (file should not exist).
        assert not isolated_state.exists()
