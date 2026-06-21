"""Tests for the envelope-extension hookspec — Envelope.kind opens to str.

Covers the schema-side changes from
docs/superpowers/specs/2026-05-25-envelope-extension-hookspec-design.md §2.1:
- Envelope.kind is open str
- payload accepts EnvelopePayload (typed) | dict[str, Any] (plugin)
- validate_kind_matches_payload handles both shapes
- BUILTIN_KINDS exposes the typed-payload set
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_core.bus.envelope import (
    BUILTIN_KINDS,
    Envelope,
    TextMessagePayload,
)


def _now() -> datetime:
    return datetime.now(UTC)


class TestBuiltinKindsConstant:
    def test_exposes_seven_built_in_kinds(self) -> None:
        assert BUILTIN_KINDS == frozenset(
            {
                "TextMessage",
                "Event",
                "ToolInvocation",
                "Cancellation",
                "Progress",
                "Acknowledgment",
                "Notification",
            }
        )

    def test_is_immutable(self) -> None:
        # frozenset means callers cannot mutate the registry inadvertently.
        with pytest.raises(AttributeError):
            BUILTIN_KINDS.add("Desire")  # type: ignore[attr-defined]


class TestPluginKindWithDictPayload:
    """Envelope accepts plugin-registered kinds as open strings."""

    def test_unknown_kind_with_matching_dict_payload_validates(self) -> None:
        env = Envelope(
            id="x",
            correlation_id="c",
            to="pepper",
            kind="Desire",
            payload={
                "kind": "Desire",
                "text": "past-me wanted this",
                "desire_created_at": "2026-05-25T20:00:00Z",
            },
            created_at=_now(),
        )
        assert env.kind == "Desire"
        assert isinstance(env.payload, dict)
        assert env.payload["text"] == "past-me wanted this"

    def test_unknown_kind_with_mismatched_dict_payload_raises(self) -> None:
        with pytest.raises(ValidationError):
            Envelope(
                id="x",
                correlation_id="c",
                to="pepper",
                kind="Desire",
                payload={"kind": "Thought", "text": "wrong"},
                created_at=_now(),
            )

    def test_unknown_kind_with_payload_missing_kind_key_raises(self) -> None:
        with pytest.raises(ValidationError):
            Envelope(
                id="x",
                correlation_id="c",
                to="pepper",
                kind="Desire",
                payload={"text": "no kind key"},
                created_at=_now(),
            )


class TestBuiltinKindsStillStrict:
    """Backward-compat invariant: built-in kinds keep strict typed validation."""

    def test_text_message_with_typed_payload_works(self) -> None:
        env = Envelope(
            id="x",
            correlation_id="c",
            to="pepper",
            kind="TextMessage",
            payload=TextMessagePayload(text="hi"),
            created_at=_now(),
        )
        assert env.kind == "TextMessage"
        assert env.payload.text == "hi"  # type: ignore[union-attr]

    def test_text_message_with_dict_payload_works(self) -> None:
        # Dict payload for built-in kind also validates via the discriminated
        # union (Pydantic dispatches by `kind` discriminator before falling
        # through to dict).
        env = Envelope(
            id="x",
            correlation_id="c",
            to="pepper",
            kind="TextMessage",
            payload={"kind": "TextMessage", "text": "hi"},
            created_at=_now(),
        )
        assert env.kind == "TextMessage"
        # Pydantic coerces matching dict to typed payload via discriminator.
        assert env.payload.text == "hi"  # type: ignore[union-attr]

    def test_text_message_with_mismatched_payload_kind_raises(self) -> None:
        with pytest.raises(ValidationError):
            Envelope(
                id="x",
                correlation_id="c",
                to="pepper",
                kind="TextMessage",
                payload={"kind": "Event", "type": "wrong", "data": {}},
                created_at=_now(),
            )

    def test_text_message_with_missing_required_typed_field_raises(self) -> None:
        # TextMessagePayload requires `text`; absence still rejected.
        with pytest.raises(ValidationError):
            Envelope(
                id="x",
                correlation_id="c",
                to="pepper",
                kind="TextMessage",
                payload={"kind": "TextMessage"},  # no text
                created_at=_now(),
            )
