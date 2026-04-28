"""Tests for the HandoffWriter hook tool (detached claude CLI launcher)."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_core.hooks.protocol import HookTool
from agent_core.hooks.tools.handoff_writer import HandoffWriter, _state_file_for
from agent_core.models import ToolResult


def make_transcript(path: Path, turns: list[tuple[str, str]]) -> None:
    """Create a JSONL transcript file with the given turns."""
    with open(path, "w", encoding="utf-8") as f:
        for role, content in turns:
            f.write(json.dumps({"message": {"role": role, "content": content}}) + "\n")


class TestHandoffWriter:
    """Tests for HandoffWriter — detached claude CLI launcher."""

    def test_implements_hook_tool_protocol(self):
        assert isinstance(HandoffWriter(), HookTool)

    @patch("agent_core.hooks.tools.handoff_writer.subprocess.Popen")
    @patch("agent_core.hooks.tools.handoff_writer.shutil.which", return_value="/usr/bin/claude")
    def test_spawns_claude_process(self, mock_which, mock_popen, tmp_path: Path):
        """Spawns claude -p with correct arguments."""
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "test-123"},
            params={"output_path": str(output), "agent_name": "Pepper", "timezone": "US/Eastern"},
        )

        assert isinstance(result, ToolResult)
        assert "spawned" in result.content.lower() or "background" in result.content.lower()
        assert mock_popen.called

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "/usr/bin/claude"
        assert "-p" in cmd
        assert "--allowedTools" in cmd

    @patch("agent_core.hooks.tools.handoff_writer.subprocess.Popen")
    @patch("agent_core.hooks.tools.handoff_writer.shutil.which", return_value="/usr/bin/claude")
    def test_writes_context_file(self, mock_which, mock_popen, tmp_path: Path):
        """Writes transcript context to a temp file for the claude process."""
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello world"), ("assistant", "Hi there")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "ctx-1"},
            params={"output_path": str(output)},
        )

        context_files = list(tmp_path.glob("handoff-context-*.md"))
        assert len(context_files) == 1
        content = context_files[0].read_text(encoding="utf-8")
        assert "Hello world" in content

    @patch("agent_core.hooks.tools.handoff_writer.subprocess.Popen")
    @patch("agent_core.hooks.tools.handoff_writer.shutil.which", return_value="/usr/bin/claude")
    def test_prompt_contains_agent_name(self, mock_which, mock_popen, tmp_path: Path):
        """Prompt passed to claude includes the agent name."""
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "s4"},
            params={"output_path": str(output), "agent_name": "Pepper"},
        )

        cmd = mock_popen.call_args[0][0]
        prompt_idx = cmd.index("-p") + 1
        prompt = cmd[prompt_idx]
        assert "Pepper" in prompt

    def test_missing_transcript_returns_immediately(self, tmp_path: Path):
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="SessionEnd",
            hook_input={"transcript_path": str(tmp_path / "missing.jsonl"), "session_id": "s1"},
            params={"output_path": str(output)},
        )
        assert "No transcript" in result.content

    def test_empty_transcript_returns_immediately(self, tmp_path: Path):
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("", encoding="utf-8")
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="SessionEnd",
            hook_input={"transcript_path": str(transcript), "session_id": "s2"},
            params={"output_path": str(output)},
        )
        assert "empty" in result.content.lower() or "No transcript" in result.content

    @patch("agent_core.hooks.tools.handoff_writer.subprocess.Popen")
    @patch("agent_core.hooks.tools.handoff_writer.shutil.which", return_value="/usr/bin/claude")
    def test_deduplication_skips_second_call(self, mock_which, mock_popen, tmp_path: Path):
        """Same session_id within 60 seconds is skipped."""
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"

        # Write state simulating a recent handoff
        state_file = output.parent / "handoff-state.json"
        state_file.write_text(
            json.dumps({"session_id": "dedup-1", "timestamp": time.time()}),
            encoding="utf-8",
        )

        tool = HandoffWriter()
        result = tool.execute(
            event="SessionEnd",
            hook_input={"transcript_path": str(transcript), "session_id": "dedup-1"},
            params={"output_path": str(output)},
        )
        assert "already written" in result.content.lower()
        assert not mock_popen.called

    @patch("agent_core.hooks.tools.handoff_writer.subprocess.Popen")
    @patch("agent_core.hooks.tools.handoff_writer.shutil.which", return_value="/usr/bin/claude")
    def test_saves_state_after_spawn(self, mock_which, mock_popen, tmp_path: Path):
        """State file is written after spawning to prevent duplicate spawns."""
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "state-1"},
            params={"output_path": str(output)},
        )

        state_file = output.parent / "handoff-state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["session_id"] == "state-1"

    def test_state_file_derived_from_output_path(self, tmp_path: Path):
        output = tmp_path / "vault" / "handoff.md"
        state = _state_file_for(output)
        assert state == tmp_path / "vault" / "handoff-state.json"

    def test_missing_output_path_raises(self):
        tool = HandoffWriter()
        with pytest.raises(ValueError, match="output_path"):
            tool.execute(event="PreCompact", hook_input={}, params={})

    @patch("agent_core.hooks.tools.handoff_writer.shutil.which", return_value=None)
    def test_missing_claude_binary(self, mock_which, tmp_path: Path):
        """Returns error if claude CLI is not found."""
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "no-cli"},
            params={"output_path": str(output)},
        )
        assert "not found" in result.content.lower()

    @patch("agent_core.hooks.tools.handoff_writer.subprocess.Popen", side_effect=OSError("spawn failed"))
    @patch("agent_core.hooks.tools.handoff_writer.shutil.which", return_value="/usr/bin/claude")
    def test_spawn_failure_returns_error(self, mock_which, mock_popen, tmp_path: Path):
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "fail-1"},
            params={"output_path": str(output)},
        )
        assert "failed" in result.content.lower() or "spawn" in result.content.lower()
