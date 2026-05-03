"""Projector registry: lookup, override, and isolation between tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
from agent_core.bus_log.projectors import (
    Projector,
    fallback_projector,
    get_projector,
    register_projector,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test starts with an empty registry; restore after."""
    reset_registry()
    yield
    reset_registry()


def _text_env(eid: str = "e1") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="src",
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime.now(UTC),
    )


def _event_env(eid: str, *, event_type: str) -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="src",
        to="agent",
        kind="Event",
        payload=EventPayload(type=event_type, data={}),
        created_at=datetime.now(UTC),
    )


class _Stub:
    def __init__(self, sentinel: dict | None) -> None:
        self.sentinel = sentinel
        self.calls: list[tuple[str, str, str]] = []

    def render(self, envelope, *, perspective, timezone):
        self.calls.append((envelope.id, perspective, timezone))
        return self.sentinel


def test_register_and_lookup_by_event_type():
    p = _Stub({"k": "v"})
    register_projector("MyEvent", p)
    found = get_projector(_event_env("e1", event_type="MyEvent"))
    assert found is p


def test_lookup_by_envelope_kind_when_not_event():
    p = _Stub({"k": "TM"})
    register_projector("TextMessage", p)
    found = get_projector(_text_env())
    assert found is p


def test_event_type_lookup_takes_priority_over_kind():
    by_type = _Stub({"src": "type"})
    by_kind = _Stub({"src": "kind"})
    register_projector("MyEvent", by_type)
    register_projector("Event", by_kind)
    found = get_projector(_event_env("e1", event_type="MyEvent"))
    assert found is by_type


def test_fallback_used_when_no_projector_registered():
    found = get_projector(_event_env("e1", event_type="UnknownEvent"))
    assert found is fallback_projector


def test_register_replaces_existing():
    first = _Stub({"a": 1})
    second = _Stub({"b": 2})
    register_projector("MyEvent", first)
    register_projector("MyEvent", second)
    found = get_projector(_event_env("e1", event_type="MyEvent"))
    assert found is second


def test_protocol_runtime_check():
    assert isinstance(_Stub(None), Projector)
