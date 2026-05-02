"""Tests for HandoffWriter enqueue-only behavior."""

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_core.hooks.protocol import HookTool
from agent_core.hooks.tools.handoff_writer import HandoffWriter
from agent_core.models import ToolResult


class _FakeResponse:
    def __init__(self, body: dict):
        self._stream = BytesIO(json.dumps(body).encode("utf-8"))

    def read(self) -> bytes:
        return self._stream.read()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestHandoffWriter:
    def test_implements_hook_tool_protocol(self):
        assert isinstance(HandoffWriter(), HookTool)

    @patch("agent_core.hooks.tools.handoff_writer.urllib.request.urlopen")
    def test_enqueues_daemon_job(self, mock_urlopen, tmp_path: Path):
        mock_urlopen.return_value = _FakeResponse({"job_id": "job-123", "status": "accepted"})
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("{}", encoding="utf-8")
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="SessionEnd",
            hook_input={"transcript_path": str(transcript), "session_id": "session-1"},
            params={
                "output_path": str(output),
                "agent_name": "Pepper",
                "handoff_jobs_url": "http://127.0.0.1:8788/internal/handoff-jobs",
            },
        )

        assert isinstance(result, ToolResult)
        assert "Enqueued daemon handoff job job-123" in result.content
        assert mock_urlopen.called
        req = mock_urlopen.call_args.args[0]
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["session_id"] == "session-1"
        assert payload["event"] == "SessionEnd"
        assert payload["handoff_path"] == str(output)

    def test_missing_output_path_raises(self):
        tool = HandoffWriter()
        with pytest.raises(ValueError, match="output_path"):
            tool.execute(event="PreCompact", hook_input={}, params={})

    def test_missing_session_id_returns_failure(self, tmp_path: Path):
        output = tmp_path / "handoff.md"
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("{}", encoding="utf-8")
        tool = HandoffWriter()
        result = tool.execute(
            event="SessionEnd",
            hook_input={"transcript_path": str(transcript)},
            params={"output_path": str(output)},
        )
        assert result.heading == "Handoff Job Enqueue Failed"
        assert "session_id" in result.content

    def test_missing_transcript_path_returns_failure(self, tmp_path: Path):
        output = tmp_path / "handoff.md"
        tool = HandoffWriter()
        result = tool.execute(
            event="SessionEnd",
            hook_input={"session_id": "s-1"},
            params={"output_path": str(output)},
        )
        assert result.heading == "Handoff Job Enqueue Failed"
        assert "transcript_path" in result.content

    @patch(
        "agent_core.hooks.tools.handoff_writer.urllib.request.urlopen",
        side_effect=OSError("down"),
    )
    def test_daemon_unreachable_returns_failure(self, mock_urlopen, tmp_path: Path):
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("{}", encoding="utf-8")
        output = tmp_path / "handoff.md"
        tool = HandoffWriter()
        result = tool.execute(
            event="SessionEnd",
            hook_input={"transcript_path": str(transcript), "session_id": "s-2"},
            params={"output_path": str(output)},
        )
        assert result.heading == "Handoff Job Enqueue Failed"
        assert "Cannot reach handoff daemon endpoint" in result.content
