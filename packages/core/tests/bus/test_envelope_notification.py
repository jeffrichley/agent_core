"""Notification envelope kind: round-trip + validation.

Notification is the bus envelope shape that the inbound-notifications router
publishes when an external event is classified Allow by a per-source
connector. See docs/superpowers/specs/2026-06-20-inbound-notifications-design.md.
"""
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_core.bus.envelope import (
    BUILTIN_KINDS,
    Envelope,
    NotificationPayload,
)


def _stamp() -> datetime:
    return datetime(2026, 6, 20, 22, 0, 0, tzinfo=UTC)


def test_notification_kind_in_builtin_kinds():
    assert "Notification" in BUILTIN_KINDS


def test_notification_payload_minimal_round_trip():
    payload = NotificationPayload(
        source="github",
        reason="PR review requested on foreman",
        landed_at=_stamp(),
        body={"pr_number": 387, "repo": "jeffrichley/foreman"},
    )
    assert payload.kind == "Notification"
    assert payload.source == "github"
    assert payload.poll_discovered_at is None


def test_notification_envelope_round_trip():
    env = Envelope(
        id="env-1",
        correlation_id="corr-1",
        to="wren",
        kind="Notification",
        payload=NotificationPayload(
            source="github",
            reason="PR review requested on foreman",
            landed_at=_stamp(),
            body={"pr_number": 387},
        ),
        urgency="red",
        created_at=_stamp(),
    )
    dumped = env.model_dump()
    rebuilt = Envelope.model_validate(dumped)
    assert rebuilt.kind == "Notification"
    assert rebuilt.payload.source == "github"  # type: ignore[union-attr]
    assert rebuilt.urgency == "red"


def test_notification_payload_rejects_unknown_source_no_validation():
    # source is a free-form string at the bus layer; per-source validation
    # is the connector's job. The bus only enforces presence + non-empty.
    payload = NotificationPayload(
        source="gmail",
        reason="email from a known correspondent",
        landed_at=_stamp(),
        body={},
    )
    assert payload.source == "gmail"


def test_notification_payload_requires_reason():
    with pytest.raises(ValidationError):
        NotificationPayload(  # type: ignore[call-arg]
            source="github",
            landed_at=_stamp(),
            body={},
        )


def test_notification_payload_with_poll_discovered_at():
    landed = _stamp()
    discovered = datetime(2026, 6, 20, 22, 0, 30, tzinfo=UTC)
    payload = NotificationPayload(
        source="gmail",
        reason="email from a known correspondent",
        landed_at=landed,
        poll_discovered_at=discovered,
        body={"message_id": "abc"},
    )
    assert payload.poll_discovered_at == discovered


def test_envelope_kind_mismatch_rejected_for_notification():
    with pytest.raises(ValidationError, match="does not match payload kind"):
        Envelope(
            id="env-1",
            correlation_id="corr-1",
            to="wren",
            kind="TextMessage",  # mismatched
            payload=NotificationPayload(
                source="github",
                reason="x",
                landed_at=_stamp(),
                body={},
            ),
            created_at=_stamp(),
        )
