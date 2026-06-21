"""Connector Protocol — structural typing check."""
from datetime import UTC, datetime

from agent_core_inbound.protocol import Connector
from agent_core_inbound.types import Allow, ConnectorEvent, Deny, Tier


class _StubConnector:
    """Minimal Connector implementation for structural-typing check."""

    name = "stub"

    def classify(self, event: ConnectorEvent, target_being: str) -> Allow | Deny:
        if target_being == "wren":
            return Allow(tier=Tier.GREEN, reason="stub allow for wren")
        return Deny()


def test_stub_satisfies_connector_protocol():
    # Protocol satisfaction is structural; isinstance(x, Connector)
    # requires Connector to be marked @runtime_checkable.
    c: Connector = _StubConnector()
    assert isinstance(c, Connector)
    assert c.name == "stub"


def test_stub_classify_returns_allow_for_wren():
    c = _StubConnector()
    e = ConnectorEvent(
        event_id="evt-1",
        landed_at=datetime(2026, 6, 20, tzinfo=UTC),
        raw={},
    )
    verdict = c.classify(e, "wren")
    assert isinstance(verdict, Allow)
    assert verdict.tier == Tier.GREEN


def test_stub_classify_returns_deny_for_other():
    c = _StubConnector()
    e = ConnectorEvent(
        event_id="evt-2",
        landed_at=datetime(2026, 6, 20, tzinfo=UTC),
        raw={},
    )
    verdict = c.classify(e, "pepper")
    assert isinstance(verdict, Deny)
