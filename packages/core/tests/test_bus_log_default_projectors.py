"""Default projectors: TextMessage + Fallback."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    Envelope,
    EventPayload,
    TextMessagePayload,
)
from agent_core.bus_log.projectors import (
    AcknowledgmentSkipProjector,
    HandoffFailedProjector,
    HandoffReadyProjector,
    SchedulerHeartbeatSkipProjector,
    TextMessageProjector,
    fallback_projector,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    reset_registry()
    yield
    reset_registry()


def _ts() -> datetime:
    # Fixed UTC instant: 2026-05-03 17:42:13 UTC == 13:42:13 EDT (US/Eastern is UTC-4 in May).
    return datetime(2026, 5, 3, 17, 42, 13, tzinfo=UTC)


def _text_env(*, frm: str = "discord", to: str = "pepper", text: str = "hi", metadata=None) -> Envelope:
    return Envelope(
        id="e1",
        correlation_id="c1",
        from_=frm,
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        urgency="yellow",
        metadata=metadata or {},
        created_at=_ts(),
    )


class TestTextMessageProjector:
    def test_dir_in_when_perspective_is_recipient(self):
        row = TextMessageProjector().render(_text_env(), perspective="pepper", timezone="US/Eastern")
        assert row is not None
        assert row["dir"] == "in"

    def test_dir_out_when_perspective_is_sender(self):
        row = TextMessageProjector().render(
            _text_env(frm="pepper", to="discord"),
            perspective="pepper",
            timezone="US/Eastern",
        )
        assert row is not None
        assert row["dir"] == "out"

    def test_dir_self_when_perspective_is_both(self):
        row = TextMessageProjector().render(
            _text_env(frm="pepper", to="pepper"),
            perspective="pepper",
            timezone="US/Eastern",
        )
        assert row is not None
        assert row["dir"] == "self"

    def test_ts_renders_in_requested_timezone(self):
        row = TextMessageProjector().render(_text_env(), perspective="pepper", timezone="US/Eastern")
        # 17:42:13 UTC -> 13:42:13 -04:00 in May (EDT).
        assert row["ts"] == "2026-05-03T13:42:13-04:00"

    def test_ts_renders_in_utc_when_requested(self):
        row = TextMessageProjector().render(_text_env(), perspective="pepper", timezone="UTC")
        assert row["ts"] == "2026-05-03T17:42:13+00:00"

    def test_sender_uses_metadata_display_name_when_present(self):
        row = TextMessageProjector().render(
            _text_env(metadata={"discord_user_display_name": "Jeff"}),
            perspective="pepper",
            timezone="US/Eastern",
        )
        assert row["sender"] == "Jeff"

    def test_sender_falls_back_to_envelope_from(self):
        row = TextMessageProjector().render(_text_env(), perspective="pepper", timezone="US/Eastern")
        assert row["sender"] == "discord"

    def test_src_is_envelope_from(self):
        row = TextMessageProjector().render(_text_env(), perspective="pepper", timezone="US/Eastern")
        assert row["src"] == "discord"

    def test_cid_is_correlation_id(self):
        row = TextMessageProjector().render(_text_env(), perspective="pepper", timezone="US/Eastern")
        assert row["cid"] == "c1"

    def test_content_is_payload_text(self):
        row = TextMessageProjector().render(
            _text_env(text="Did you see the report?"),
            perspective="pepper",
            timezone="US/Eastern",
        )
        assert row["content"] == "Did you see the report?"


class TestFallbackProjector:
    def test_renders_unknown_event_with_event_prefix(self):
        env = Envelope(
            id="x1",
            correlation_id="cx",
            from_="some-source",
            to="pepper",
            kind="Event",
            payload=EventPayload(type="UnknownThing", data={"k": "v"}),
            created_at=_ts(),
        )
        row = fallback_projector.render(env, perspective="pepper", timezone="US/Eastern")
        assert row is not None
        assert row["content"].startswith("event:UnknownThing")
        assert "k" in row["content"]
        assert row["dir"] == "in"
        assert row["src"] == "some-source"
        assert row["cid"] == "cx"

    def test_renders_non_event_envelope_with_kind_id_content(self):
        """Non-Event envelopes (e.g., Acknowledgment) take the else branch
        in _FallbackProjector.render and render content as ``{kind}:{id}``.
        Production envelope kinds beyond TextMessage/Event hit this path."""
        env = Envelope(
            id="ack-1",
            correlation_id="c-ack",
            from_="discord",
            to="pepper",
            kind="Acknowledgment",
            payload=AcknowledgmentPayload(of="other-id"),
            created_at=_ts(),
        )
        row = fallback_projector.render(env, perspective="pepper", timezone="UTC")
        assert row is not None
        assert row["content"] == "Acknowledgment:ack-1"
        assert row["dir"] == "in"
        assert row["src"] == "discord"

    def test_truncates_oversized_event_data_to_max_chars_with_ellipsis(self):
        """Event data that exceeds _MAX_DATA_CHARS (200) when JSON-serialized
        is truncated to exactly 200 chars including a trailing single-char
        ellipsis. Boundary lock-in: the truncation guarantee must hold so
        rotated daily files stay roughly bounded in line length."""
        big = {f"key{i}": "x" * 50 for i in range(20)}  # ~1200 chars after json.dumps
        env = Envelope(
            id="big-1",
            correlation_id="c-big",
            from_="src",
            to="pepper",
            kind="Event",
            payload=EventPayload(type="BigEvent", data=big),
            created_at=_ts(),
        )
        row = fallback_projector.render(env, perspective="pepper", timezone="UTC")
        prefix = "event:BigEvent data="
        assert row["content"].startswith(prefix)
        truncated = row["content"][len(prefix):]
        assert len(truncated) == 200
        assert truncated.endswith("…")


class TestHandoffReadyProjector:
    def test_renders_continuity_ready_with_path(self):
        env = Envelope(
            id="h1",
            correlation_id="c-h1",
            from_="handoff-jobs",
            to="pepper",
            kind="Event",
            payload=EventPayload(
                type="HandoffReady",
                data={
                    "job_id": "j-1",
                    "session_id": "s-1",
                    "handoff_path": "/x/handoff.md",
                    "content_sha256": "abc",
                },
            ),
            created_at=_ts(),
        )
        row = HandoffReadyProjector().render(env, perspective="pepper", timezone="US/Eastern")
        assert row is not None
        assert "continuity ready" in row["content"].lower()
        assert "/x/handoff.md" in row["content"]
        assert row["src"] == "handoff-jobs"
        assert row["dir"] == "in"


class TestHandoffFailedProjector:
    def test_renders_continuity_failed_with_error(self):
        env = Envelope(
            id="f1",
            correlation_id="c-f1",
            from_="handoff-jobs",
            to="pepper",
            kind="Event",
            payload=EventPayload(
                type="HandoffFailed",
                data={"job_id": "j-2", "error": "boom"},
            ),
            created_at=_ts(),
        )
        row = HandoffFailedProjector().render(env, perspective="pepper", timezone="US/Eastern")
        assert row is not None
        assert "continuity failed" in row["content"].lower()
        assert "boom" in row["content"]
        assert row["src"] == "handoff-jobs"


class TestAcknowledgmentSkipProjector:
    def test_returns_none(self):
        env = Envelope(
            id="ack-1",
            correlation_id="c-ack",
            from_="discord",
            to="pepper",
            kind="Acknowledgment",
            payload=AcknowledgmentPayload(of="other-id"),
            created_at=_ts(),
        )
        row = AcknowledgmentSkipProjector().render(env, perspective="pepper", timezone="US/Eastern")
        assert row is None


class TestSchedulerHeartbeatSkipProjector:
    def test_skips_when_metadata_marks_heartbeat(self):
        env = _text_env(metadata={"scheduler_job": "heartbeat"})
        row = SchedulerHeartbeatSkipProjector().render(env, perspective="pepper", timezone="US/Eastern")
        assert row is None

    def test_passes_through_non_heartbeat_text_messages(self):
        env = _text_env(metadata={"scheduler_job": "daily-briefing"})
        # Non-heartbeat scheduler jobs are not this projector's concern.
        # It should defer (return a sentinel? or fall through?). Per the
        # spec we want this projector to ONLY skip exact heartbeat jobs;
        # otherwise the TextMessage projector handles it. Implementation:
        # return None means "skip from summary". To delegate, this
        # projector should NOT be registered against TextMessage globally;
        # it's only registered when it would skip. So passing it a
        # non-heartbeat returns the same Tool 3 row a generic TextMessage
        # projector would produce, OR returns None and we register it
        # selectively. We pick: this projector returns None ONLY for
        # heartbeats, else falls through to TextMessageProjector logic.
        row = SchedulerHeartbeatSkipProjector().render(env, perspective="pepper", timezone="US/Eastern")
        assert row is not None
        assert row["content"] == "hi"  # the default _text_env text
