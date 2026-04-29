"""Tests for the urgency field on Envelope."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_core.bus.envelope import Envelope, TextMessagePayload


def _make_envelope(**overrides):
    base = dict(
        id="e1",
        correlation_id="c1",
        from_="src",
        to="dst",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return Envelope(**base)


def test_envelope_urgency_defaults_to_green():
    env = _make_envelope()
    assert env.urgency == "green"


def test_envelope_urgency_accepts_yellow_and_red():
    assert _make_envelope(urgency="yellow").urgency == "yellow"
    assert _make_envelope(urgency="red").urgency == "red"


def test_envelope_urgency_rejects_unknown_value():
    with pytest.raises(ValidationError):
        _make_envelope(urgency="blue")


def test_envelope_urgency_rejects_empty_string():
    with pytest.raises(ValidationError):
        _make_envelope(urgency="")


def test_envelope_urgency_roundtrips_via_json():
    env = _make_envelope(urgency="red")
    payload = env.model_dump_json()
    restored = Envelope.model_validate_json(payload)
    assert restored.urgency == "red"


def test_envelope_urgency_default_when_absent_from_input_json():
    """Old persisted rows won't carry the field — Pydantic must use the default."""
    raw = {
        "id": "e1",
        "correlation_id": "c1",
        "from": "src",
        "to": "dst",
        "kind": "TextMessage",
        "payload": {"kind": "TextMessage", "text": "hi"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    env = Envelope.model_validate(raw)
    assert env.urgency == "green"
