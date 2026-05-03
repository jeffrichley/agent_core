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

    def test_handoff_pending_other_session_shows_continuity_placeholder(
        self, tmp_path: Path
    ):
        """Cutover #02 (b): a fresh session must not silently load stale handoff
        content while the prior session's continuity is still summarizing."""
        base = tmp_path
        (base / "handoff.md").write_text("STALE ON DISK", encoding="utf-8")
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
        assert "STALE ON DISK" not in result.content
        assert "Continuity not ready yet" in result.content
        assert "MEMORY.md" in result.content
        assert "HandoffReady" in result.content

    def test_handoff_pending_no_hook_session_shows_placeholder(self, tmp_path: Path):
        """No `session_id` in hook_input → cannot prove same-session, treat as
        cross-session. The placeholder must protect against silent stale read."""
        base = tmp_path
        (base / "handoff.md").write_text("DO NOT LEAK", encoding="utf-8")
        status = {
            "schema_version": 1,
            "state": "pending",
            "session_id": "any-sid",
            "correlation_id": "c1",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "error": None,
            "content_sha256": None,
        }
        (base / "handoff-status.json").write_text(json.dumps(status), encoding="utf-8")
        tool = HandoffInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={"base_path": str(base), "files": ["handoff.md"]},
        )
        assert "DO NOT LEAK" not in result.content
        assert "Continuity not ready yet" in result.content

    def test_handoff_failed_includes_guidance_and_stale_body_label(
        self, tmp_path: Path
    ):
        """Cutover #02 (c): failed-state placeholder must direct the agent at
        MEMORY.md / dailies as ground truth, and label the on-disk file as a
        possibly-stale prior attempt rather than a silent success."""
        base = tmp_path
        (base / "handoff.md").write_text("LAST ATTEMPT SNIPPET", encoding="utf-8")
        status = {
            "schema_version": 1,
            "state": "failed",
            "session_id": "sid-x",
            "correlation_id": "c1",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "error": "worker exploded",
            "content_sha256": None,
        }
        (base / "handoff-status.json").write_text(json.dumps(status), encoding="utf-8")
        tool = HandoffInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={"session_id": "sid-x"},
            params={"base_path": str(base), "files": ["handoff.md"]},
        )
        assert "Handoff status: failed" in result.content
        assert "worker exploded" in result.content
        assert "MEMORY.md" in result.content
        assert "ground truth" in result.content
        assert "last-known-good" in result.content
        assert "LAST ATTEMPT SNIPPET" in result.content

    def test_handoff_failed_with_no_on_disk_body_still_shows_guidance(
        self, tmp_path: Path
    ):
        """Failed-state placeholder must include guidance even when handoff.md
        is absent from disk (no last-known-good attribution applies)."""
        base = tmp_path
        status = {
            "schema_version": 1,
            "state": "failed",
            "session_id": "sid-x",
            "correlation_id": "c1",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "error": "summarizer crashed",
            "content_sha256": None,
        }
        (base / "handoff-status.json").write_text(json.dumps(status), encoding="utf-8")
        tool = HandoffInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={"session_id": "sid-x"},
            params={"base_path": str(base), "files": ["handoff.md"]},
        )
        assert "Handoff status: failed" in result.content
        assert "summarizer crashed" in result.content
        assert "MEMORY.md" in result.content
        assert "ground truth" in result.content
        # No on-disk body, so the last-known-good attribution must not be claimed.
        assert "last-known-good" not in result.content.lower()

    def test_handoff_ready_missing_file_warn_emits_placeholder(self, tmp_path: Path):
        """Status says ready but handoff.md is gone from disk → warn placeholder."""
        base = tmp_path
        status = {
            "schema_version": 1,
            "state": "ready",
            "session_id": "x",
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
            params={
                "base_path": str(base),
                "files": ["handoff.md"],
                "missing_file_behavior": "warn",
            },
        )
        assert "ready" in result.content
        assert "missing on disk" in result.content

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
