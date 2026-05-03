"""Default projectors: TextMessage + Fallback."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
from agent_core.bus_log.projectors import (
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
