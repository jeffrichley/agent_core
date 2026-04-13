"""Tests for the HandoffWriter hook tool."""

import json
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

MOCK_ERROR_RESPONSE = "HANDOFF_ERROR: ConnectionError: API unreachable"


class TestHandoffWriter:
    """Tests for HandoffWriter — LLM-powered continuity note writer."""

    def test_implements_hook_tool_protocol(self):
        """HandoffWriter must satisfy the HookTool protocol."""
        assert isinstance(HandoffWriter(), HookTool)

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_writes_handoff_file(self, mock_extract, tmp_path: Path):
        """Writes a structured handoff note with session metadata and LLM content."""
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
        """HANDOFF_EMPTY sentinel produces a short note and writes the file."""
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
        assert output.exists()
        file_content = output.read_text(encoding="utf-8")
        assert "No significant content" in file_content
        assert "# Handoff Note" in file_content

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_handoff_error_response(self, mock_extract, tmp_path: Path):
        """HANDOFF_ERROR writes a fallback message, not raw error text."""
        mock_extract.return_value = MOCK_ERROR_RESPONSE
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "err-1"},
            params={"output_path": str(output)},
        )
        assert "failed" in result.content.lower()
        assert output.exists()
        file_content = output.read_text(encoding="utf-8")
        assert "ConnectionError" not in file_content
        assert "HANDOFF_ERROR" not in file_content
        assert "extraction failed" in file_content.lower()

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_missing_transcript(self, mock_extract, tmp_path: Path):
        """Missing transcript writes a note indicating no transcript was available."""
        mock_extract.return_value = MOCK_EMPTY_RESPONSE
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        tool.execute(
            event="SessionEnd",
            hook_input={"transcript_path": str(tmp_path / "missing.jsonl"), "session_id": "s1"},
            params={"output_path": str(output)},
        )
        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "No transcript available" in content

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_creates_parent_directories(self, mock_extract, tmp_path: Path):
        """Creates parent directories for the output path if they don't exist."""
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
        """Overwrites previous handoff content completely."""
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
        """Agent name param is forwarded to the LLM extraction function."""
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

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_deduplication_skips_second_call(self, mock_extract, tmp_path: Path):
        """Same session_id within 60 seconds is skipped on the second call."""
        mock_extract.return_value = MOCK_LLM_RESPONSE
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        hook_input = {"transcript_path": str(transcript), "session_id": "dedup-1"}
        params = {"output_path": str(output)}

        # First call writes normally
        result1 = tool.execute(event="PreCompact", hook_input=hook_input, params=params)
        assert "saved" in result1.content.lower() or "Handoff note" in result1.content
        assert mock_extract.call_count == 1

        # Second call with same session_id is skipped
        result2 = tool.execute(event="SessionEnd", hook_input=hook_input, params=params)
        assert "already written" in result2.content.lower()
        assert mock_extract.call_count == 1  # not called again

    def test_state_file_derived_from_output_path(self, tmp_path: Path):
        """State file is placed alongside the output file, not at a hardcoded repo path."""
        output = tmp_path / "vault" / "handoff.md"
        state = _state_file_for(output)
        assert state == tmp_path / "vault" / "handoff-state.json"

    def test_missing_output_path_raises(self):
        """Missing output_path param raises ValueError."""
        tool = HandoffWriter()
        with pytest.raises(ValueError, match="output_path"):
            tool.execute(event="PreCompact", hook_input={}, params={})
