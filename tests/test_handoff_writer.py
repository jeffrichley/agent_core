"""Tests for the HandoffWriter hook tool."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_core.hooks.protocol import HookTool
from agent_core.hooks.tools.handoff_writer import HandoffWriter
from agent_core.models import ToolResult


def make_transcript(path: Path, turns: list[tuple[str, str]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for role, content in turns:
            f.write(json.dumps({"message": {"role": role, "content": content}}) + "\n")


MOCK_LLM_RESPONSE = """## What We Were Working On
- Building a pluggable hook tool system
- Designing FileInjector and HandoffWriter

## Decisions Made
- Use LLM extraction instead of heuristics

## Emotional Temperature
Productive and collaborative.

## Open Threads
- Need to test end-to-end hook firing

## Observations
- The user prefers thorough documentation"""

MOCK_EMPTY_RESPONSE = "HANDOFF_EMPTY"


class TestHandoffWriter:
    def test_implements_hook_tool_protocol(self):
        assert isinstance(HandoffWriter(), HookTool)

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_writes_handoff_file(self, mock_extract, tmp_path: Path):
        mock_extract.return_value = MOCK_LLM_RESPONSE
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "test-123"},
            params={"output_path": str(output), "agent_name": "TestAgent"},
        )

        assert isinstance(result, ToolResult)
        assert result.heading == "Handoff Note Written"
        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "# Handoff Note" in content
        assert "test-123" in content
        assert "What We Were Working On" in content

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_handoff_empty_response(self, mock_extract, tmp_path: Path):
        mock_extract.return_value = MOCK_EMPTY_RESPONSE
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "hi"), ("assistant", "hello")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "test-456"},
            params={"output_path": str(output)},
        )
        assert "No significant content" in result.content

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_missing_transcript(self, mock_extract, tmp_path: Path):
        mock_extract.return_value = MOCK_EMPTY_RESPONSE
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="SessionEnd",
            hook_input={"transcript_path": str(tmp_path / "missing.jsonl"), "session_id": "s1"},
            params={"output_path": str(output)},
        )
        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "No transcript available" in content

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_creates_parent_directories(self, mock_extract, tmp_path: Path):
        mock_extract.return_value = MOCK_LLM_RESPONSE
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "deep" / "nested" / "handoff.md"

        tool = HandoffWriter()
        tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "s2"},
            params={"output_path": str(output)},
        )
        assert output.exists()

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_overwrites_existing_handoff(self, mock_extract, tmp_path: Path):
        mock_extract.return_value = MOCK_LLM_RESPONSE
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"
        output.write_text("OLD CONTENT THAT SHOULD BE GONE", encoding="utf-8")

        tool = HandoffWriter()
        tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "s3"},
            params={"output_path": str(output)},
        )
        content = output.read_text(encoding="utf-8")
        assert "OLD CONTENT" not in content
        assert "What We Were Working On" in content

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_agent_name_passed_to_extract(self, mock_extract, tmp_path: Path):
        mock_extract.return_value = MOCK_LLM_RESPONSE
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "s4"},
            params={"output_path": str(output), "agent_name": "Pepper"},
        )
        mock_extract.assert_called_once()
        args = mock_extract.call_args[0]
        assert args[1] == "Pepper"

    def test_missing_output_path_raises(self):
        tool = HandoffWriter()
        with pytest.raises(ValueError, match="output_path"):
            tool.execute(event="PreCompact", hook_input={}, params={})
