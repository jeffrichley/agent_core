"""Tests for Envelope and payload models."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    CancellationPayload,
    EndpointInfo,
    Envelope,
    EventPayload,
    ProgressPayload,
    TextMessagePayload,
    ToolInvocationPayload,
)


def _now() -> datetime:
    return datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)


class TestEnvelopeRoundtrip:
    def test_text_message_roundtrip(self):
        env = Envelope(
            id="e1",
            correlation_id="c1",
            to="agent-pepper",
            kind="TextMessage",
            payload=TextMessagePayload(text="hello"),
            created_at=_now(),
        )
        data = env.model_dump(by_alias=True, mode="json")
        assert data["from"] == ""  # default; bus stamps later
        assert data["to"] == "agent-pepper"
        assert data["kind"] == "TextMessage"
        assert data["payload"]["kind"] == "TextMessage"
        assert data["payload"]["text"] == "hello"
        rebuilt = Envelope.model_validate(data)
        assert rebuilt.payload.text == "hello"

    def test_event_with_open_data(self):
        env = Envelope(
            id="e2",
            correlation_id="c2",
            to="events",
            kind="Event",
            payload=EventPayload(type="location", data={"lat": 38.9, "lon": -77.0}),
            created_at=_now(),
        )
        data = env.model_dump(by_alias=True, mode="json")
        assert data["payload"]["type"] == "location"
        assert data["payload"]["data"] == {"lat": 38.9, "lon": -77.0}
        rebuilt = Envelope.model_validate(data)
        assert rebuilt.payload.data["lat"] == 38.9

    def test_progress(self):
        env = Envelope(
            id="e3",
            correlation_id="c3",
            to="agent-pepper",
            kind="Progress",
            payload=ProgressPayload(status="working", percent=0.5),
            created_at=_now(),
        )
        assert env.payload.status == "working"
        assert env.payload.percent == 0.5

    def test_cancellation(self):
        env = Envelope(
            id="e4",
            correlation_id="c4",
            to="agent-deb",
            kind="Cancellation",
            payload=CancellationPayload(reason="user changed mind"),
            created_at=_now(),
        )
        assert env.payload.reason == "user changed mind"

    def test_tool_invocation(self):
        env = Envelope(
            id="e5",
            correlation_id="c5",
            to="scheduler",
            kind="ToolInvocation",
            payload=ToolInvocationPayload(tool="create_job", args={"name": "x"}),
            created_at=_now(),
        )
        assert env.payload.tool == "create_job"

    def test_acknowledgment(self):
        env = Envelope(
            id="e6",
            correlation_id="c6",
            to="agent-pepper",
            kind="Acknowledgment",
            payload=AcknowledgmentPayload(of="e5"),
            created_at=_now(),
        )
        assert env.payload.of == "e5"


class TestEnvelopeValidation:
    def test_kind_payload_must_match(self):
        # `kind: TextMessage` with an `EventPayload` must fail (discriminator mismatch).
        with pytest.raises(ValidationError):
            Envelope.model_validate(
                {
                    "id": "e1",
                    "correlation_id": "c1",
                    "from": "",
                    "to": "x",
                    "kind": "TextMessage",
                    "payload": {"kind": "Event", "type": "foo", "data": {}},
                    "created_at": _now().isoformat(),
                }
            )

    def test_from_alias(self):
        # JSON uses `from`, Python attribute is `from_`.
        env = Envelope(
            id="e1",
            correlation_id="c1",
            to="x",
            kind="TextMessage",
            payload=TextMessagePayload(text="hi"),
            created_at=_now(),
        )
        env.from_ = "agent-pepper"
        data = env.model_dump(by_alias=True, mode="json")
        assert data["from"] == "agent-pepper"
        assert "from_" not in data


class TestEndpointInfo:
    def test_construction(self):
        info = EndpointInfo(name="agent-deb", description="Research agent.")
        assert info.name == "agent-deb"
        assert info.description == "Research agent."

    def test_default_description(self):
        info = EndpointInfo(name="x")
        assert info.description == ""
