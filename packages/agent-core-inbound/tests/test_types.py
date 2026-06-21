"""Allow/Deny verdict types + Tier enum + ConnectorEvent base."""
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_core_inbound.types import Allow, ConnectorEvent, Deny, Tier


def _stamp() -> datetime:
    return datetime(2026, 6, 20, 22, 0, 0, tzinfo=UTC)


def test_tier_values():
    assert Tier.RED.value == "red"
    assert Tier.YELLOW.value == "yellow"
    assert Tier.GREEN.value == "green"


def test_allow_minimal_construction():
    a = Allow(tier=Tier.RED, reason="PR review requested on foreman")
    assert a.tier == Tier.RED
    assert a.reason == "PR review requested on foreman"


def test_allow_requires_reason():
    with pytest.raises(ValidationError):
        Allow(tier=Tier.YELLOW, reason="")  # empty reason rejected


def test_deny_has_no_body():
    d = Deny()
    # Deny carries no data — it is intentionally empty so audit-log
    # writers don't accidentally serialize an event payload alongside
    # a "denied" verdict (privacy + storage).
    assert d.model_dump() == {}


def test_connector_event_id_and_landed_at_required():
    e = ConnectorEvent(
        event_id="github-12345-67890",
        landed_at=_stamp(),
        raw={"action": "review_requested"},
    )
    assert e.event_id == "github-12345-67890"
    assert e.landed_at == _stamp()
    assert e.raw["action"] == "review_requested"


def test_connector_event_id_must_be_non_empty():
    with pytest.raises(ValidationError):
        ConnectorEvent(event_id="", landed_at=_stamp(), raw={})
