"""Tests for HandoffInjector — sidecar-aware handoff.md loader."""

import json
from pathlib import Path

import pytest

from agent_core.hooks.protocol import HookTool
from agent_core.hooks.tools.file_injector import FileInjector
from agent_core.hooks.tools.handoff_injector import HandoffInjector


class TestHandoffInjector:
    """Tests for HandoffInjector — gates handoff.md loads on a sidecar status file."""

    def test_implements_hook_tool_protocol(self):
        assert isinstance(HandoffInjector(), HookTool)

    def test_is_subclass_of_file_injector(self):
        assert issubclass(HandoffInjector, FileInjector)

    def test_handoff_pending_same_session_shows_banner_not_file(self, tmp_path: Path):
        base = tmp_path
        (base / "handoff.md").write_text("SECRET ON DISK", encoding="utf-8")
        status = {
            "schema_version": 1,
            "state": "pending",
            "session_id": "sid-a",
            "correlation_id": "c1",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "error": None,
            "content_sha256": None,
        }
        (base / "handoff-status.json").write_text(json.dumps(status), encoding="utf-8")
        tool = HandoffInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={"session_id": "sid-a"},
            params={"base_path": str(base), "files": ["handoff.md"]},
        )
        assert "pending" in result.content
        assert "SECRET ON DISK" not in result.content

    def test_handoff_pending_other_session_reads_file(self, tmp_path: Path):
        base = tmp_path
        (base / "handoff.md").write_text("GOOD CONTENT", encoding="utf-8")
        status = {
            "schema_version": 1,
            "state": "pending",
            "session_id": "old-session",
            "correlation_id": "c1",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "error": None,
            "content_sha256": None,
        }
        (base / "handoff-status.json").write_text(json.dumps(status), encoding="utf-8")
        tool = HandoffInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={"session_id": "new-session"},
            params={"base_path": str(base), "files": ["handoff.md"]},
        )
        assert "GOOD CONTENT" in result.content
        assert "Handoff status: pending" not in result.content

    def test_handoff_ready_loads_file(self, tmp_path: Path):
        base = tmp_path
        (base / "handoff.md").write_text("READY BODY", encoding="utf-8")
        status = {
            "schema_version": 1,
            "state": "ready",
            "session_id": "any",
            "correlation_id": "c1",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "error": None,
            "content_sha256": "abc",
        }
        (base / "handoff-status.json").write_text(json.dumps(status), encoding="utf-8")
        tool = HandoffInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={"session_id": "x"},
            params={"base_path": str(base), "files": ["handoff.md"]},
        )
        assert "READY BODY" in result.content

    def test_handoff_failed_includes_error_and_stale_body(self, tmp_path: Path):
        base = tmp_path
        (base / "handoff.md").write_text("OLD BODY", encoding="utf-8")
        status = {
            "schema_version": 1,
            "state": "failed",
            "session_id": "x",
            "correlation_id": "c1",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "error": "summarizer crashed",
            "content_sha256": None,
        }
        (base / "handoff-status.json").write_text(json.dumps(status), encoding="utf-8")
        tool = HandoffInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={"session_id": "x"},
            params={"base_path": str(base), "files": ["handoff.md"]},
        )
        assert "Handoff status: failed" in result.content
        assert "summarizer crashed" in result.content
        assert "OLD BODY" in result.content

    def test_no_status_file_falls_back_to_plain_read(self, tmp_path: Path):
        base = tmp_path
        (base / "handoff.md").write_text("PLAIN CONTENT", encoding="utf-8")
        tool = HandoffInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={"session_id": "x"},
            params={"base_path": str(base), "files": ["handoff.md"]},
        )
        assert "PLAIN CONTENT" in result.content

    def test_skip_handoff_status_gate_reads_file(self, tmp_path: Path):
        base = tmp_path
        (base / "handoff.md").write_text("BODY", encoding="utf-8")
        status = {
            "schema_version": 1,
            "state": "pending",
            "session_id": "same",
            "correlation_id": "c1",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "error": None,
            "content_sha256": None,
        }
        (base / "handoff-status.json").write_text(json.dumps(status), encoding="utf-8")
        tool = HandoffInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={"session_id": "same"},
            params={
                "base_path": str(base),
                "files": ["handoff.md"],
                "skip_handoff_status_gate": True,
            },
        )
        assert "BODY" in result.content
        assert "pending" not in result.content

    def test_rejects_non_handoff_filename(self, tmp_path: Path):
        """HandoffInjector must refuse files that are not named handoff.md."""
        base = tmp_path
        (base / "preferences.md").write_text("PREFS BODY", encoding="utf-8")
        status = {
            "schema_version": 1,
            "state": "pending",
            "session_id": "sid-a",
            "correlation_id": "c1",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "error": None,
            "content_sha256": None,
        }
        (base / "handoff-status.json").write_text(json.dumps(status), encoding="utf-8")
        tool = HandoffInjector()
        with pytest.raises(ValueError, match="only loads files named 'handoff.md'"):
            tool.execute(
                event="SessionStart",
                hook_input={"session_id": "sid-a"},
                params={"base_path": str(base), "files": ["preferences.md"]},
            )

    def test_rejects_non_handoff_filename_even_with_skip_gate(self, tmp_path: Path):
        """The basename contract is enforced even when sidecar gating is skipped."""
        base = tmp_path
        (base / "preferences.md").write_text("PREFS BODY", encoding="utf-8")
        tool = HandoffInjector()
        with pytest.raises(ValueError, match="only loads files named 'handoff.md'"):
            tool.execute(
                event="SessionStart",
                hook_input={},
                params={
                    "base_path": str(base),
                    "files": ["preferences.md"],
                    "skip_handoff_status_gate": True,
                },
            )

    def test_handoff_status_file_param_overrides_default_path(self, tmp_path: Path):
        base = tmp_path
        (base / "handoff.md").write_text("BODY", encoding="utf-8")
        status = {
            "schema_version": 1,
            "state": "pending",
            "session_id": "sid-a",
            "correlation_id": "c1",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "error": None,
            "content_sha256": None,
        }
        (base / "custom-status.json").write_text(json.dumps(status), encoding="utf-8")
        tool = HandoffInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={"session_id": "sid-a"},
            params={
                "base_path": str(base),
                "files": ["handoff.md"],
                "handoff_status_file": "custom-status.json",
            },
        )
        assert "pending" in result.content
        assert "BODY" not in result.content
