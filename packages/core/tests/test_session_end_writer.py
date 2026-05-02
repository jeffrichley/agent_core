"""Tests for SessionEndWriter — JSONL daily log + handoff end section."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_core.hooks.protocol import HookTool
from agent_core.hooks.tools.session_end_writer import SessionEndWriter, _session_end_marker
from agent_core.models import ToolResult


def make_transcript(path: Path, turns: list[tuple[str, str]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for role, content in turns:
            f.write(json.dumps({"message": {"role": role, "content": content}}) + "\n")


class TestSessionEndWriter:
    def test_implements_hook_tool_protocol(self):
        assert isinstance(SessionEndWriter(), HookTool)

    def test_requires_vault_path(self):
        with pytest.raises(ValueError, match="vault_path"):
            SessionEndWriter().execute(
                "SessionEnd",
                hook_input={"session_id": "s1", "transcript_path": ""},
                params={},
            )

    def test_appends_jsonl_and_end_section_when_handoff_matches_session(
        self, tmp_path: Path
    ):
        vault = tmp_path / "Memory"
        handoff_rel = Path("pepper") / "handoff.md"
        handoff = vault / handoff_rel
        handoff.parent.mkdir(parents=True)
        handoff.write_text(
            "# Handoff Note\n**Session:** sid-99\n\n## What We Were Working On\n- x\n",
            encoding="utf-8",
        )
        transcript = tmp_path / "t.jsonl"
        make_transcript(transcript, [("user", "Hi")])

        result = SessionEndWriter().execute(
            "SessionEnd",
            hook_input={"session_id": "sid-99", "transcript_path": str(transcript)},
            params={
                "vault_path": str(vault),
                "daily_log_dir": "daily/raw",
                "handoff_file": str(handoff_rel).replace("\\", "/"),
                "timezone": "US/Eastern",
                "agent_name": "Pepper",
            },
        )

        assert isinstance(result, ToolResult)
        assert result.heading == "Session End Captured"
        assert result.content == (
            "Session summary appended to daily log. Handoff note updated."
        )

        daily_dir = vault / "daily" / "raw"
        logs = list(daily_dir.glob("*.jsonl"))
        assert len(logs) == 1
        lines = logs[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["dir"] == "system"
        assert row["src"] == "session-end"
        assert row["cid"] == "session-sid-99"
        assert row["sender"] == "Pepper"
        assert "Session ended." in row["content"]
        assert "Topics:" in row["content"]

        updated = handoff.read_text(encoding="utf-8")
        assert "## End of Session" in updated
        assert _session_end_marker("sid-99") in updated

        st_path = vault / "pepper" / "handoff-status.json"
        assert st_path.exists()
        st = json.loads(st_path.read_text(encoding="utf-8"))
        assert st["state"] == "ready"
        assert st["session_id"] == "sid-99"
        assert st.get("content_sha256")

    def test_jsonl_append_preserves_existing_rows(self, tmp_path: Path):
        vault = tmp_path / "v"
        daily = vault / "daily" / "raw"
        daily.mkdir(parents=True)
        log = daily / "2099-01-15.jsonl"
        log.write_text('{"legacy": true}\n', encoding="utf-8")

        handoff = vault / "pepper" / "handoff.md"
        handoff.parent.mkdir(parents=True)
        handoff.write_text("# Handoff Note\n**Session:** one\n", encoding="utf-8")
        transcript = tmp_path / "t.jsonl"
        make_transcript(transcript, [("user", "a")])

        with patch(
            "agent_core.hooks.tools.session_end_writer._daily_log_path",
            return_value=log,
        ):
            SessionEndWriter().execute(
                "SessionEnd",
                hook_input={"session_id": "one", "transcript_path": str(transcript)},
                params={"vault_path": str(vault), "timezone": "US/Eastern"},
            )

        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"legacy": True}
        row = json.loads(lines[1])
        assert row["cid"] == "session-one"

    def test_reads_handoff_with_bom(self, tmp_path: Path):
        vault = tmp_path / "m"
        handoff = vault / "pepper" / "handoff.md"
        handoff.parent.mkdir(parents=True)
        body = "# Handoff Note\n**Session:** bom-session\n"
        handoff.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
        transcript = tmp_path / "t.jsonl"
        make_transcript(transcript, [("user", "x")])

        SessionEndWriter().execute(
            "SessionEnd",
            hook_input={"session_id": "bom-session", "transcript_path": str(transcript)},
            params={"vault_path": str(vault), "timezone": "US/Eastern"},
        )

        text = handoff.read_text(encoding="utf-8")
        assert text.startswith("# Handoff Note")
        assert "## End of Session" in text

    def test_skips_second_handoff_mutation_same_session(self, tmp_path: Path):
        vault = tmp_path / "v"
        handoff = vault / "pepper" / "handoff.md"
        handoff.parent.mkdir(parents=True)
        handoff.write_text("# Handoff Note\n**Session:** dup\n", encoding="utf-8")
        transcript = tmp_path / "t.jsonl"
        make_transcript(transcript, [("user", "u")])

        tool = SessionEndWriter()
        tool.execute(
            "SessionEnd",
            hook_input={"session_id": "dup", "transcript_path": str(transcript)},
            params={"vault_path": str(vault), "timezone": "US/Eastern"},
        )
        after_first = handoff.read_text(encoding="utf-8")
        assert _session_end_marker("dup") in after_first
        tool.execute(
            "SessionEnd",
            hook_input={"session_id": "dup", "transcript_path": str(transcript)},
            params={"vault_path": str(vault), "timezone": "US/Eastern"},
        )
        assert handoff.read_text(encoding="utf-8") == after_first

    @patch("agent_core.hooks.tools.handoff_writer.HandoffWriter")
    def test_delegates_to_handoff_writer_when_no_session_handoff(
        self, mock_hw_class, tmp_path: Path
    ):
        instance = MagicMock()
        instance.execute = MagicMock(
            return_value=ToolResult(
                heading="Handoff",
                content="Spawned handoff writer in background.",
            )
        )
        mock_hw_class.return_value = instance

        vault = tmp_path / "vault"
        transcript = tmp_path / "tr.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hey")])

        SessionEndWriter().execute(
            "SessionEnd",
            hook_input={"session_id": "new-s", "transcript_path": str(transcript)},
            params={
                "vault_path": str(vault),
                "handoff_file": "pepper/handoff.md",
                "timezone": "US/Eastern",
                "agent_name": "Pepper",
            },
        )

        assert instance.execute.called

    def test_duration_from_transcript_timestamps(self, tmp_path: Path):
        from agent_core.hooks.tools.session_end_writer import _duration_hint

        p = tmp_path / "x.jsonl"
        t0 = 1_700_000_000.0
        t1 = t0 + 125.0
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": t0, "message": {"role": "user", "content": "a"}}) + "\n")
            f.write(
                json.dumps({"timestamp": t1, "message": {"role": "assistant", "content": "b"}}) + "\n"
            )
        hint = _duration_hint(p)
        assert hint is not None
        assert "m" in hint
